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
