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
from urllib.parse import urlencode

import cv2
import numpy as np

from annotation_qa.data import image_path, load_export
from annotation_qa.geometry import _dist_to_polyline, dense
from annotation_qa.imaging import FrameImage
from annotation_qa.render import issue_crop
from annotation_qa.rounds import merged_tags, round_files  # noqa: F401 - re-exported

log = logging.getLogger(__name__)
SCALABEL = "http://localhost:8686"
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


def _crop_name(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _project_link(repo_root: Path, tag: str, index: int) -> str:
    size = 0
    try:
        size = int(json.loads((Path(repo_root) / "local-data/scalabel/projects" / tag
                               / "project.json").read_text())["config"]["taskSize"])
    except Exception:  # noqa: BLE001
        pass
    task = index // size if size > 0 else 0
    return f"{SCALABEL}/label?project_name={tag}&task_index={task}"


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
        for fname, labels in changes.items():
            rw = row(r["stem"], fname)
            shipped = {l["id"]: l for l in _labels(cur.get(fname, (0, None))[1])}
            for lid, rec in labels.items():
                q = urlencode({"stem": r["stem"], "frame": fname, "label": lid, "round": r["tag"]})
                c = {"round": r["round"], "issue_type": r["type"], "label_id": lid,
                     "what": describe(rec.get("before"), rec.get("after")),
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
                rw["projects"].append({"round": r["round"], "project": r["tag"],
                                       "link": _project_link(repo_root, r["tag"], items[fname][0])})
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
                                   "link": _project_link(repo_root, r["tag"], idx)})
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
(async()=>{const rows=await (await fetch('/viewb/rows')).json();
 $('#n').textContent=rows.length+' edited frames';
 $('#rows').innerHTML=rows.map(r=>`<div class="card"><div class="meta"><div><b>${esc(r.export_stem)}</b></div>
 <div>${esc(r.frame_name)} &middot; ${r.changes.length} changes</div>
 <div>${r.projects.map(p=>`<a href="${esc(p.link)}" target="_blank">${esc(p.project)}</a>`).join('<br>')}</div>
 ${r.hand.length?'<div><span class="badge">draw by hand</span> '+r.hand.map(h=>esc(h.round)+': '+(h.done?'done':'not done')).join(', ')+'</div>':''}
 ${edits(r)}
 <table>${r.changes.map(c=>`<tr><td>${esc(c.round)}${c.merged?'':' <span class="badge">not merged yet</span>'}</td><td>label ${esc(c.label_id)}</td><td>${esc(c.what)}${flag(c)}</td>
 <td><img loading="lazy" src="${esc(c.before_crop)}" title="before"></td><td><img loading="lazy" src="${esc(c.after_crop)}" title="${c.merged?'after review (ships)':'after (proposed)'}"></td></tr>`).join('')}</table>
 </div></div>`).join('');})();
"""


def viewb_html(css: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Annotation QA View B</title>
<style>{css}table img{{max-width:420px}}td{{padding:4px 8px;vertical-align:top}}</style></head><body>
<header><a href="/">&larr; View A</a> &nbsp; <b>View B: final review</b> &nbsp; <span id="n"></span>
&nbsp; (each change: before left, after right; merged rounds show what will ship)</header><div id="rows"></div>
<script>{VIEWB_JS}</script></body></html>"""
