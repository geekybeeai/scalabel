"""View B: every edited frame across all fix rounds, with before/after crops.

For a merged round the "after" side shows what will ship: the label as it is in
OUT/work/<stem>.json after Roshan's review. A change is flagged "changed in
review" when that differs from the tool's proposal and "reverted" when it equals
the label before the round; labels added or deleted during review are listed
per frame. (The working copy holds every merged round, so a later round's edit
of the same label shows up here too.)

Crops are rendered on demand, at most two at a time, all crops of one frame
from a single image decode; ``prerender`` renders everything up front.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlencode

import cv2
import numpy as np

from annotation_qa.data import image_path, load_export
from annotation_qa.geometry import _dist_to_polyline, change_anchor, dense
from annotation_qa.imaging import FrameImage
from annotation_qa.render import issue_crop
from annotation_qa.rounds import merged_tags, round_files  # noqa: F401 - re-exported

log = logging.getLogger(__name__)
SCALABEL = "http://localhost:8686"
TASK_SIZE = 50  # the review-project creator's task size
RENDER_SLOTS = threading.Semaphore(2)  # concurrent crop renders (images are up to ~190 MP)
SAME_TOL = 0.05  # px: Scalabel's export may reformat numbers

_cache_lock = threading.Lock()
_cache: dict[tuple[str, Path], tuple[int, object]] = {}
_frame_locks: dict[tuple[str, str], threading.Lock] = {}


def _cached(kind: str, path: Path, load):
    """``load(path)``, cached by (path, mtime)."""
    path = Path(path)
    mt = path.stat().st_mtime_ns
    with _cache_lock:
        hit = _cache.get((kind, path))
        if hit and hit[0] == mt:
            return hit[1]
    val = load(path)
    with _cache_lock:
        _cache[(kind, path)] = (mt, val)
    return val


def frames_by_name(path: Path) -> dict[str, tuple[int, dict]]:
    """name -> (index, frame) of an export, cached by (path, mtime)."""
    return _cached("frames", path, lambda p: {
        f["name"]: (i, f) for i, f in reversed(list(enumerate(load_export(p).frames)))})


def _changes(path: Path) -> dict:
    return _cached("json", path, lambda p: json.loads(p.read_text()))


def _snapshot(label: dict | None) -> dict | None:
    return None if label is None else {"category": label.get("category"),
                                       "poly2d": label.get("poly2d") or []}


def _same(a: dict | None, b: dict | None) -> bool:
    """Same category and geometry (vertices within SAME_TOL px)."""
    if a is None or b is None:
        return a is b
    if a.get("category") != b.get("category"):
        return False
    pa, pb = a.get("poly2d") or [], b.get("poly2d") or []
    if len(pa) != len(pb):
        return False
    for x, y in zip(pa, pb):
        vx, vy = x.get("vertices") or [], y.get("vertices") or []
        if (len(vx) != len(vy) or bool(x.get("closed")) != bool(y.get("closed"))
                or (x.get("types") or "L" * len(vx)) != (y.get("types") or "L" * len(vy))):
            return False
        if vx and not np.allclose(np.asarray(vx, float), np.asarray(vy, float), atol=SAME_TOL):
            return False
    return True


def _moved(before: dict, after: dict) -> float | None:
    """Largest vertex displacement in px, or None when the vertex structure differs."""
    bp, ap = before.get("poly2d") or [], after.get("poly2d") or []
    if len(bp) != len(ap):
        return None
    worst = 0.0
    for b, a in zip(bp, ap):
        if len(b["vertices"]) != len(a["vertices"]):
            return None
        d = np.asarray(a["vertices"], float) - np.asarray(b["vertices"], float)
        if d.size:
            worst = max(worst, float(np.hypot(d[:, 0], d[:, 1]).max()))
    return worst


def _moved_curve(before: dict, after: dict) -> float | None:
    """Largest distance (px) between the drawn curves, for polys whose vertices changed."""
    bp, ap = before.get("poly2d") or [], after.get("poly2d") or []
    if len(bp) != len(ap) or not bp:
        return None
    worst = 0.0
    for b, a in zip(bp, ap):
        db, da = np.asarray(dense(b), float), np.asarray(dense(a), float)
        if len(db) < 2 or len(da) < 2:
            return None
        worst = max(worst, float(_dist_to_polyline(da, db).max()),
                    float(_dist_to_polyline(db, da).max()))
    return worst


def describe(before: dict | None, after: dict | None) -> str:
    if after is None:
        return "deleted"
    if before is None:
        return "added"
    parts = []
    if before["category"] != after["category"]:
        parts.append(f"{before['category']} → {after['category']}")
    n_b = sum(len(p["vertices"]) for p in before.get("poly2d") or [])
    n_a = sum(len(p["vertices"]) for p in after.get("poly2d") or [])
    mv = _moved(before, after)
    if mv is None:
        far = _moved_curve(before, after)
        if far is not None and far >= 0.05:
            parts.append(f"moved up to {far:.1f}".removesuffix(".0") + " px")
        if n_b != n_a:
            parts.append(f"vertices {n_b} → {n_a}")
    elif mv >= 0.05:
        parts.append(f"moved {mv:.1f}".removesuffix(".0") + " px")
    return " + ".join(parts) or "no visible change"


VERDICT_RANK = {"bad": 0, "check": 1, "unverified": 1, "ok": 2}


def _verdicts(out: Path, tag: str) -> dict:
    p = Path(out) / "rounds" / f"{tag}_verify.json"
    try:
        return _changes(p)
    except (OSError, ValueError):
        return {}


def _finish_row(rw: dict) -> None:
    """Row verdict = worst change verdict; a draw-by-hand frame counts as check."""
    vs = [c["verdict"] for c in rw["changes"]] + (["check"] if rw["hand"] else [])
    rw["verdict"] = min(vs, key=VERDICT_RANK.get) if vs else "check"
    rw["all_ok"] = bool(rw["changes"]) and all(v == "ok" for v in vs) and not rw["review_edits"]


def select_rows(rows: list[dict], tag: str = "", show: str = "needs") -> dict:
    """View B's filter and order. ``show``: all | needs (check+bad) | bad.

    Rows are limited to a round when ``tag`` is given and ordered bad, check, ok, then by
    project item order (rows without a round keep their incoming order within a verdict).
    Counts are per change verdict over the round's rows, before the show filter."""
    def idx(r):
        p = next((p for p in r["projects"] if p["project"] == tag), None)
        return p["index"] if p else 10 ** 9
    rows = [r for r in rows if not tag or any(p["project"] == tag for p in r["projects"])]
    counts = {"ok": 0, "check": 0, "bad": 0, "unverified": 0}
    for r in rows:
        for c in r["changes"]:
            if not tag or c["tag"] == tag:
                counts[c["verdict"]] += 1
    keep = {"all": 2, "needs": 1, "bad": 0}.get(show, 1)
    rows = [r for r in rows if VERDICT_RANK[r["verdict"]] <= keep]
    rows = sorted(rows, key=lambda r: (VERDICT_RANK[r["verdict"]], idx(r) if tag else 0))
    return {"rows": rows, "counts": counts}


