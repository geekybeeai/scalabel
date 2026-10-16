"""Loading and saving Scalabel export JSON, plus frame/label iteration helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGINALS_DIR = (
    REPO_ROOT
    / "local-data/items/Full_Verfication/Annotation_Reviewed_by_Roshan/2026_10_15_export"
).resolve()


@dataclass
class Export:
    path: Path
    frames: list[dict]
    config: dict

    @property
    def stem(self) -> str:
        return Path(self.path).stem


def load_export(path: Path) -> Export:
    path = Path(path)
    with open(path) as f:
        raw = json.load(f)
    return Export(path, raw.get("frames", []), raw.get("config", {}))


def save_export(exp: Export, path: Path) -> None:
    """Write ``{"frames", "config"}``; never overwrite the original exports."""
    target = Path(path).resolve()
    if target == ORIGINALS_DIR or ORIGINALS_DIR in target.parents:
        raise ValueError(f"refusing to write under the originals dir: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump({"frames": exp.frames, "config": exp.config}, f)
    os.replace(tmp, target)  # atomic: a crash never leaves a half-written export


# The 8 frames the downstream reviewer flagged, as basename prefixes.
REVIEWER_FRAMES = ["208199", "207851", "MLC 1", "100865_201054", "565_verification",
                   "860_verification", "105193_204737", "100292_200553"]


def reviewer_match(frame_name: str) -> str | None:
    """The reviewer-frame id whose prefix starts the frame's basename, else None.

    The prefix must not be followed by another digit, so "MLC 1" matches
    "MLC 1_verification_z20.png" but not "MLC 10_..." and "565_verification"
    does not match "113652_211565_verification.png".
    """
    base = Path(frame_name).name
    for sub in REVIEWER_FRAMES:
        if base.startswith(sub) and not base[len(sub):len(sub) + 1].isdigit():
            return sub
    return None


def iter_polys(frame: dict) -> Iterator[tuple[dict, int, dict]]:
    """Yield ``(label, poly_index, poly)`` for every poly2d entry of a frame."""
    for label in frame.get("labels") or []:
        for i, poly in enumerate(label.get("poly2d") or []):
            yield label, i, poly


def image_path(frame: dict, root: Path) -> Path:
    """Frame ``url`` is relative to ``root`` (the ``local-data`` dir)."""
    return Path(root) / frame["url"].lstrip("/")
