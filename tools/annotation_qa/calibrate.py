"""Calibration of the yellow-structure detector (ruling R20).

``calibrate_yellow`` writes ``OUT/calibration/yellow_stats.json``:
- ``ctl_pair_distance_median``: geometry only, over ALL center_turn_lane labels
  of ALL exports (perpendicular distance to the nearest other CTL in the same
  frame, direction within 15 degrees; median).
- ``fwhm_double_median`` / ``fwhm_single_yellow_median`` / ``fwhm_threshold``
  (midpoint): measured on images. Frames holding double_yellow_line labels and
  frames holding solid_single_line labels are shuffled separately
  (random.Random(0)); frames are measured until 20 frames of each pool have
  yielded a measurement (40 in total), with a cap on frames opened per pool.
"""

from __future__ import annotations

import json
import logging
import os
import random
import statistics
from pathlib import Path

from annotation_qa.data import image_path, load_export
from annotation_qa.detectors.position import SOLID
from annotation_qa.detectors.yellow_structure import (
    CTL, DOUBLE, STATS_NAME, clear_cache, is_yellow, line_geom, longest_polys, nearest_partner)
from annotation_qa.imaging import FrameImage, yellow_fwhm

log = logging.getLogger("annotation_qa.calibrate")

FRAMES_PER_POOL = 20
MIN_WIDTH_RATIO = 1.5  # doubles must be this much wider than yellow singles for the width check
MAX_OPEN_PER_POOL = 120
MIN_LABEL_SAMPLES = 20  # a label's FWHM median needs this many valid profile points


def _measure(frame: dict, root: Path, roi_cache: Path) -> tuple[list[float], list[float]]:
    """(double fwhm list, single-yellow fwhm list) for one frame's labels."""
    img = FrameImage(image_path(frame, root), roi_cache)
    dbl, sgl = [], []
    try:
        for _lid, (_pi, poly, label) in longest_polys(frame, (DOUBLE, SOLID)).items():
            res = yellow_fwhm(img, poly)
            if res is not None and res[1] >= MIN_LABEL_SAMPLES and (label["category"] == DOUBLE or is_yellow(img, poly, res)):
                (dbl if label["category"] == DOUBLE else sgl).append(res[0])
    finally:
        img.release()
    return dbl, sgl


def calibrate_yellow(exports: list[Path], root: Path, out: Path) -> dict:
    out = Path(out)
    ctl_dists, n_ctl = [], 0
    pools: dict[str, list[tuple[str, dict]]] = {DOUBLE: [], SOLID: []}
    for path in exports:
        exp = load_export(path)
        for frame in exp.frames:
            ctl = longest_polys(frame, (CTL,))
            n_ctl += len(ctl)
            geoms = {lid: line_geom(poly) for lid, (_pi, poly, _l) in ctl.items()}
            geoms = {k: g for k, g in geoms.items() if g is not None}
            for lid in geoms:
                p = nearest_partner(geoms, lid)
                if p is not None:
                    ctl_dists.append(p[1])
            cats = {(lab.get("category")) for lab in frame.get("labels") or []}
            for cat in (DOUBLE, SOLID):
                if cat in cats:
                    pools[cat].append((exp.stem, frame))
    rng = random.Random(0)
    measured: dict[int, tuple[list[float], list[float]]] = {}
    doubles: list[float] = []
    singles: list[float] = []
    contributing = {DOUBLE: 0, SOLID: 0}
    for cat in (DOUBLE, SOLID):
        pool = pools[cat][:]
        rng.shuffle(pool)
        for n, (stem, frame) in enumerate(pool):
            if contributing[cat] >= FRAMES_PER_POOL or n >= MAX_OPEN_PER_POOL:
                break
            key = id(frame)
            if key not in measured:
                try:
                    measured[key] = _measure(frame, Path(root), out / "roi_cache")
                    if measured[key][0] or measured[key][1]:
                        doubles += measured[key][0]
                        singles += measured[key][1]
                except FileNotFoundError as e:
                    log.warning("calibration: skipping %s|%s: %s", stem, frame.get("name"), e)
                    measured[key] = ([], [])
            if measured[key][0 if cat == DOUBLE else 1]:
                contributing[cat] += 1
    if not ctl_dists or not doubles or not singles:
        raise RuntimeError(
            f"not enough data to calibrate: ctl pairs={len(ctl_dists)}, "
            f"double labels={len(doubles)}, yellow singles={len(singles)}")
    fd, fs = statistics.median(doubles), statistics.median(singles)
    stats = {
        "ctl_pair_distance_median": float(statistics.median(ctl_dists)),
        "fwhm_double_median": float(fd),
        "fwhm_single_yellow_median": float(fs),
        "fwhm_threshold": float((fd + fs) / 2),
        # a midpoint only means something when doubles are clearly wider than yellow singles
        "width_usable": bool(fd >= MIN_WIDTH_RATIO * fs),
        "n_ctl_labels": n_ctl, "n_ctl_with_partner": len(ctl_dists),
        "n_double_labels": len(doubles), "n_single_yellow_labels": len(singles),
        "n_frames_measured": len(measured),
        "n_frames_with_double": contributing[DOUBLE], "n_frames_with_single_yellow": contributing[SOLID],
    }
    target = Path(out) / STATS_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(stats, indent=1))
    os.replace(tmp, target)
    clear_cache(target)
    return stats
