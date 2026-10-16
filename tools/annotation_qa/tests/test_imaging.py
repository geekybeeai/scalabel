from annotation_qa.imaging import profile, side_strips


def test_ridge_peaks_on_white_stripe(road_with_white_stripe):
    img, poly_on, poly_off12 = road_with_white_stripe
    pr = profile(img, poly_off12)
    assert pr.offsets[pr.white[pr.valid].mean(0).argmax()] in (-12, 12)
    on = profile(img, poly_on)
    assert on.offsets[on.white[on.valid].mean(0).argmax()] == 0
    assert on.yellow[on.valid].mean(0).max() < 10


def test_concrete_band_is_not_a_ridge(road_with_concrete_band):
    img, poly = road_with_concrete_band
    pr = profile(img, poly)
    assert pr.white[pr.valid].mean(0).max() < 10


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
