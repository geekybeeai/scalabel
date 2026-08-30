"""Correct Scalabel polyline annotations before import.

Two fixes, one pass:

* **ROI clamp** -- orthomosaic images pad a narrow captured footprint into a
  large rectangle with black. Vertices that drift into that padding are pulled
  back to the nearest valid pixel.
* **Auto-connect** -- polyline endpoints of the same category that nearly touch
  are spliced into a single label, the offline equivalent of the editor's
  drag-endpoint-onto-endpoint gesture.

Typical use::

    from annotation_fix import Options, process_document

    corrected, report = process_document(doc, Options(image_root="local-data/"))
    print(report.total_clamped, report.total_merged)
"""

from .autoconnect import Connection, ConnectResult, connect_labels
from .clamp import ClampResult, VertexCorrection, clamp_labels, clamp_vertices
from .core import (
    FrameReport,
    Options,
    Report,
    process_document,
    process_frame,
    resolve_image_path,
)
from .mask import RoiMask, compute_roi_mask, load_roi_mask

__all__ = [
    "Options",
    "Report",
    "FrameReport",
    "process_document",
    "process_frame",
    "resolve_image_path",
    "RoiMask",
    "compute_roi_mask",
    "load_roi_mask",
    "clamp_labels",
    "clamp_vertices",
    "ClampResult",
    "VertexCorrection",
    "connect_labels",
    "ConnectResult",
    "Connection",
]

__version__ = "0.1.0"
