import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from annotation_qa.data import Export  # noqa: E402


def _label(i: int, category: str, vertices: list) -> dict:
    return {
        "id": str(i),
        "category": category,
        "attributes": {},
        "manualShape": True,
        "poly2d": [{"vertices": vertices, "types": "L" * len(vertices), "closed": False}],
        "box2d": None,
        "box3d": None,
    }


@pytest.fixture
def two_poly_label_frame() -> dict:
    return {
        "name": "f1.png",
        "url": "items/f1.png",
        "labels": [
            _label(1, "solid_single_line", [[0, 0], [50, 0]]),
            _label(2, "curb_road_edge", [[0, 20], [50, 20]]),
        ],
    }


@pytest.fixture
def small_export(two_poly_label_frame) -> Export:
    return Export(Path("small.json"), [two_poly_label_frame], {})
