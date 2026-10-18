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
from annotation_qa.progress import is_reviewed
from annotation_qa.reverts import read_reverts
from annotation_qa.render import RENDER_VERSION, issue_crop
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


_CLASS_WORD = {"curb_road_edge": "CURB", "without_curb_road_edge": "SOFT EDGE"}


def _word(cat: str) -> str:
    return _CLASS_WORD.get(cat) or cat.replace("_", " ").upper()


def split_pieces(after) -> list[dict] | None:
    """The piece snapshots of a split change ("after" is a list), else None."""
    return after if isinstance(after, list) else None


def _main(after):
    """The label that keeps the id: the first piece of a split, else ``after`` itself."""
    return after[0] if isinstance(after, list) else after


def _split_text(before: dict, pieces: list[dict]) -> str:
    words = " | ".join(_word(p["category"]) if p["category"] != before["category"]
                       else _word(p["category"]).lower() for p in pieces)
    return f"{_word(before['category']).lower()} → split in {len(pieces)}: {words}"


def class_change(before: dict | None, after: dict | None) -> str:
    """"soft edge → CURB" style text when the category changed, else "". A split reads
    "curb → split in 2: SOFT EDGE | curb" (changed classes in capitals)."""
    pieces = split_pieces(after)
    if before and pieces:
        return _split_text(before, pieces)
    if not before or not after or before["category"] == after["category"]:
        return ""
    return f"{_word(before['category']).lower()} → {_word(after['category'])}"


def ops_text(before: dict | None, after: dict | None) -> str:
    """Combined ops of a change, e.g. "soft edge → CURB + moved 6 px" (class change first);
    "" unless the category changed (a plain move is already in ``describe``)."""
    cc = class_change(before, after)
    if not cc:
        return ""
    if split_pieces(after):
        return cc
    rest = [p for p in describe(before, after).split(" + ")
            if p != f"{before['category']} → {after['category']}"]
    return " + ".join([cc] + rest)


def describe(before: dict | None, after: dict | None) -> str:
    if after is None:
        return "deleted"
    if before is None:
        return "added"
    pieces = split_pieces(after)
    if pieces:
        return f"split into {len(pieces)} pieces (" + ", ".join(
            f"{p.get('id')}: {p['category']}" for p in pieces) + ")"
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
    live = [c for c in rw["changes"] if not c["reverted"]]  # a reverted change is "no change"
    vs = [c["verdict"] for c in live] + (["check"] if rw["hand"] else [])
    rw["verdict"] = min(vs, key=VERDICT_RANK.get) if vs else ("ok" if rw["changes"] else "check")
    rw["all_ok"] = bool(rw["changes"]) and all(v == "ok" for v in vs) and not rw["review_edits"]


def select_rows(rows: list[dict], tag: str = "", show: str = "needs",
                progress: dict | None = None) -> dict:
    """View B's filter and order. ``show``: all | needs (check+bad) | bad | unreviewed.

    Rows are limited to a round when ``tag`` is given and ordered bad, check, ok, then by
    project item order (rows without a round keep their incoming order within a verdict).
    ``unreviewed`` is "needs a look" minus reviewed frames. Counts are per change verdict over
    the round's rows, before the show filter.

    Every row gets ``n`` (1-based position) and ``reviewed``; positions and the totals
    (``total``, ``reviewed``) are over the verdict-filtered list, so they stay stable while
    the "unreviewed" filter hides frames. ``resume`` is the list index of the first
    unreviewed row, or -1. Merged rounds count as reviewed (read-only: ``locked``)."""
    progress = progress or {}

    def idx(r):
        p = next((p for p in r["projects"] if p["project"] == tag), None)
        return p["index"] if p else 10 ** 9
    rows = [r for r in rows if not tag or any(p["project"] == tag for p in r["projects"])]
    counts = {"ok": 0, "check": 0, "bad": 0, "unverified": 0}
    for r in rows:
        for c in r["changes"]:
            if (not tag or c["tag"] == tag) and not c["reverted"]:
                counts[c["verdict"]] += 1
    keep = {"all": 2, "needs": 1, "bad": 0, "unreviewed": 1}.get(show, 1)
    rows = [r for r in rows if VERDICT_RANK[r["verdict"]] <= keep]
    rows = sorted(rows, key=lambda r: (VERDICT_RANK[r["verdict"]], idx(r) if tag else 0))
    for n, r in enumerate(rows, 1):
        ps = [p for p in r["projects"] if not tag or p["project"] == tag]
        r["n"] = n
        r["locked"] = bool(ps) and all(p.get("merged") for p in ps)
        r["reviewed"] = bool(ps) and all(
            p.get("merged") or is_reviewed(progress, p["project"], r["frame_name"]) for p in ps)
        r["scalabel"] = ps[0] if ps else None
    total = len(rows)
    done = sum(1 for r in rows if r["reviewed"])
    first = next((i for i, r in enumerate(rows) if not r["reviewed"] and not r["locked"]), -1)
    if show == "unreviewed":
        rows = [r for r in rows if not r["reviewed"]]
        first = 0 if rows else -1
    return {"rows": rows, "counts": counts, "total": total, "reviewed": done, "resume": first}


