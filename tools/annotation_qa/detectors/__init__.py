"""Detector registry: issue type -> callable(FrameContext) -> list[Issue].

Contract: a detector emits at most ONE issue per (export_stem, frame_name,
label_id, issue_type); run_scan keeps the first and warns on a duplicate key.

Each detector module is registered below (imported after REGISTRY exists).
"""

from __future__ import annotations

from typing import Callable

from annotation_qa.context import FrameContext
from annotation_qa.issues import Issue

REGISTRY: dict[str, Callable[[FrameContext], list[Issue]]] = {}

from annotation_qa.detectors import curb_soft  # noqa: E402

REGISTRY["curb_soft"] = curb_soft.detect
from annotation_qa.detectors import position  # noqa: E402

REGISTRY["position"] = position.detect
from annotation_qa.detectors import shape  # noqa: E402

REGISTRY["shape"] = shape.detect
from annotation_qa.detectors import duplicates  # noqa: E402

REGISTRY["duplicates"] = duplicates.detect
from annotation_qa.detectors import solid_dashed  # noqa: E402

REGISTRY["solid_dashed"] = solid_dashed.detect
from annotation_qa.detectors import yellow_structure  # noqa: E402

REGISTRY["yellow_structure"] = yellow_structure.detect
from annotation_qa.detectors import edge_position  # noqa: E402

REGISTRY["edge_position"] = edge_position.detect
from annotation_qa.detectors import edge_on_paint  # noqa: E402

REGISTRY["edge_on_paint"] = edge_on_paint.detect