def _crop_name(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _project_link(repo_root: Path, tag: str, index: int) -> dict:
    """Deep link to frame ``index`` (0-based, items-file order) of a review project."""
    size = 0
    try:
        size = int(json.loads((Path(repo_root) / "local-data/scalabel/projects" / tag
                               / "project.json").read_text())["config"]["taskSize"])
    except Exception:  # noqa: BLE001
        pass
    if size <= 0:
        size = TASK_SIZE
    task, item = index // size, index % size + 1
    return {"link": f"{SCALABEL}/label?project_name={quote(tag, safe='')}&task_index={task}&item={item}",
            "index": index, "task_index": task, "item": item}


def _work_frames(out: Path, stem: str) -> dict[str, tuple[int, dict]]:
    p = Path(out) / "work" / f"{stem}.json"
    try:
        return frames_by_name(p)
    except (OSError, ValueError) as e:
        log.warning("cannot read %s: %s", p, e)
        return {}


def _labels(frame: dict | None) -> list[dict]:
    return (frame or {}).get("labels") or []


def build_rows(out: Path, repo_root: Path) -> list[dict]:
    """One row per (export_stem, frame_name), most changes first."""
    out = Path(out)
    rows: dict[tuple[str, str], dict] = {}

    def row(stem: str, name: str) -> dict:
        return rows.setdefault((stem, name), {
            "export_stem": stem, "frame_name": name, "changes": [], "hand": [], "projects": [],
            "review_edits": []})

    merged = merged_tags(out)
    for r in round_files(out):
        try:
            changes = _changes(r["changes"])
            items = frames_by_name(r["items"])
        except (OSError, ValueError) as e:
            log.warning("skipping round %s: %s", r["tag"], e)
            continue
        is_merged = (r["stem"], r["tag"]) in merged
        cur = _work_frames(out, r["stem"]) if is_merged else {}
        verd = _verdicts(out, r["tag"])
        for fname, labels in changes.items():
            rw = row(r["stem"], fname)
            shipped = {l["id"]: l for l in _labels(cur.get(fname, (0, None))[1])}
            for lid, rec in labels.items():
                q = urlencode({"stem": r["stem"], "frame": fname, "label": lid, "round": r["tag"]})
                vd = verd.get(fname, {}).get(lid) or {}
                c = {"round": r["round"], "tag": r["tag"], "issue_type": r["type"], "label_id": lid,
                     "verdict": vd.get("verdict", "unverified"),
                     "reasons": vd.get("reasons") or ["not verified yet (run: python3 -m "
                                                      "annotation_qa verify --round <tag>)"],
                     "what": describe(rec.get("before"), rec.get("after")),
                     "anchor": rec.get("anchor") or change_anchor(rec.get("before"), rec.get("after")),
                     "merged": is_merged, "review": "", "shipped": "",
                     "before_crop": f"/viewb/crop?{q}&side=before",
                     "after_crop": f"/viewb/crop?{q}&side=after"}
                if is_merged and fname in cur:
                    now = _snapshot(shipped.get(lid))
                    if not _same(now, rec.get("after")):
                        c["review"] = ("reverted" if _same(now, rec.get("before"))
                                       else "changed in review")
                        c["shipped"] = describe(rec.get("before"), now)
                rw["changes"].append(c)
            if fname in items:
                pl = _project_link(repo_root, r["tag"], items[fname][0])
                rw["projects"].append({"round": r["round"], "project": r["tag"], **pl})
                for c in rw["changes"]:
                    if c["round"] == r["round"] and "link" not in c:
                        c["link"] = pl["link"] + "&label=" + quote(str(c["label_id"]), safe="")
                        if c.get("anchor"):
                            c["link"] += f"&x={c['anchor'][0]}&y={c['anchor'][1]}"
        for fname, (idx, frame) in items.items():
            pre = _labels(frame)
            if is_merged and fname in cur:
                proposed = {l["id"] for l in pre}
                now = {l["id"] for l in _labels(cur[fname][1])}
                if proposed != now:
                    row(r["stem"], fname)["review_edits"].append({
                        "round": r["round"], "added": sorted(now - proposed),
                        "deleted": sorted(proposed - now)})
            if fname in changes:
                continue
            rw = row(r["stem"], fname)
            done = _labels(_work_frames(out, r["stem"]).get(fname, (0, frame))[1]) != pre
            rw["hand"].append({"round": r["round"], "done": done})
            rw["projects"].append({"round": r["round"], "project": r["tag"],
                                   **_project_link(repo_root, r["tag"], idx)})
    for rw in rows.values():
        _finish_row(rw)
    return sorted(rows.values(),
                  key=lambda r: (-len(r["changes"]), r["export_stem"], r["frame_name"]))


def frame_specs(out: Path, stem: str, frame_name: str) -> dict[tuple[str, str, str], dict]:
    """(round tag, label id, side) -> {"frame", "issue", "dest"} for every change of a frame."""
    out = Path(out)
    merged = merged_tags(out)
    specs: dict[tuple[str, str, str], dict] = {}
    for r in round_files(out):
        if r["stem"] != stem:
            continue
        try:
            changes = _changes(r["changes"]).get(frame_name)
            item = frames_by_name(r["items"]).get(frame_name) if changes else None
        except (OSError, ValueError):
            continue
        if not changes or item is None:
            continue
        proposed = item[1]
        shipped = None
        if (stem, r["tag"]) in merged:
            shipped = _work_frames(out, stem).get(frame_name, (0, None))[1]
        for lid, rec in changes.items():
            ref = rec.get("before") or rec.get("after")
            pts = [p for poly in (ref or {}).get("poly2d") or [] for p in dense(poly)]
            if not pts:
                continue
            anchor = tuple(float(v) for v in np.mean(np.asarray(pts, float), axis=0))
            for side in ("before", "after"):
                if side == "after" and shipped is not None:
                    base, labels = shipped, list(_labels(shipped))  # what will ship
                else:
                    base = proposed
                    labels = [l for l in _labels(proposed) if l["id"] != lid]
                    if rec.get(side) is not None:
                        labels.append({"id": lid, **rec[side]})
                frame = {"name": frame_name, "url": base["url"], "labels": labels}
                key = json.dumps([frame["url"], labels, anchor, lid], sort_keys=True)
                specs[(r["tag"], lid, side)] = {
                    "frame": frame,
                    "issue": SimpleNamespace(anchor=anchor, label_id=lid, related_label_ids=[]),
                    "dest": out / "viewb" / f"{_crop_name(key)}.jpg"}
    return specs


def _frame_lock(stem: str, frame_name: str) -> threading.Lock:
    with _cache_lock:
        return _frame_locks.setdefault((stem, frame_name), threading.Lock())


def render_frame(out: Path, repo_root: Path, stem: str, frame_name: str,
                 specs: dict | None = None) -> int:
    """Render every missing crop of one frame from a single image decode; return how many."""
    specs = frame_specs(out, stem, frame_name) if specs is None else specs
    with _frame_lock(stem, frame_name):
        todo = [s for s in specs.values() if not s["dest"].exists()]
        if not todo:
            return 0
        with RENDER_SLOTS:
            root = Path(repo_root) / "local-data"
            n = 0
            imgs: dict[str, FrameImage] = {}
            try:
                for s in todo:
                    url = s["frame"]["url"]
                    img = imgs.setdefault(url, FrameImage(image_path(s["frame"], root),
                                                          Path(out) / "roi_cache"))
                    try:
                        crop = issue_crop(img, s["frame"], s["issue"])
                    except FileNotFoundError:
                        continue
                    s["dest"].parent.mkdir(parents=True, exist_ok=True)
                    tmp = s["dest"].with_name(s["dest"].stem + ".tmp.jpg")
                    if cv2.imwrite(str(tmp), crop):
                        tmp.replace(s["dest"])
                        n += 1
            finally:
                for img in imgs.values():
                    img.release()
    return n


def render_crop(out: Path, repo_root: Path, stem: str, frame_name: str, label_id: str,
                tag: str, side: str) -> Path | None:
    """OUT/viewb/<hash>.jpg for one change (rendered with its frame if missing), or None."""
    if side not in ("before", "after"):
        return None
    specs = frame_specs(out, stem, frame_name)
    spec = specs.get((tag, label_id, side))
    if spec is None:
        return None
    if not spec["dest"].exists():
        render_frame(out, repo_root, stem, frame_name, specs)
    return spec["dest"] if spec["dest"].exists() else None


def prerender(out: Path, repo_root: Path) -> int:
    """Render every View B crop now; return the number rendered."""
    rows = [r for r in build_rows(out, repo_root) if r["changes"]]
    total = 0
    for k, r in enumerate(rows, 1):
        n = render_frame(out, repo_root, r["export_stem"], r["frame_name"])
        total += n
        print(f"[{k}/{len(rows)}] {r['export_stem']}|{r['frame_name']}: {n} crops", flush=True)
    return total


VIEWB_JS = """
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const edits=r=>r.review_edits.map(e=>'<div><span class="badge">edited in review</span> '+esc(e.round)+': '
 +(e.added.length?'added '+e.added.map(esc).join(', '):'')+(e.added.length&&e.deleted.length?'; ':'')
 +(e.deleted.length?'deleted '+e.deleted.map(esc).join(', '):'')+'</div>').join('');
const flag=c=>c.review?` <span class="badge">${esc(c.review)}</span>`+(c.shipped?' ships as: '+esc(c.shipped):''):'';
const VB={ok:['✓ ok','#2a7','#e6f6ee'],check:['⚠ check','#b80','#fff4d6'],unverified:['⚠ not verified','#b80','#fff4d6'],bad:['✗ likely wrong','#c33','#fde8e8']};
const vbadge=c=>{const v=VB[c.verdict]||VB.check,rs=(c.reasons||[]).map(esc).join('; ');
 return `<span class="vb" style="color:${v[1]};background:${v[2]};border:1px solid ${v[1]};border-radius:3px;padding:0 5px;font-weight:600" title="${rs}">${v[0]}</span><div class="why" style="font-size:11px;color:#666;max-width:260px">${(c.reasons||[]).map(esc).join('<br>')}</div>`;};
const card=r=>r.all_ok&&!r._open?`<div class="card collapsed" data-i="${r._i}"><div class="meta"><span class="vb" style="color:#2a7">✓ all changes verified</span> &mdash; ${esc(r.frame_name)} (${r.changes.length}) &mdash; <a href="#" class="expand" data-i="${r._i}">open anyway</a>${r.projects.length?' &middot; <a class="open" href="'+esc(r.projects[0].link)+'" target="_blank">Scalabel</a>':''}</div></div>`:`<div class="card" data-i="${r._i}"><div class="meta"><div><b>${esc(r.export_stem)}</b></div>
 <div>${esc(r.frame_name)} &middot; ${r.changes.length} changes</div>
 <div>${r.projects.map(p=>`<a href="${esc(p.link)}" target="_blank">${esc(p.project)} (task ${p.task_index}, frame ${p.item})</a>`).join('<br>')}</div>
 ${r.hand.length?'<div><span class="badge">draw by hand</span> '+r.hand.map(h=>esc(h.round)+': '+(h.done?'done':'not done')).join(', ')+'</div>':''}
 ${edits(r)}
 <div class="chgs">${r.changes.map(c=>`<div class="chg"><div class="txt">${esc(c.round)}${c.merged?'':' <span class="badge">not merged yet</span>'}<br>label ${esc(c.label_id)}<br>${c.link?`<a class="open" href="${esc(c.link)}" target="_blank">Open in Scalabel</a>`:''}<br>${vbadge(c)}<br>${esc(c.what)}${flag(c)}</div>
 <figure><figcaption>before</figcaption><a href="${esc(c.before_crop)}" target="_blank"><img loading="lazy" src="${esc(c.before_crop)}" title="before"></a></figure><figure><figcaption>after</figcaption><a href="${esc(c.after_crop)}" target="_blank"><img loading="lazy" src="${esc(c.after_crop)}" title="${c.merged?'after review (ships)':'after (proposed)'}"></a></figure></div>`).join('')}</div>
 </div></div>`;
let ROWS=[],cur=-1;
async function show(){const t=$('#round').value,f=$('#show').value;
 const d=await (await fetch('/viewb/view?round='+encodeURIComponent(t)+'&show='+encodeURIComponent(f))).json();
 ROWS=d.rows;ROWS.forEach((r,i)=>r._i=i);const c=d.counts;
 $('#n').textContent=ROWS.length+' edited frames'+(t?' (bad, check, ok; then Scalabel item order)':'')
  +' | changes: ✓ '+c.ok+' ok, ⚠ '+(c.check+c.unverified)+' check, ✗ '+c.bad+' bad';
 $('#rows').innerHTML=ROWS.map(card).join('');cur=-1;}
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('a.expand');if(!a)return;e.preventDefault();
 const r=ROWS[+a.dataset.i];r._open=true;const el=a.closest('.card');el.outerHTML=card(r);});
function go(d){const cs=[...document.querySelectorAll('#rows .card')];if(!cs.length)return;
 cur=Math.max(0,Math.min(cs.length-1,cur+d));
 cs.forEach((c,i)=>c.style.outline=i===cur?'3px solid #38f':'');
 cs[cur].scrollIntoView({block:'start',behavior:'smooth'});}
document.addEventListener('keydown',e=>{
 const t=e.target&&e.target.tagName;if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT'||(e.target&&e.target.isContentEditable)||e.ctrlKey||e.metaKey||e.altKey)return;
 const k=e.key.toLowerCase();
 if(k==='j')go(1);else if(k==='k')go(-1);
 else if(k==='o'){const c=document.querySelectorAll('#rows .card')[cur];if(!c)return;
  const a=c.querySelector('a.open')||c.querySelector('.meta a');if(a)window.open(a.href,'_blank');}});
(async()=>{const all=await (await fetch('/viewb/rows')).json();
 const tags=[...new Set(all.flatMap(r=>r.projects.map(p=>p.project)))].sort();
 $('#round').innerHTML='<option value="">all rounds</option>'+tags.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('');
 $('#round').onchange=show;$('#show').onchange=show;show();})();
"""


def viewb_html(css: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Annotation QA View B</title>
<style>{css}
.chgs{{display:block}}
.chg{{display:flex;gap:10px;align-items:flex-start;padding:4px 8px;border-top:1px solid #eee}}
.txt{{flex:0 0 220px;width:220px;min-width:0;overflow-wrap:anywhere;font-size:13px}}
.chg figure{{flex:1 1 0;min-width:0;max-width:calc((100% - 240px)/2);margin:0}}
.chg figcaption{{font-size:11px;color:#666}}
#rows .card{{width:100%;box-sizing:border-box}}
#rows .meta{{max-width:none;flex:1 1 auto;min-width:0;width:100%}}
.chg img{{display:block;width:100%;max-width:100%;max-height:none;height:auto;object-fit:contain}}
@media (max-width:999px){{.chg{{flex-direction:column}}.txt{{flex:none;width:auto}}.chg figure{{max-width:none;width:100%}}}}
</style></head><body>
<header><a href="/">&larr; View A</a> &nbsp; <b>View B: final review</b> &nbsp; <select id="round"></select>
 show: <select id="show"><option value="all">all</option><option value="needs" selected>needs a look (check+bad)</option><option value="bad">bad only</option></select> <span id="n"></span>
&nbsp; (each change: before left, after right; merged rounds show what will ship; J/K next/prev frame, O open in Scalabel)</header><div id="rows"></div>
<script>{VIEWB_JS}</script></body></html>"""