def _crop_name(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _project_link(repo_root: Path, tag: str, index: int, n_items: int = 0) -> dict:
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
            "index": index, "task_index": task, "item": item,
            "n_tasks": max(1, -(-n_items // size)) if n_items else 1}


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
    reverts = read_reverts(out)
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
                c = {"round": r["round"], "tag": r["tag"], "issue_type": r["type"],
                     "issue_types": rec.get("issue_types") or [rec.get("issue_type") or r["type"]],
                     "ops": rec.get("ops") or [], "label_id": lid,
                     "verdict": vd.get("verdict", "unverified"),
                     "reasons": vd.get("reasons") or ["not verified yet (run: python3 -m "
                                                      "annotation_qa verify --round <tag>)"],
                     "class_change": class_change(rec.get("before"), rec.get("after")),
                     "ops_text": ops_text(rec.get("before"), rec.get("after")),
                     "what": describe(rec.get("before"), rec.get("after")),
                     "anchor": rec.get("anchor") or change_anchor(rec.get("before"), _main(rec.get("after"))),
                     "merged": is_merged, "review": "", "shipped": "",
                     "reverted": False, "revert_state": "", "round_tag": r["tag"], "frame_name": fname,
                     "before_crop": f"/viewb/crop?{q}&side=before",
                     "after_crop": f"/viewb/crop?{q}&side=after"}
                if is_merged and fname in cur:
                    now = _snapshot(shipped.get(lid))
                    pieces = split_pieces(rec.get("after"))
                    if pieces:  # a split ships when every piece is still there, unchanged
                        rest = [_snapshot(shipped.get(p.get("id"))) for p in pieces[1:]]
                        if not (_same(now, pieces[0]) and all(
                                _same(n, p) for n, p in zip(rest, pieces[1:]))):
                            c["review"] = ("reverted" if _same(now, rec.get("before"))
                                           and not any(rest) else "changed in review")
                            c["shipped"] = describe(rec.get("before"), now)
                    elif not _same(now, rec.get("after")):
                        c["review"] = ("reverted" if _same(now, rec.get("before"))
                                       else "changed in review")
                        c["shipped"] = describe(rec.get("before"), now)
                if str(lid) in ((reverts.get(r["tag"]) or {}).get(fname) or {}):
                    c["reverted"] = True
                    c["revert_state"] = ("pending" if not is_merged else
                                         {"reverted": "applied", "changed in review": "conflict"}
                                         .get(c["review"], "not_applied"))
                rw["changes"].append(c)
            if fname in items:
                pl = _project_link(repo_root, r["tag"], items[fname][0], len(items))
                rw["projects"].append({"round": r["round"], "project": r["tag"],
                                       "merged": is_merged, **pl})
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
            rw["projects"].append({"round": r["round"], "project": r["tag"], "merged": is_merged,
                                   **_project_link(repo_root, r["tag"], idx, len(items))})
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
            pieces = split_pieces(rec.get("after"))
            if pieces:
                specs.update(_split_specs(out, r["tag"], frame_name, lid, rec, pieces,
                                          proposed, shipped))
                continue
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
                key = json.dumps([RENDER_VERSION, frame["url"], labels, anchor, lid],
                                 sort_keys=True)
                b_, a_ = rec.get("before"), rec.get("after")
                cat = (rec.get(side) or {}).get("category")
                hl = cat if (b_ and a_ and b_["category"] != a_["category"]) else None
                specs[(r["tag"], lid, side)] = {
                    "frame": frame,
                    "issue": SimpleNamespace(
                        anchor=anchor, label_id=lid, related_label_ids=[], highlight_category=hl,
                        panel_tag=f"{side}: {cat}" if hl else None),
                    "dest": out / "viewb" / f"{_crop_name(key)}.jpg"}
    return specs


def _split_specs(out: Path, tag: str, frame_name: str, lid: str, rec: dict, pieces: list[dict],
                 proposed: dict, shipped: dict | None) -> dict:
    """Crop specs of a split: before = the original line in its class colour; after = every
    piece in its class colour (red curb / green soft) with white cut markers. Centred on the
    first cut."""
    before = rec.get("before") or {}
    ids = {str(p.get("id")) for p in pieces} | {str(lid)}
    cuts = [list(map(float, c)) for c in rec.get("cut_points") or []]
    anchor = tuple(float(v) for v in (rec.get("anchor") or (cuts[0] if cuts else [0, 0])))
    specs = {}
    for side in ("before", "after"):
        if side == "after" and shipped is not None:
            base, labels = shipped, list(_labels(shipped))
        else:
            base = proposed
            labels = [l for l in _labels(proposed) if str(l["id"]) not in ids]
            if side == "before":
                labels.append({"id": lid, **before})
            else:
                labels += [dict(p) for p in pieces]
        if side == "before":
            hl, hls, marks = before.get("category"), {}, []
            tag_txt = f"before: {before.get('category')}"
        else:
            present = {str(l["id"]): l.get("category") for l in labels}
            hls = {str(p.get("id")): present[str(p.get("id"))] for p in pieces[1:]
                   if str(p.get("id")) in present}
            hl, marks = present.get(str(lid)), cuts
            tag_txt = f"after: split in {len(pieces)}"
        frame = {"name": frame_name, "url": base["url"], "labels": labels}
        key = json.dumps([RENDER_VERSION, "split", frame["url"], labels, anchor, lid, marks],
                         sort_keys=True)
        specs[(tag, lid, side)] = {
            "frame": frame,
            "issue": SimpleNamespace(anchor=anchor, label_id=lid, related_label_ids=[],
                                     highlight_category=hl, highlights=hls, markers=marks,
                                     panel_tag=tag_txt),
            "dest": Path(out) / "viewb" / f"{_crop_name(key)}.jpg"}
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
const REVT={pending:'REVERTED \u2014 will keep the original',applied:'reverted (kept original)',conflict:'revert not applied (Scalabel edit wins)',not_applied:'revert not applied'};
const revui=(c,i,j)=>(c.reverted?`<div><span class="badge revbadge" style="background:#888;color:#fff">${esc(REVT[c.revert_state]||REVT.pending)}</span></div>`:'')
 +(c.merged?'':`<button class="rv" data-i="${i}" data-j="${j}">${c.reverted?'Undo revert':'Revert'}</button>`);
const flag=c=>c.review?` <span class="badge">${esc(c.review)}</span>`+(c.shipped?' ships as: '+esc(c.shipped):''):'';
const VB={ok:['✓ ok','#2a7','#e6f6ee'],check:['⚠ check','#b80','#fff4d6'],unverified:['⚠ not verified','#b80','#fff4d6'],bad:['✗ likely wrong','#c33','#fde8e8']};
const vbadge=c=>{const v=VB[c.verdict]||VB.check,rs=(c.reasons||[]).map(esc).join('; ');
 return `<span class="vb" style="color:${v[1]};background:${v[2]};border:1px solid ${v[1]};border-radius:3px;padding:0 5px;font-weight:600" title="${rs}">${v[0]}</span><div class="why" style="font-size:11px;color:#666;max-width:260px">${(c.reasons||[]).map(esc).join('<br>')}</div>`;};
let TOTAL=0,NREV=0;
const sfr=r=>{const p=r.scalabel;return p?(p.n_tasks>1?'task '+p.task_index+', frame '+p.item:'frame '+p.item):'';};
const where=r=>{const a=r.projects.length?'<a class="open" href="'+esc((r.scalabel||r.projects[0]).link)+'" target="_blank">Open in Scalabel</a>':'';
 return `<div class="where"><span class="pos">Image ${r.n} of ${TOTAL}</span>${r.scalabel?' <span class="sfr">Scalabel '+esc(sfr(r))+'</span>':''} ${a}
 ${r.reviewed?'<span class="rbadge">✓ reviewed</span>':''} <label class="rvw"><input type="checkbox" class="rchk" data-i="${r._i}"${r.reviewed?' checked':''}${r.locked?' disabled':''}> reviewed${r.locked?' (merged)':''}</label></div>`;};
const card=r=>r.all_ok&&!r._open?`<div class="card collapsed" data-i="${r._i}"><div class="meta">${where(r)}<span class="vb" style="color:#2a7">✓ all changes verified</span> &mdash; ${esc(r.frame_name)} (${r.changes.length}) &mdash; <a href="#" class="expand" data-i="${r._i}">open anyway</a>${r.projects.length?' &middot; <a class="open" href="'+esc(r.projects[0].link)+'" target="_blank">Scalabel</a>':''}</div></div>`:`<div class="card" data-i="${r._i}"><div class="meta">${where(r)}<div><b>${esc(r.export_stem)}</b></div>
 <div>${esc(r.frame_name)} &middot; ${r.changes.length} changes</div>
 <div>${r.projects.map(p=>`<a href="${esc(p.link)}" target="_blank">${esc(p.project)} (task ${p.task_index}, frame ${p.item})</a>`).join('<br>')}</div>
 ${r.hand.length?'<div><span class="badge">draw by hand</span> '+r.hand.map(h=>esc(h.round)+': '+(h.done?'done':'not done')).join(', ')+'</div>':''}
 ${edits(r)}
 <div class="chgs">${r.changes.map((c,j)=>`<div class="chg${c.reverted?' rev':''}" tabindex="0" data-j="${j}"><div class="txt">${esc(c.round)}${c.merged?'':' <span class="badge">not merged yet</span>'}<br>label ${esc(c.label_id)}<br>${c.link?`<a class="open" href="${esc(c.link)}" target="_blank">Open in Scalabel</a>`:''}<br>${vbadge(c)}<br>${c.class_change?`<b style="font-size:15px">${esc(c.ops_text||c.class_change)}</b><br>`:''}${esc(c.what)}${flag(c)}<br>${revui(c,r._i,j)}</div>
 <figure${c.reverted?' class="revfig"':''}><figcaption>before${c.reverted?' (kept)':''}</figcaption><a href="${esc(c.before_crop)}" target="_blank"><img loading="lazy" src="${esc(c.before_crop)}" title="before"></a></figure><figure><figcaption>after</figcaption><a href="${esc(c.after_crop)}" target="_blank"><img loading="lazy" src="${esc(c.after_crop)}" title="${c.merged?'after review (ships)':'after (proposed)'}"></a></figure></div>`).join('')}</div>
 </div></div>`;
let ROWS=[],cur=-1;
async function show(){const t=$('#round').value,f=$('#show').value;
 const d=await (await fetch('/viewb/view?round='+encodeURIComponent(t)+'&show='+encodeURIComponent(f))).json();
 ROWS=d.rows;ROWS.forEach((r,i)=>r._i=i);const c=d.counts;TOTAL=d.total;NREV=d.reviewed;
 $('#n').textContent=ROWS.length+' edited frames'+(t?' (bad, check, ok; then Scalabel item order)':'')
  +' | changes: ✓ '+c.ok+' ok, ⚠ '+(c.check+c.unverified)+' check, ✗ '+c.bad+' bad';
 $('#rows').innerHTML=ROWS.map(card).join('');cur=-1;nrev();bar();}
function bar(){$('#bround').textContent=$('#round').value||'all rounds';
 $('#bprog').textContent='Reviewed '+NREV+' / '+TOTAL+' images';
 const pb=$('#pbar');pb.max=Math.max(1,TOTAL);pb.value=NREV;}
function rerender(i){const r=ROWS[i];const el=document.querySelector('#rows .card[data-i="'+i+'"]');if(!el)return;
 const had=el.style.outline;el.outerHTML=card(r);const n=document.querySelector('#rows .card[data-i="'+i+'"]');if(n)n.style.outline=had;}
async function setReviewed(i,want){const r=ROWS[i];if(!r||r.locked||r.reviewed===want)return true;
 const sel=$('#round').value;
 const tags=r.projects.filter(p=>!p.merged&&(!sel||p.project===sel)).map(p=>p.project);
 try{for(const t of tags){const x=await fetch('/viewb/progress',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({round:t,frame:r.frame_name,reviewed:want})});if(!x.ok)throw new Error(await x.text());}}
 catch(e){alert('progress not saved: '+e.message);rerender(i);return false;}
 r.reviewed=want;NREV+=want?1:-1;rerender(i);bar();return true;}
function resume(){const r=ROWS.find(r=>!r.reviewed&&!r.locked);if(!r){alert('Every image is reviewed.');return;}
 const cs=[...document.querySelectorAll('#rows .card')];const k=cs.findIndex(c=>+c.dataset.i===r._i);if(k<0)return;
 cur=k;cs.forEach((c,i)=>c.style.outline=i===cur?'3px solid #38f':'');cs[k].scrollIntoView({block:'start',behavior:'smooth'});}
document.addEventListener('change',e=>{const b=e.target;if(!b.classList||!b.classList.contains('rchk'))return;
 const i=+b.dataset.i,want=b.checked;b.blur();setReviewed(i,want);});
document.addEventListener('click',e=>{if(e.target.id==='resume')resume();});
function nrev(){const n=ROWS.reduce((a,r)=>a+r.changes.filter(c=>c.reverted).length,0);$('#nrev').textContent=n?n+' reverted':'';}
async function toggleRev(i,j){const r=ROWS[i],c=r&&r.changes[j];if(!c||c.merged)return;
 const want=!c.reverted;let d;
 try{const x=await fetch('/viewb/revert',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({round:c.tag,frame:r.frame_name,label:String(c.label_id),revert:want})});
  if(!x.ok)throw new Error(await x.text());d=await x.json();}catch(e){alert('revert failed: '+e.message);return;}
 c.reverted=!!d.reverted;c.revert_state=c.reverted?'pending':'';r._open=true;
 const el=document.querySelector('#rows .card[data-i="'+i+'"]');if(el){const had=el.style.outline;el.outerHTML=card(r);
  const n=document.querySelector('#rows .card[data-i="'+i+'"]');if(n)n.style.outline=had;}nrev();}
document.addEventListener('click',e=>{const b=e.target.closest&&e.target.closest('button.rv');if(b)toggleRev(+b.dataset.i,+b.dataset.j);});
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('a.expand');if(!a)return;e.preventDefault();
 const r=ROWS[+a.dataset.i];r._open=true;const el=a.closest('.card');el.outerHTML=card(r);});
