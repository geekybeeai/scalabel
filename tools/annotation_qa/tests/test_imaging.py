from annotation_qa.imaging import profile, side_strips

import numpy as np

from annotation_qa.tests.conftest import hpoly


def test_ridge_peaks_on_white_stripe(road_with_white_stripe):
    img, poly_on, poly_off12 = road_with_white_stripe
    pr = profile(img, poly_off12)
    assert pr.offsets[np.nanargmax(np.nanmean(pr.white[pr.valid], 0))] in (-12, 12)
    on = profile(img, poly_on)
    assert on.offsets[np.nanargmax(np.nanmean(on.white[on.valid], 0))] == 0
    assert np.nanmax(np.nanmean(on.yellow[on.valid], 0)) < 10


def test_concrete_band_is_not_a_ridge(road_with_concrete_band):
    img, poly = road_with_concrete_band
    pr = profile(img, poly)
    assert np.nanmax(np.nanmean(pr.white[pr.valid], 0)) < 10


def test_profile_none_outside_roi(road_mostly_black):
    img, poly = road_mostly_black
    assert profile(img, poly) is None


def test_side_strips_green_vs_concrete(road_with_grass_left_concrete_right):
    img, poly = road_with_grass_left_concrete_right
    left, right = side_strips(img, poly)
    assert left.green > right.green + 15 and right.gray > left.gray
    assert left.n > 0 and right.n > 0


def test_crop_clips_to_canvas(road_with_white_stripe):
    img, _, _ = road_with_white_stripe
    assert img.crop(-20, -20, 30, 30).shape == (30, 30, 3)


def test_shapes(road_with_white_stripe):
    img, poly_on, _ = road_with_white_stripe
    pr = profile(img, poly_on, R=20)
    assert len(pr.offsets) == 41
    assert pr.white.shape == (len(pr.valid), 41) == pr.ok.shape
    assert pr.valid.tolist() == pr.ok[:, 20].tolist()


def test_roi_edge_sides_are_nan_not_inflated(road_with_concrete_band):
    img, _ = road_with_concrete_band
    # ROI edge (black beyond) is at y=10; label 6 px inside it
    pr = profile(img, hpoly(16))
    assert pr.valid.all()
    # offset -10 -> y=6 (black): not ok; offset -6 (y=10 ok) side at y=5 black
    j = {o: i for i, o in enumerate(pr.offsets)}
    assert np.isnan(pr.white[:, j[-10]]).all()
    assert np.isnan(pr.white[:, j[-6]]).all()
    assert abs(np.nanmean(pr.white[:, j[0]])) < 10  # not inflated by black side
    assert not np.isnan(pr.white[:, j[0]]).any()


def test_off_canvas_and_short_labels_return_none(road_with_concrete_band):
    img, _ = road_with_concrete_band
    off = hpoly(-500)
    assert profile(img, off) is None
    assert side_strips(img, off) is None
    nan = {"vertices": [[float("nan"), 5], [50, 5]], "types": "LL", "closed": False}
    assert profile(img, nan) is None


def test_side_strips_none_when_strip_outside_roi(road_mostly_black):
    img, poly = road_mostly_black
    assert side_strips(img, poly) is None
