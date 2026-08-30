"""The public API: correct a Scalabel annotation document.

One call applies both fixes to a parsed export document and hands back a
corrected copy plus a report. The CLI and the HTTP service are thin wrappers
over ``process_document`` -- there is no file I/O or global state here, so the
same function serves a batch of 221 frames and a single-frame API request.

Stage order is deliberate: CLAMP FIRST, THEN CONNECT.

Out-of-ROI vertices are frequently line ENDPOINTS sitting in the black padding,
and endpoints are exactly what auto-connect reasons about. The measured median
spill (8 px) is the same order of magnitude as the connect tolerance (15 px), so
connecting before clamping would evaluate junctions at positions that are about
to move -- and clamping afterwards could pull an already-merged junction apart.
Clamping first means every connection decision is made on final geometry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .autoconnect import (
    DEFAULT_MIN_ANGLE,
    DEFAULT_TOLERANCE,
    ConnectResult,
    connect_labels,
)
from .clamp import (
    DEFAULT_FLAG_DISTANCE,
    DEFAULT_INSET,
    ClampResult,
    clamp_labels,
)
from .mask import DEFAULT_THRESHOLD, load_roi_mask


@dataclass
class Options:
    """Knobs for one run. Defaults are the measured-sane values."""

    clamp: bool = True
    connect: bool = True
    threshold: int = DEFAULT_THRESHOLD
    tolerance: float = DEFAULT_TOLERANCE
    min_angle: float = DEFAULT_MIN_ANGLE
    inset: float = DEFAULT_INSET
    flag_distance: float = DEFAULT_FLAG_DISTANCE
    image_root: str = ""
    cache_dir: Optional[str] = None
    """Where to memoise ROI masks. None disables caching."""

    strict: bool = False
    """Fail the run on a missing image instead of skipping the clamp stage."""


@dataclass
class FrameReport:
    """What happened to one frame."""

    name: str
    image_found: bool = True
    roi_coverage: Optional[float] = None
    total_vertices: int = 0
    clamped: int = 0
    flagged: List[dict] = field(default_factory=list)
    merged: int = 0
    labels_before: int = 0
    labels_after: int = 0
    connections: List[dict] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialise for the run report."""
        return {
            "name": self.name,
            "imageFound": self.image_found,
            "roiCoverage": (
                None if self.roi_coverage is None else round(self.roi_coverage, 5)
            ),
            "totalVertices": self.total_vertices,
            "clamped": self.clamped,
            "flagged": self.flagged,
            "merged": self.merged,
            "labelsBefore": self.labels_before,
            "labelsAfter": self.labels_after,
            "connections": self.connections,
            "error": self.error,
        }


@dataclass
class Report:
    """Aggregate outcome across every frame in a document."""

    frames: List[FrameReport] = field(default_factory=list)

    @property
    def total_clamped(self) -> int:
        """Vertices moved back inside the ROI."""
        return sum(f.clamped for f in self.frames)

    @property
    def total_merged(self) -> int:
        """Polyline pairs joined."""
        return sum(f.merged for f in self.frames)

    @property
    def total_flagged(self) -> int:
        """Corrections large enough to warrant review."""
        return sum(len(f.flagged) for f in self.frames)

    @property
    def missing_images(self) -> List[str]:
        """Frames whose image could not be located."""
        return [f.name for f in self.frames if not f.image_found]

    def to_dict(self) -> dict:
        """Serialise the whole report."""
        return {
            "summary": {
                "frames": len(self.frames),
                "totalClamped": self.total_clamped,
                "totalMerged": self.total_merged,
                "totalFlagged": self.total_flagged,
                "missingImages": self.missing_images,
            },
            "frames": [f.to_dict() for f in self.frames],
        }


def resolve_image_path(name: str, image_root: str) -> Optional[str]:
    """Locate a frame's image.

    Frame names in these documents are paths relative to the data root (for
    example ``items/.../images/208683_verification.png``), but a document may
    also carry an absolute path or a bare filename. Tries the obvious
    interpretations and gives up rather than guessing wildly.
    """
    if not name:
        return None

    candidates = [name]
    if image_root:
        candidates.append(os.path.join(image_root, name))
        candidates.append(os.path.join(image_root, os.path.basename(name)))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def process_frame(
    frame: Dict[str, Any], options: Optional[Options] = None
) -> Tuple[Dict[str, Any], FrameReport]:
    """Correct a single frame in place, returning it with its report.

    The frame is mutated, so pass a copy if the original matters. Both stages
    are independently switchable; a frame whose image is missing still gets the
    connect stage, which needs no pixels.
    """
    options = options or Options()
    name = str(frame.get("name", ""))
    report = FrameReport(name=name)

    labels = frame.get("labels") or []
    report.labels_before = len(labels)
    report.labels_after = len(labels)

    if options.clamp:
        image_path = resolve_image_path(name, options.image_root)
        if image_path is None:
            report.image_found = False
            if options.strict:
                raise FileNotFoundError(f"image not found for frame: {name}")
        else:
            try:
                roi = load_roi_mask(
                    image_path, options.threshold, options.cache_dir
                )
                report.roi_coverage = roi.coverage
                clamp_result: ClampResult = clamp_labels(
                    roi, labels, inset=options.inset
                )
                report.total_vertices = clamp_result.total_vertices
                report.clamped = clamp_result.corrected_count
                report.flagged = [
                    c.to_dict()
                    for c in clamp_result.flagged(options.flag_distance)
                ]
            except Exception as exc:  # pragma: no cover - surfaced in the report
                report.error = f"{type(exc).__name__}: {exc}"
                if options.strict:
                    raise

    if options.connect and labels:
        survivors, connect_result = connect_labels(
            labels, tolerance=options.tolerance, min_angle=options.min_angle
        )
        frame["labels"] = survivors
        report.merged = connect_result.merged_count
        report.labels_after = connect_result.labels_after
        report.connections = [c.to_dict() for c in connect_result.connections]

    return frame, report


def process_document(
    document: Dict[str, Any], options: Optional[Options] = None
) -> Tuple[Dict[str, Any], Report]:
    """Correct every frame in a Scalabel export document.

    This is the API entry point:

        corrected, report = process_document(doc, Options(image_root="local-data/"))

    Accepts either a full document (``{"frames": [...], "config": {...}}``) or a
    bare list of frames, and returns the same shape it was given. The input is
    deep-copied, so the caller's document is never modified.
    """
    options = options or Options()

    import copy

    document = copy.deepcopy(document)

    if isinstance(document, list):
        frames = document
        container: Dict[str, Any] = {"frames": frames}
        bare_list = True
    else:
        frames = document.get("frames") or []
        container = document
        bare_list = False

    report = Report()
    for frame in frames:
        _, frame_report = process_frame(frame, options)
        report.frames.append(frame_report)

    return (frames if bare_list else container), report