function go(d){const cs=[...document.querySelectorAll('#rows .card')];if(!cs.length)return;
 cur=Math.max(0,Math.min(cs.length-1,cur+d));
 cs.forEach((c,i)=>c.style.outline=i===cur?'3px solid #38f':'');
 cs[cur].scrollIntoView({block:'start',behavior:'smooth'});}
document.addEventListener('keydown',e=>{
 const t=e.target&&e.target.tagName;if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT'||(e.target&&e.target.isContentEditable)||e.ctrlKey||e.metaKey||e.altKey)return;
 const k=e.key.toLowerCase();
 if(k==='j'){const cd=document.querySelectorAll('#rows .card')[cur];
  if(cd)setReviewed(+cd.dataset.i,true);go(1);}else if(k==='u')resume();else if(k==='k')go(-1);
 else if(k==='x'){const f=document.activeElement&&document.activeElement.closest&&document.activeElement.closest('.chg');
  const cd=f?f.closest('.card'):document.querySelectorAll('#rows .card')[cur];if(!cd)return;
  toggleRev(+cd.dataset.i,f?+f.dataset.j:0);}
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
.chg.rev{{background:#f1f1f1}}.chg.rev .revfig img{{outline:4px solid #888;opacity:.9}}
.chg:focus{{outline:2px solid #38f}}
#bar{{position:sticky;top:0;z-index:20;background:#fff;border-bottom:2px solid #ccc;padding:6px 10px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}}
#bar progress{{width:240px;height:14px}}#bround{{font-weight:700}}#bprog{{font-weight:600}}
#rows .card{{scroll-margin-top:56px}}
.where{{display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:20px;margin-bottom:6px}}
.where .pos{{font-weight:700}}.where .sfr{{font-weight:700;color:#246}}.where a.open{{font-size:16px}}
.where .rvw{{font-size:14px}}.rbadge{{background:#e6f6ee;color:#2a7;border:1px solid #2a7;border-radius:3px;padding:0 6px;font-size:15px;font-weight:600}}
button.rv{{margin-top:4px}}
.chg figcaption{{font-size:11px;color:#666}}
#rows .card{{width:100%;box-sizing:border-box}}
#rows .meta{{max-width:none;flex:1 1 auto;min-width:0;width:100%}}
.chg img{{display:block;width:100%;max-width:100%;max-height:none;height:auto;object-fit:contain}}
@media (max-width:999px){{.chg{{flex-direction:column}}.txt{{flex:none;width:auto}}.chg figure{{max-width:none;width:100%}}}}
</style></head><body>
<header><a href="/">&larr; View A</a> &nbsp; <b>View B: final review</b> &nbsp; <select id="round"></select>
 show: <select id="show"><option value="all">all</option><option value="needs" selected>needs a look (check+bad)</option><option value="bad">bad only</option><option value="unreviewed">not yet reviewed</option></select> <span id="n"></span> <b id="nrev" style="color:#666"></b>
&nbsp; (each change: before left, after right; merged rounds show what will ship; J mark reviewed + next, K prev, U resume at first unreviewed, O open in Scalabel, X revert/undo a change)</header>
<div id="bar"><span>Round: <span id="bround"></span></span><span id="bprog"></span><progress id="pbar" value="0" max="1"></progress>
 <button id="resume" type="button" title="U">Resume (U)</button><span style="font-size:12px;color:#666">J = mark reviewed and next</span></div><div id="rows"></div>
<script>{VIEWB_JS}</script></body></html>"""
