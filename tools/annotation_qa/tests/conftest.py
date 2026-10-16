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


# ---- synthetic images (400x400, black border, gray "road" interior) ----
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from annotation_qa.imaging import FrameImage  # noqa: E402

ROAD = (110, 110, 110)


def _road(size=400, margin=10) -> np.ndarray:
    img = np.zeros((size, size, 3), np.uint8)
    img[margin:size - margin, margin:size - margin] = ROAD
    return img


def _frame(tmp_path, img) -> FrameImage:
    p = tmp_path / "img.png"
    cv2.imwrite(str(p), img)
    return FrameImage(p, tmp_path / "roi")


def hpoly(y: float, x0: float = 50, x1: float = 350) -> dict:
    return {"vertices": [[x0, y], [x1, y]], "types": "LL", "closed": False}


@pytest.fixture
def road_with_white_stripe(tmp_path):
    img = _road()
    # 5 px stripe with a brighter core so the ridge has a unique peak row
    for dy, v in zip(range(-2, 3), (200, 230, 255, 230, 200)):
        img[200 + dy, 10:390] = (v, v, v)
    return _frame(tmp_path, img), hpoly(200), hpoly(212)


@pytest.fixture
def road_with_concrete_band(tmp_path):
    img = _road()
    img[185:215, 10:390] = (170, 170, 170)
    return _frame(tmp_path, img), hpoly(200)


@pytest.fixture
def road_mostly_black(tmp_path):
    img = np.zeros((400, 400, 3), np.uint8)
    img[300:390, 300:390] = ROAD
    return _frame(tmp_path, img), hpoly(100)


@pytest.fixture
def road_with_grass_left_concrete_right(tmp_path):
    img = _road()
    img[10:200, 10:390] = (60, 140, 60)
    img[200:390, 10:390] = (170, 170, 170)
    return _frame(tmp_path, img), hpoly(200)


# ---- curb / soft-edge scenes: outside material above y=200, road below ----
GRASS = (40, 120, 40)


def _outside(img, rows, x0, x1, kind):
    if kind == "concrete":
        img[rows, x0:x1] = (170, 170, 170)
    else:
        rng = np.random.default_rng(0)
        noise = rng.normal(0, 12, (img[rows, x0:x1].shape[0], x1 - x0, 1))
        img[rows, x0:x1] = np.clip(np.array(GRASS) + noise, 0, 255).astype(np.uint8)


def make_edge_ctx(tmp_path, category: str, left: str, right: str):
    """FrameContext with one horizontal edge label at y=200 (x 50..350).

    Road lies below the line; above it the outside is ``left`` for x<200 and
    ``right`` for x>=200 ("concrete" or "grass").
    """
    from annotation_qa.context import FrameContext

    img = _road()
    _outside(img, slice(10, 200), 10, 200, left)
    _outside(img, slice(10, 200), 200, 390, right)
    label = {"id": "L1", "category": category, "attributes": {}, "manualShape": True,
             "poly2d": [hpoly(200)], "box2d": None, "box3d": None}
    frame = {"name": "f.png", "url": "f.png", "labels": [label]}
    return FrameContext(Export(Path("exp.json"), [frame], {}), frame, _frame(tmp_path, img))


@pytest.fixture
def scene_concrete_sidewalk_labelled_soft(tmp_path):
    return make_edge_ctx(tmp_path, "without_curb_road_edge", "concrete", "concrete")


@pytest.fixture
def scene_concrete_sidewalk_labelled_curb(tmp_path):
    return make_edge_ctx(tmp_path, "curb_road_edge", "concrete", "concrete")


@pytest.fixture
def scene_grass_labelled_curb(tmp_path):
    return make_edge_ctx(tmp_path, "curb_road_edge", "grass", "grass")


@pytest.fixture
def scene_grass_labelled_soft(tmp_path):
    return make_edge_ctx(tmp_path, "without_curb_road_edge", "grass", "grass")


@pytest.fixture
def scene_half_grass_half_concrete(tmp_path):
    return make_edge_ctx(tmp_path, "curb_road_edge", "grass", "concrete")
