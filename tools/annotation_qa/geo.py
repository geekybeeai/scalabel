"""Image lat/lon for the review pages: the same CSV Batch Review uses (tools/batch_review).

The CSV has a header row; the image column is the first header named image / image_name / name
/ file / filename (else containing image, name or file), lat is lat / latitude (else containing
"lat"), lon is lon / lng / long / longitude (else containing "lon" or "lng"); "," ";" or tab
separated. An image matches its exact file name (lower case) first, else by ``cad_ref.norm``
(basename without extension, _z<N> and a trailing input / verification; separators unified), as
Batch Review matches them -- so "98736-201751" and "98736_201751", two different images, keep
their own rows. Files are
read again when they change, so lat/lon typed or uploaded in Batch Review shows up here too.
"""

from __future__ import annotations

import csv
import io
import threading
from pathlib import Path

from annotation_qa.cad_ref import norm as _norm


def norm(name: str) -> str:
    """``cad_ref.norm`` of the file name only (no folders; "\\" or "/" separated)."""
    return _norm(str(name).replace("\\", "/").rsplit("/", 1)[-1])


REPO = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO / "tools" / "batch_review" / "image_locations.csv"

_IMAGE = (("image", "image_name", "name", "file", "filename"), ("image", "name", "file"))
_LAT = (("lat", "latitude"), ("lat",))
_LON = (("lon", "lng", "long", "longitude"), ("lon", "lng"))


def links(lat: float, lon: float) -> dict:
    ll = f"{lat},{lon}"
    return {"maps": f"https://www.google.com/maps/search/?api=1&query={ll}",
            "streetview": f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={ll}"}


def _pick(head: list[str], names: tuple[str, ...], contains: tuple[str, ...]) -> int:
    low = [h.strip().lower() for h in head]
    for n in names:
        if n in low:
            return low.index(n)
    for k, h in enumerate(low):
        if any(c in h for c in contains):
            return k
    return -1


def basename(name: str) -> str:
    return str(name).replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def read_csv(text: str) -> dict[str, tuple[float, float]]:
    """{norm(image): (lat, lon)}; rows that cannot be read are skipped."""
    return _parse(text)[0]


def _parse(text: str) -> tuple[dict, dict]:
    """({norm(image): (lat, lon)}, {exact lower-case file name: (lat, lon)})."""
    text = text.lstrip("﻿")
    head = text.splitlines()[0] if text else ""
    delim = max([",", ";", "\t"], key=lambda d: len(head.split(d)))
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        return {}, {}
    ic, la, lo = (_pick(rows[0], *c) for c in (_IMAGE, _LAT, _LON))
    if min(ic, la, lo) < 0:
        return {}, {}
    out, exact = {}, {}
    for r in rows[1:]:
        try:
            key, lat, lon = norm(r[ic]), float(r[la]), float(r[lo])
        except (IndexError, ValueError):
            continue
        if key and abs(lat) <= 90 and abs(lon) <= 180:
            out[key] = (lat, lon)
            exact[basename(r[ic])] = (lat, lon)
    return out, exact


class GeoIndex:
    """lat/lon of images from CSV files (later files win), re-read when a file changes."""

    def __init__(self, paths):
        self.paths = [Path(p) for p in paths]
        self._lock = threading.Lock()
        self._stamp = None
        self._geo: dict[str, tuple[float, float]] = {}
        self._exact: dict[str, tuple[float, float]] = {}

    def _load(self) -> tuple[dict, dict]:
        stamp = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.is_file() else None
                      for p in self.paths)
        with self._lock:
            if stamp != self._stamp:
                geo, exact = {}, {}
                for p in self.paths:
                    if p.is_file():
                        try:
                            g, e = _parse(p.read_text(encoding="utf-8"))
                        except (OSError, UnicodeDecodeError):
                            continue
                        geo.update(g)
                        exact.update(e)
                self._geo, self._exact, self._stamp = geo, exact, stamp
            return self._geo, self._exact

    def lookup(self, frame_name: str) -> dict | None:
        loose, exact = self._load()
        g = exact.get(basename(frame_name)) or loose.get(norm(str(frame_name)))
        return {"lat": g[0], "lon": g[1], **links(*g)} if g else None

    def count(self) -> int:
        return len(self._load()[1])


_default: GeoIndex | None = None


def default_index() -> GeoIndex:
    """The index the review pages use: DEFAULT_CSV (Batch Review's lat/lon CSV)."""
    global _default
    if _default is None:
        _default = GeoIndex([DEFAULT_CSV])
    return _default
