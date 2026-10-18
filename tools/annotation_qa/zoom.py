"""High-detail on-demand crops for the review lightbox, and their prerender.

``OUT/zoom/<hash>.jpg`` is the labelled | raw pair (half=220, zoom=3, panels of 1320 px);
``<hash>_raw.jpg`` is the raw panel alone. The hash covers RENDER_VERSION, the issue (key,
anchor, related labels, fix) and the frame's url and labels as the crop draws them (work
copy, else original), so a merge, a live edit or a new renderer gives a new file instead of
a stale crop. A crop is rendered once, then served from the file. At most ``ZOOM_SLOTS``
renders run at once (images are up to 190 MP).
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import cv2

from annotation_qa.data import image_path
from annotation_qa.imaging import FrameImage
from annotation_qa.locator import find_frame, save_base_from, ensure_base, base_path
from annotation_qa.issues import Issue, calibration_sample_path, load_issues_tolerant
from annotation_qa.render import RENDER_VERSION, issue_crop

HALF, ZOOM = 220, 3
ZOOM_SLOTS = threading.Semaphore(2)
_locks: dict[tuple[str, str], threading.Lock] = {}
_locks_guard = threading.Lock()


def zoom_path(out: Path, issue: Issue, frame: dict, raw: bool = False) -> Path:
    """Cache file of one zoom crop of ``issue`` drawn on ``frame`` (see the module doc)."""
    key = json.dumps([RENDER_VERSION, HALF, ZOOM, issue.key, list(issue.anchor),
                      sorted(map(str, issue.related_label_ids or [])), issue.fix,
                      frame.get("url"), frame.get("labels") or []], sort_keys=True, default=str)
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    return Path(out) / "zoom" / f"{h}{'_raw' if raw else ''}.jpg"


def _frame_lock(stem: str, name: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault((stem, name), threading.Lock())


def render_issues(out: Path, repo_root: Path, originals: Path, issues: list[Issue],
                  variants: tuple[bool, ...] = (False, True)) -> int:
    """Render the missing zoom crops of issues that share ONE frame (one image decode)."""
    first = issues[0]
    frame = find_frame(out, originals, first.export_stem, first.frame_name)
    if frame is None:
        return 0
    todo = [(i, raw) for i in issues for raw in variants
            if not zoom_path(out, i, frame, raw).exists()]
    if not todo:
        return 0
    n = 0
    with _frame_lock(first.export_stem, first.frame_name), ZOOM_SLOTS:
        img = FrameImage(image_path(frame, Path(repo_root) / "local-data"), Path(out) / "roi_cache")
        try:
            if not base_path(out, first.export_stem, first.frame_name).exists():
                try:
                    save_base_from(out, first.export_stem, first.frame_name, img.bgr)
                except FileNotFoundError:
                    return 0
            for issue, raw in todo:
                dest = zoom_path(out, issue, frame, raw)
                if dest.exists():
                    continue
                try:
                    crop = issue_crop(img, frame, issue, half=HALF, zoom=ZOOM, raw_only=raw)
                except FileNotFoundError:
                    return n
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.stem + ".tmp.jpg")
                if cv2.imwrite(str(tmp), crop, [cv2.IMWRITE_JPEG_QUALITY, 92]):
                    tmp.replace(dest)
                    n += 1
        finally:
            img.release()
    return n


def render_zoom(out: Path, repo_root: Path, originals: Path, issue: Issue,
                raw: bool = False) -> Path | None:
    frame = find_frame(out, originals, issue.export_stem, issue.frame_name)
    if frame is None:
        return None
    dest = zoom_path(out, issue, frame, raw)
    if not dest.exists():
        render_issues(out, repo_root, originals, [issue], (raw,))
    return dest if dest.exists() else None


def prerender(out: Path, repo_root: Path, originals: Path,
              calibration_only: bool = False) -> int:
    """Render zoom crops (both variants), grouped by frame; return the number written."""
    out = Path(out)
    issues: list[Issue] = []
    for p in sorted(out.glob("issues_*.jsonl")):
        t = p.stem[len("issues_"):]
        found = load_issues_tolerant(p)
        if calibration_only:
            try:
                keys = set(json.loads(calibration_sample_path(out, t).read_text())["keys"])
            except (OSError, ValueError, KeyError, TypeError):
                print(f"{t}: no calibration sample, skipped", flush=True)
                continue
            found = [i for i in found if i.key in keys]
        issues += found
    groups: dict[tuple[str, str], list[Issue]] = {}
    for i in issues:
        groups.setdefault((i.export_stem, i.frame_name), []).append(i)
    total = 0
    for k, ((stem, name), grp) in enumerate(groups.items(), 1):
        try:
            n = render_issues(out, repo_root, originals, grp)
        except Exception as e:  # noqa: BLE001 - keep going with the next frame
            print(f"[{k}/{len(groups)}] {stem}|{name}: FAILED {type(e).__name__}: {e}", flush=True)
            continue
        total += n
        try:
            ensure_base(out, repo_root, originals, stem, name)  # no-op when the decode made it
        except Exception as e:  # noqa: BLE001
            print(f"  locator failed: {e}", flush=True)
        print(f"[{k}/{len(groups)}] {stem}|{name}: {n} crops", flush=True)
    return total
