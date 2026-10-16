"""View B: every edited frame across all fix rounds, with before/after crops."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import cv2
import numpy as np

from annotation_qa.data import image_path, load_export
from annotation_qa.geometry import dense
from annotation_qa.imaging import FrameImage
from annotation_qa.render import issue_crop

log = logging.getLogger(__name__)
SCALABEL = "http://localhost:8686"
_CHANGES = re.compile(r"^(?P<stem>.+?)_fix_(?P<type>.+)_v(?P<v>\d+)_changes\.json$")


def round_files(out: Path) -> list[dict]:
    """One dict per fix round found in OUT/rounds, ordered by version, type, stem."""
    rounds = []
    for p in (Path(out) / "rounds").glob("*_changes.json"):
        m = _CHANGES.match(p.name)
        if m:
            tag = p.name[: -len("_changes.json")]
            rounds.append({"stem": m["stem"], "type": m["type"], "v": int(m["v"]), "tag": tag,
                           "round": f"{m['type']}_v{m['v']}",
                           "changes": p, "items": p.with_name(f"{tag}_items.json")})
    return sorted(rounds, key=lambda r: (r["v"], r["type"], r["stem"]))


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


def build_rows(out: Path, repo_root: Path) -> list[dict]:
    """One row per (export_stem, frame_name), most changes first."""
    out = Path(out)
    rows: dict[tuple[str, str], dict] = {}

    def row(stem: str, name: str) -> dict:
        return rows.setdefault((stem, name), {
            "export_stem": stem, "frame_name": name, "changes": [], "hand": [], "projects": []})

    work_labels: dict[str, dict[str, list]] = {}

    def current(stem: str) -> dict[str, list]:
        if stem not in work_labels:
            p = out / "work" / f"{stem}.json"
            try:
                work_labels[stem] = {f["name"]: f.get("labels") or [] for f in load_export(p).frames}
            except (OSError, ValueError) as e:
                log.warning("cannot read %s: %s", p, e)
                work_labels[stem] = {}
        return work_labels[stem]

    for r in round_files(out):
        try:
            changes = json.loads(r["changes"].read_text())
            items = {f["name"]: (i, f.get("labels") or [])
                     for i, f in enumerate(load_export(r["items"]).frames)}
        except (OSError, ValueError) as e:
            log.warning("skipping round %s: %s", r["tag"], e)
            continue
        for fname, labels in changes.items():
            rw = row(r["stem"], fname)
            for lid, rec in labels.items():
                q = urlencode({"stem": r["stem"], "frame": fname, "label": lid, "round": r["tag"]})
                rw["changes"].append({
                    "round": r["round"], "issue_type": r["type"], "label_id": lid,
                    "what": describe(rec.get("before"), rec.get("after")),
                    "before_crop": f"/viewb/crop?{q}&side=before",
                    "after_crop": f"/viewb/crop?{q}&side=after"})
            if fname in items:
                rw["projects"].append({"round": r["round"], "project": r["tag"],
                                       "link": _project_link(repo_root, r["tag"], items[fname][0])})
        for fname, (idx, pre) in items.items():
            if fname in changes:
                continue
            rw = row(r["stem"], fname)
            done = current(r["stem"]).get(fname, pre) != pre
            rw["hand"].append({"round": r["round"], "done": done})
            rw["projects"].append({"round": r["round"], "project": r["tag"],
                                   "link": _project_link(repo_root, r["tag"], idx)})
    return sorted(rows.values(),
                  key=lambda r: (-len(r["changes"]), r["export_stem"], r["frame_name"]))


def render_crop(out: Path, repo_root: Path, stem: str, frame_name: str, label_id: str,
                tag: str, side: str) -> Path | None:
    """Render (or reuse) OUT/viewb/<hash>.jpg for one change; None if it does not exist."""
    out = Path(out)
    if side not in ("before", "after"):
        return None
    rnd = next((r for r in round_files(out) if r["tag"] == tag and r["stem"] == stem), None)
    if rnd is None:
        return None
    try:
        rec = json.loads(rnd["changes"].read_text())[frame_name][label_id]
        after_frame = next(f for f in load_export(out / "work" / f"{tag}.json").frames
                           if f["name"] == frame_name)
    except (OSError, ValueError, KeyError, StopIteration):
        return None
    dest = out / "viewb" / f"{_crop_name(tag, frame_name, label_id, side)}.jpg"
    if dest.exists():
        return dest
    snap = rec.get(side)
    ref = rec.get("before") or rec.get("after")
    pts = [p for poly in (ref or {}).get("poly2d") or [] for p in dense(poly)]
    if not pts:
        return None
    anchor = tuple(np.mean(np.asarray(pts, float), axis=0))
    labels = [l for l in after_frame.get("labels") or [] if l["id"] != label_id]
    if snap is not None:
        labels.append({"id": label_id, **snap})
    frame = {"name": frame_name, "url": after_frame["url"], "labels": labels}
    issue = SimpleNamespace(anchor=anchor, label_id=label_id, related_label_ids=[])
    root = Path(repo_root) / "local-data"
    img = FrameImage(image_path(frame, root), out / "roi_cache")
    try:
        crop = issue_crop(img, frame, issue)
    except FileNotFoundError:
        return None
    finally:
        img.release()
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), crop)
    return dest


VIEWB_JS = """
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
(async()=>{const rows=await (await fetch('/viewb/rows')).json();
 $('#n').textContent=rows.length+' edited frames';
 $('#rows').innerHTML=rows.map(r=>`<div class="card"><div class="meta"><div><b>${esc(r.export_stem)}</b></div>
 <div>${esc(r.frame_name)} &middot; ${r.changes.length} changes</div>
 <div>${r.projects.map(p=>`<a href="${p.link}" target="_blank">${esc(p.project)}</a>`).join('<br>')}</div>
 ${r.hand.length?'<div><span class="badge">draw by hand</span> '+r.hand.map(h=>esc(h.round)+': '+(h.done?'done':'not done')).join(', ')+'</div>':''}
 <table>${r.changes.map(c=>`<tr><td>${esc(c.round)}</td><td>label ${esc(c.label_id)}</td><td>${esc(c.what)}</td>
 <td><img loading="lazy" src="${c.before_crop}" title="before"></td><td><img loading="lazy" src="${c.after_crop}" title="after"></td></tr>`).join('')}</table>
 </div></div>`).join('');})();
"""


def viewb_html(css: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Annotation QA View B</title>
<style>{css}table img{{max-width:420px}}td{{padding:4px 8px;vertical-align:top}}</style></head><body>
<header><a href="/">&larr; View A</a> &nbsp; <b>View B: final review</b> &nbsp; <span id="n"></span>
&nbsp; (each change: before left, after right)</header><div id="rows"></div>
<script>{VIEWB_JS}</script></body></html>"""
