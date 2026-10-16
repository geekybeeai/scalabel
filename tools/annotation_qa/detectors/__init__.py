"""Detector registry: issue type -> callable(FrameContext) -> list[Issue].

Empty for now; each detector module registers itself here in later tasks.
"""

from __future__ import annotations

from typing import Callable

from annotation_qa.context import FrameContext
from annotation_qa.issues import Issue

REGISTRY: dict[str, Callable[[FrameContext], list[Issue]]] = {}
