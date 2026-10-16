"""FrameContext lives in its own module so detectors can import it without
importing ``scan`` (which imports the detector registry): no import cycle.
``scan`` re-exports it as ``scan.FrameContext``."""

from __future__ import annotations

from dataclasses import dataclass

from annotation_qa.data import Export
from annotation_qa.imaging import FrameImage


@dataclass
class FrameContext:
    export: Export
    frame: dict
    image: FrameImage
