"""Loading and saving Scalabel export JSON, plus frame/label iteration helpers."""

from __future__ import annotations

import json
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
    with open(target, "w") as f:
        json.dump({"frames": exp.frames, "config": exp.config}, f)


def iter_polys(frame: dict) -> Iterator[tuple[dict, int, dict]]:
    """Yield ``(label, poly_index, poly)`` for every poly2d entry of a frame."""
    for label in frame.get("labels") or []:
        for i, poly in enumerate(label.get("poly2d") or []):
            yield label, i, poly


def image_path(frame: dict, root: Path) -> Path:
    """Frame ``url`` is relative to ``root`` (the ``local-data`` dir)."""
    return Path(root) / frame["url"].lstrip("/")
