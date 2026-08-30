"""ROI mask extraction for orthomosaic images.

The images are aerial orthomosaics composited into a rectangular canvas: the
captured imagery occupies a narrow, often cross/L-shaped footprint and every
pixel outside it is pure black padding. Annotations are only meaningful inside
that footprint, so the mask produced here defines the valid region.

Channel reduction uses ``max(R, G, B)``, NOT luminance. Luminance weights green
~0.59 and blue ~0.11, which pushes dark blue-grey asphalt below the threshold
and shatters the mask: measured on a real 20000x9440 frame, ``convert("L")``
yields 54 connected components where ``max(R, G, B)`` yields 2. Any non-zero
channel means real imagery.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from PIL import Image
from scipy import ndimage

# Orthomosaic padding is exactly (0, 0, 0); real imagery essentially never is.
# Measured insensitive: the non-black fraction moves only 0.1415 -> 0.1396
# across thresholds 2..30 on a sample frame, so this needs no per-image tuning.
DEFAULT_THRESHOLD = 10

# PIL refuses very large images by default as a decompression-bomb guard. These
# orthomosaics legitimately reach ~190 megapixels.
Image.MAX_IMAGE_PIXELS = None


@dataclass
class RoiMask:
    """A boolean ROI mask plus the lazily-built nearest-inside lookup."""

    mask: np.ndarray
    """True where the pixel is inside the region of interest."""

    _distance: Optional[np.ndarray] = None
    _indices: Optional[np.ndarray] = None

    @property
    def height(self) -> int:
        """Mask height in pixels."""
        return int(self.mask.shape[0])

    @property
    def width(self) -> int:
        """Mask width in pixels."""
        return int(self.mask.shape[1])

    @property
    def coverage(self) -> float:
        """Fraction of the canvas inside the ROI (typically 0.07-0.18 here)."""
        return float(self.mask.mean())

    def contains(self, x: float, y: float) -> bool:
        """Whether an image-space point lies inside the ROI.

        Points beyond the canvas are outside by definition.
        """
        ix, iy = int(round(x)), int(round(y))
        if ix < 0 or iy < 0 or ix >= self.width or iy >= self.height:
            return False
        return bool(self.mask[iy, ix])

    def _ensure_transform(self) -> None:
        """Build the Euclidean distance transform over the outside region.

        ``return_indices`` gives, for every outside pixel, the coordinates of
        the nearest inside pixel. That turns clamping into an O(1) table lookup
        per vertex -- no contour tracing and no point-in-polygon test. It is the
        single most expensive step, so it is built on first use and only when
        something actually falls outside.
        """
        if self._indices is not None:
            return
        distance, indices = ndimage.distance_transform_edt(
            ~self.mask, return_indices=True
        )
        self._distance = distance
        self._indices = indices

    def nearest_inside(self, x: float, y: float) -> Tuple[int, int, float]:
        """Nearest in-ROI pixel to a point, with the distance to it.

        Returns ``(nx, ny, distance)``. For a point already inside, the point
        itself is returned with distance 0.
        """
        ix = int(np.clip(round(x), 0, self.width - 1))
        iy = int(np.clip(round(y), 0, self.height - 1))
        if self.mask[iy, ix]:
            return ix, iy, 0.0
        self._ensure_transform()
        assert self._indices is not None and self._distance is not None
        ny = int(self._indices[0, iy, ix])
        nx = int(self._indices[1, iy, ix])
        # Measure from the true (unrounded) point so sub-pixel spill is not
        # quantised away by the round() above.
        dist = float(np.hypot(nx - x, ny - y))
        return nx, ny, dist


def compute_roi_mask(
    image_path: str, threshold: int = DEFAULT_THRESHOLD
) -> RoiMask:
    """Extract the ROI mask from an orthomosaic image.

    Three steps, in order:

    1. Threshold ``max(R, G, B)`` to separate imagery from black padding.
    2. Fill interior holes. Dark asphalt, shadows and dark vehicles inside the
       road threshold to black and would otherwise punch false holes through
       the region.
    3. Keep the largest connected component, dropping stray specks such as
       compression noise in the padding.
    """
    with Image.open(image_path) as handle:
        rgb = handle.convert("RGB")
        array = np.asarray(rgb)

    # max over channels: any non-zero channel means real imagery.
    mask = array.max(axis=2) > threshold
    del array

    mask = ndimage.binary_fill_holes(mask)

    labelled, count = ndimage.label(mask)
    if count > 1:
        sizes = ndimage.sum(mask, labelled, range(1, count + 1))
        mask = labelled == (1 + int(np.argmax(sizes)))
    del labelled

    return RoiMask(mask=np.ascontiguousarray(mask))


def cache_key(image_path: str, threshold: int) -> str:
    """Cache identity for a mask: path, mtime, size and threshold."""
    stat = os.stat(image_path)
    raw = f"{os.path.abspath(image_path)}|{stat.st_mtime_ns}|{stat.st_size}|{threshold}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_roi_mask(
    image_path: str,
    threshold: int = DEFAULT_THRESHOLD,
    cache_dir: Optional[str] = None,
) -> RoiMask:
    """Compute a ROI mask, reusing a cached one when the image is unchanged.

    Masks are stored bit-packed, so even a 190-megapixel frame caches to a few
    megabytes. A cache miss on a corrupt or unreadable entry falls through to
    recomputation rather than failing.
    """
    if cache_dir is None:
        return compute_roi_mask(image_path, threshold)

    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{cache_key(image_path, threshold)}.npz")

    if os.path.exists(path):
        try:
            with np.load(path) as data:
                packed = data["packed"]
                shape = tuple(int(v) for v in data["shape"])
            bits = np.unpackbits(packed, count=shape[0] * shape[1])
            return RoiMask(mask=bits.reshape(shape).astype(bool))
        except Exception:
            pass  # Unreadable cache entry: recompute below.

    roi = compute_roi_mask(image_path, threshold)
    try:
        np.savez_compressed(
            path,
            packed=np.packbits(roi.mask),
            shape=np.asarray(roi.mask.shape),
        )
    except OSError:
        pass  # A cache write failure must never fail the run.
    return roi
