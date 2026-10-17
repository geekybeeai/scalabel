"""Whole-frame locator images: a cached 1/8 base per frame, circles drawn per request.

``OUT/locator/<sha1(stem|frame)[:12]>.jpg`` is the frame at 1/8 size (cv2.IMREAD_REDUCED_COLOR_8
keeps the decode of a 190 MP image cheap). Anchors are scaled by 1/8 and circled in red.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import cv2
import numpy as np

from annotation_qa.data import image_path
from annotation_qa.imaging import FrameImage  # noqa: F401 - sets OPENCV_IO_MAX_IMAGE_PIXELS

REDUCE = 8
LOCATOR_SLOTS = threading.Semaphore(2)
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
RED = (0, 0, 255)


def find_frame(out: Path, originals: Path, stem: str, name: str) -> dict | None:
    from annotation_qa.viewb import frames_by_name
    for p in (Path(out) / "work" / f"{stem}.json", Path(originals) / f"{stem}.json"):
        if p.exists():
            hit = frames_by_name(p).get(name)
            return hit[1] if hit else None
    return None


def base_path(out: Path, stem: str, frame_name: str) -> Path:
    h = hashlib.sha1(f"{stem}|{frame_name}".encode()).hexdigest()[:12]
    return Path(out) / "locator" / f"{h}.jpg"


def _write(dest: Path, small: np.ndarray) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".tmp.jpg")
    if cv2.imwrite(str(tmp), small, [cv2.IMWRITE_JPEG_QUALITY, 85]):
        tmp.replace(dest)


def save_base_from(out: Path, stem: str, frame_name: str, bgr: np.ndarray) -> None:
    """Make the base from an already decoded full image (prerender: one decode per frame)."""
    h, w = bgr.shape[:2]
    small = cv2.resize(bgr, (-(-w // REDUCE), -(-h // REDUCE)), interpolation=cv2.INTER_AREA)
    _write(base_path(out, stem, frame_name), small)


def ensure_base(out: Path, repo_root: Path, originals: Path, stem: str,
                frame_name: str) -> Path | None:
    dest = base_path(out, stem, frame_name)
    if dest.exists():
        return dest
    with _guard:
        lock = _locks.setdefault(str(dest), threading.Lock())
    with lock:
        if dest.exists():
            return dest
        frame = find_frame(out, originals, stem, frame_name)
        if frame is None:
            return None
        with LOCATOR_SLOTS:
            small = cv2.imread(str(image_path(frame, Path(repo_root) / "local-data")),
                               cv2.IMREAD_REDUCED_COLOR_8)
        if small is None:
            return None
        _write(dest, small)
    return dest


def draw(base: np.ndarray, marks: list[tuple[tuple[float, float], str | None]],
         width: int) -> bytes:
    """``base`` resized to ``width`` with a red circle (and its number) per mark; JPEG bytes."""
    h, w = base.shape[:2]
    s = width / w
    img = cv2.resize(base, (width, max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    r = max(7, width // 45)
    for (ax, ay), num in marks:
        c = (int(round(ax / REDUCE * s)), int(round(ay / REDUCE * s)))
        cv2.circle(img, c, r, RED, 2, cv2.LINE_AA)
        if num:
            sc = r / 14
            (tw, th), _ = cv2.getTextSize(num, cv2.FONT_HERSHEY_SIMPLEX, sc, 2)
            org = (c[0] + r + 2, c[1] + th // 2)
            cv2.putText(img, num, org, cv2.FONT_HERSHEY_SIMPLEX, sc, (255, 255, 255), 4,
                        cv2.LINE_AA)
            cv2.putText(img, num, org, cv2.FONT_HERSHEY_SIMPLEX, sc, RED, 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else b""


def render(out: Path, repo_root: Path, originals: Path, stem: str, frame_name: str,
           marks: list[tuple[tuple[float, float], str | None]], width: int) -> bytes | None:
    p = ensure_base(out, repo_root, originals, stem, frame_name)
    base = cv2.imread(str(p)) if p else None
    return None if base is None else draw(base, marks, width)
