"""TMI reference CAD drawings: RED lines = curb, GREEN lines = soft edge / no curb.

``cad_soft_flag(frame_name)`` says whether the CAD drawing of a frame shows any soft
edge (None when no CAD image matches). Green street-name TEXT is ignored: only thin,
elongated green components count as line strokes.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
CAD_DIR = REPO / "local-data" / "items" / "Full_Verfication" / "TMI_Annotations_basemap_annotated"
MIN_SIDE, MIN_ELONG, FLAG_LEN = 30, 8.0, 600.0
_CACHE_NAME = "cad_cache.json"


def norm(filename: str) -> str:
    """Normalized id (copy of build_annotated_basemaps.norm)."""
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"_z\d+$", "", stem, flags=re.I)
    stem = re.sub(r"[_\s-]*(input|verification)$", "", stem, flags=re.I)
    return re.sub(r"[\s_-]+", "_", stem.strip()).lower()


def line_length(rgba: np.ndarray, color: str) -> float:
    """Summed bbox diagonal of thin elongated strokes of ``color`` ('green'|'red')."""
    r, g, b = (rgba[..., i].astype(np.int16) for i in range(3))
    a = rgba[..., 3] if rgba.shape[2] > 3 else np.full(r.shape, 255, np.uint8)
    if color == "green":
        m = (g > 120) & (r < 90) & (b < 90)
    else:
        m = (r > 120) & (g < 90) & (b < 90)
    m &= a > 0
    n, _, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    total = 0.0
    for k in range(1, n):
        w, h, area = int(st[k, 2]), int(st[k, 3]), int(st[k, 4])
        L = max(w, h)
        if L >= MIN_SIDE and L / (area / L) >= MIN_ELONG:
            total += float(np.hypot(w, h))
    return total


def _index(cad_dir: Path) -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for p in sorted(Path(cad_dir).glob("Batch_*/*.png")):
        idx.setdefault(norm(p.name), p)
    return idx


class CadRef:
    """frame_name -> CAD flag, with a per-file cache in ``out/cad_cache.json``."""

    def __init__(self, out: Path | None = None, cad_dir: Path | None = None):
        self.cad_dir = Path(cad_dir) if cad_dir else CAD_DIR
        self.path = Path(out) / _CACHE_NAME if out else None
        self._idx: dict[str, Path] | None = None
        self._cache: dict = {}
        if self.path and self.path.exists():
            try:
                self._cache = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self._cache = {}
        self._mem: dict[str, dict | None] = {}

    def info(self, frame_name: str) -> dict | None:
        if frame_name in self._mem:
            return self._mem[frame_name]
        if self._idx is None:
            self._idx = _index(self.cad_dir)
        p = self._idx.get(norm(os.path.basename(frame_name)))
        res = None
        if p is not None:
            key = f"{p}|{p.stat().st_mtime_ns}"
            res = self._cache.get(key)
            if res is None:
                img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
                if img is None or img.ndim != 3:
                    res = None
                else:
                    code = cv2.COLOR_BGRA2RGBA if img.shape[2] == 4 else cv2.COLOR_BGR2RGB
                    img = cv2.cvtColor(img, code)
                    gl, rl = line_length(img, "green"), line_length(img, "red")
                    res = {"green_len": gl, "cad_curb_length": rl, "soft": gl >= FLAG_LEN}
                    self._cache[key] = res
                    self._save()
        self._mem[frame_name] = res
        return res

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self._cache))
        os.replace(tmp, self.path)

    def __call__(self, frame_name: str) -> bool | None:
        i = self.info(frame_name)
        return None if i is None else bool(i["soft"])

    def green_len(self, frame_name: str) -> float | None:
        i = self.info(frame_name)
        return None if i is None else float(i["green_len"])


_REFS: dict[str, CadRef] = {}


def cad_for(out: Path | None) -> CadRef:
    k = str(out)
    if k not in _REFS:
        _REFS[k] = CadRef(out)
    return _REFS[k]


def cad_soft_flag(frame_name: str, out: Path | None = None) -> bool | None:
    return cad_for(out)(frame_name)
