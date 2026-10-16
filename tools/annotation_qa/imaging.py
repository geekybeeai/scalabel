"""ROI-aware paint-ridge and side-strip scores, computed on padded bbox crops.

Whole images (up to ~190 MP) are never converted to float: scoring happens on
the padded bounding-box crop of one label and only there is data cast to
float32. Points come from ``geometry.dense`` (curve rule: never re-sample
Beziers here).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(1 << 40))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from annotation_fix.mask import load_roi_mask  # noqa: E402
from annotation_qa.geometry import dense, resample  # noqa: E402

MIN_VALID = 5


class FrameImage:
    """A frame's BGR image (loaded once, lazily) and its ROI mask."""

    def __init__(self, path: Path, roi_cache: Path):
        self.path = Path(path)
        self.roi_cache = Path(roi_cache)
        self._bgr: np.ndarray | None = None
        self._roi = None

    @property
    def bgr(self) -> np.ndarray:
        if self._bgr is None:
            img = cv2.imread(str(self.path), cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(f"cannot read image: {self.path}")
            self._bgr = img
        return self._bgr

    @property
    def roi(self):
        if self._roi is None:
            self._roi = load_roi_mask(self.path, cache_dir=self.roi_cache)
        return self._roi

    def release(self) -> None:
        """Free the decoded image (and ROI) so the next frame can load."""
        self._bgr = None
        self._roi = None

    @property
    def shape(self) -> tuple[int, int]:
        """(height, width) of the image."""
        return self.bgr.shape[:2]

    def crop(self, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
        """BGR uint8 view of [x0,x1) x [y0,y1), clipped to the canvas."""
        h, w = self.shape
        x0, x1 = max(int(x0), 0), min(int(x1), w)
        y0, y1 = max(int(y0), 0), min(int(y1), h)
        return self.bgr[y0:y1, x0:x1]


@dataclass
class Profile:
    """Ridge scores along each resampled point's normal.

    ``white``/``yellow`` are NaN wherever ``ok`` is False, so consumers must
    use nan-aware reductions (np.nanmean / np.nanmax).
    """

    offsets: np.ndarray  # ints -R..R
    white: np.ndarray  # (M, 2R+1) white ridge per resampled point
    yellow: np.ndarray  # (M, 2R+1) yellow ridge per resampled point
    valid: np.ndarray  # (M,) bool: == ok[:, R] (offset-0 cell and its sides inside)
    ok: np.ndarray  # (M, 2R+1) bool: centre cell and both side cells inside ROI+canvas
    P: np.ndarray  # (M, 2)
    Nrm: np.ndarray  # (M, 2)


@dataclass
class StripStats:
    gray: float
    sat: float
    green: float  # G - (R+B)/2
    brown: float  # R - B
    texture: float  # std of gray inside the strip
    n: int


def _resampled(poly: dict):
    pts = dense(poly)
    if not np.isfinite(pts).all():
        return None, None
    return resample(pts)


def _local_crop(img: FrameImage, P: np.ndarray, pad: int):
    """Float32 crop around P padded by ``pad``; returns (crop, x0, y0)."""
    if not np.isfinite(P).all():
        return None
    h, w = img.shape
    x0 = max(int(np.floor(P[:, 0].min())) - pad, 0)
    y0 = max(int(np.floor(P[:, 1].min())) - pad, 0)
    x1 = min(int(np.ceil(P[:, 0].max())) + pad + 1, w)
    y1 = min(int(np.ceil(P[:, 1].max())) + pad + 1, h)
    c = img.crop(x0, y0, x1, y1)
    if c.size == 0:
        return None
    return c.astype(np.float32), x0, y0


def _sample(chan: np.ndarray, x: np.ndarray, y: np.ndarray, x0: int, y0: int):
    out = cv2.remap(chan, (x - x0).astype(np.float32), (y - y0).astype(np.float32),
                    cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out


def _inside(img: FrameImage, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-position bool: inside the canvas and the ROI (rounded coords)."""
    h, w = img.shape
    ix, iy = np.rint(x).astype(int), np.rint(y).astype(int)
    ok = (ix >= 0) & (iy >= 0) & (ix < w) & (iy < h)
    res = np.zeros(ok.shape, dtype=bool)
    res[ok] = img.roi.mask[iy[ok], ix[ok]]
    return res


def _channels(crop: np.ndarray):
    b, g, r = crop[..., 0], crop[..., 1], crop[..., 2]
    gray = 0.114 * b + 0.587 * g + 0.299 * r
    sat = crop.max(axis=2) - crop.min(axis=2)
    return gray, sat, r, g, b


def profile(img: FrameImage, poly: dict, R: int = 25, side_gap: int = 5) -> Profile | None:
    P, Nrm = _resampled(poly)
    if P is None:
        return None
    ext = np.arange(-R - side_gap, R + side_gap + 1)
    if not np.isfinite(P).all():
        return None
    X = P[:, 0:1] + ext[None, :] * Nrm[:, 0:1]
    Y = P[:, 1:2] + ext[None, :] * Nrm[:, 1:2]
    offsets = np.arange(-R, R + 1)
    inside = _inside(img, X, Y)
    ok = (inside[:, :2 * R + 1] & inside[:, side_gap:side_gap + 2 * R + 1]
          & inside[:, 2 * side_gap:])
    valid = ok[:, R].copy()
    if valid.sum() < MIN_VALID:
        return None
    loc = _local_crop(img, P, R + side_gap + 2)
    if loc is None:
        return None
    crop, x0, y0 = loc
    gray, sat, r, g, b = _channels(crop)
    white = cv2.blur(gray - 0.8 * sat, (3, 3))
    yellow = cv2.blur((r + g) / 2 - b, (3, 3))
    centre = slice(side_gap, side_gap + 2 * R + 1)

    def ridge(score):
        s = _sample(score, X, Y, x0, y0)
        left, mid, right = s[:, :2 * R + 1], s[:, centre], s[:, 2 * side_gap:]
        return np.where(ok, mid - np.maximum(left, right), np.nan)

    return Profile(offsets, ridge(white), ridge(yellow), valid, ok, P, Nrm)


def side_strips(img: FrameImage, poly: dict, near: int = 6, far: int = 20):
    """(left, right) strip stats; left is the -Nrm side. None if too little data."""
    P, Nrm = _resampled(poly)
    if P is None or not np.isfinite(P).all():
        return None
    loc = _local_crop(img, P, far + 2)
    if loc is None:
        return None
    crop, x0, y0 = loc
    gray, sat, r, g, b = _channels(crop)
    planes = {"gray": gray, "sat": sat, "green": g - (r + b) / 2, "brown": r - b}
    dist = np.arange(near, far + 1)
    stats = []
    for sign in (-1, 1):
        X = P[:, 0:1] + sign * dist[None, :] * Nrm[:, 0:1]
        Y = P[:, 1:2] + sign * dist[None, :] * Nrm[:, 1:2]
        ok = _inside(img, X, Y)
        if ok.sum() < MIN_VALID:
            return None
        vals = {k: _sample(v, X, Y, x0, y0)[ok] for k, v in planes.items()}
        stats.append(StripStats(
            gray=float(vals["gray"].mean()), sat=float(vals["sat"].mean()),
            green=float(vals["green"].mean()), brown=float(vals["brown"].mean()),
            texture=float(vals["gray"].std()), n=int(ok.sum())))
    return stats[0], stats[1]


def yellow_fwhm(img: FrameImage, poly: dict, R: int = 25, peak_window: int = 3,
                min_contrast: float = 20.0) -> tuple[float, int, float] | None:
    """(median FWHM in px, n samples, median peak-minus-base contrast of the raw yellow score).

    Per resampled point the yellow score ((R+G)/2 - B, 3x3 blurred) is sampled
    over offsets -R..R; the peak is the maximum within ``peak_window`` of
    offset 0, the base the 10th percentile of the row, and the width the run of
    offsets around the peak that stay at or above (peak + base) / 2. Points
    whose window leaves the ROI/canvas, or whose peak is less than
    ``min_contrast`` above the base (no yellow), are dropped. None if fewer than
    MIN_VALID points remain.
    """
    P, Nrm = _resampled(poly)
    if P is None:
        return None
    ext = np.arange(-R, R + 1)
    X = P[:, 0:1] + ext[None, :] * Nrm[:, 0:1]
    Y = P[:, 1:2] + ext[None, :] * Nrm[:, 1:2]
    ok = _inside(img, X, Y).all(axis=1)
    if ok.sum() < MIN_VALID:
        return None
    loc = _local_crop(img, P, R + 2)
    if loc is None:
        return None
    crop, x0, y0 = loc
    _gray, _sat, r, g, b = _channels(crop)
    score = _sample(cv2.blur((r + g) / 2 - b, (3, 3)), X, Y, x0, y0)[ok]
    base = np.percentile(score, 10, axis=1)
    win = score[:, R - peak_window:R + peak_window + 1]
    k = R - peak_window + win.argmax(axis=1)
    peak = score[np.arange(len(score)), k]
    widths, centre = [], []
    for row, kk, pk, bs in zip(score, k, peak, base):
        if pk - bs < min_contrast:
            continue
        centre.append(pk - bs)
        half = (pk + bs) / 2
        lo = hi = int(kk)
        while lo > 0 and row[lo - 1] >= half:
            lo -= 1
        while hi < len(row) - 1 and row[hi + 1] >= half:
            hi += 1
        widths.append(hi - lo + 1)
    if len(widths) < MIN_VALID:
        return None
    return float(np.median(widths)), len(widths), float(np.median(centre))
