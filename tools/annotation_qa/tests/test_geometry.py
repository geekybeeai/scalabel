import numpy as np
import pytest
from scipy.spatial import cKDTree

from annotation_qa.geometry import dense, resample, shift_poly

BEZ = {"vertices": [[0, 0], [0, 100], [100, 100], [100, 0]], "types": "LCCL", "closed": False}


def test_dense_follows_bezier_not_control_points():
    pts = dense(BEZ)
    assert pts[0].tolist() == [0, 0] and pts[-1].tolist() == [100, 0]
    assert np.abs(pts[:, 1]).max() == pytest.approx(75, abs=1)  # bezier peak, not 100


def test_closed_poly_wraps():
    poly = {"vertices": [[0, 0], [10, 0], [10, 10]], "types": "LLL", "closed": True}
    assert dense(poly)[-1].tolist() == [0, 0]


def test_resample_short_line_returns_none():
    assert resample(np.array([[0, 0], [10, 0]]))[0] is None


def test_shift_poly_moves_curve_uniformly():
    P, N = resample(dense(BEZ))
    moved = shift_poly(BEZ, np.full(len(P), 8.0))
    # dense() has ~5 px vertex spacing; resample finely so the KD-tree distance
    # measures distance to the curve, not to its nearest vertex
    Q, _ = resample(dense(moved), step=0.5)
    d = cKDTree(Q).query(P + 8.0 * N)[0]
    assert d.max() < 1.5


def test_shift_poly_straight_line_moves_all_vertices():
    poly = {"vertices": [[0, 0], [50, 0], [100, 0]], "types": "LLL", "closed": False}
    P, N = resample(dense(poly))
    moved = shift_poly(poly, np.full(len(P), 5.0))
    assert np.allclose(np.array(moved["vertices"])[:, 1], N[0][1] * 5.0)
    assert poly["vertices"][0] == [0, 0]  # input untouched


def _max_err(poly, off_fn):
    P, N = resample(dense(poly))
    off = off_fn(len(P))
    Q, _ = resample(dense(shift_poly(poly, off)), step=0.5)
    d = cKDTree(Q).query(P + off[:, None] * N)[0]
    if poly["closed"]:
        # samples near the closing corners have one-sided/averaged normals that
        # are not the mitred offset target; ignore them
        v = np.array(poly["vertices"][-1:] + poly["vertices"][:1], dtype=float)
        d = d[cKDTree(v).query(P)[0] > 15]
    return d.max()


LLCCLL = {"vertices": [[0, 0], [60, 0], [60, 60], [120, 60], [180, 0], [240, 0]],
          "types": "LLCCLL", "closed": False}
CLOSED = {"vertices": [[0, 0], [100, 0], [150, 50], [100, 100], [0, 100]],
          "types": "LLCCL", "closed": True}


def test_shift_mixed_open_nonuniform():
    assert _max_err(LLCCLL, lambda n: np.linspace(-6, 10, n)) <= 1.5


def test_shift_closed_uniform():
    assert _max_err(CLOSED, lambda n: np.full(n, 8.0)) <= 1.5


def test_shift_closed_smooth():
    assert _max_err(CLOSED, lambda n: 8 * np.sin(np.linspace(0, np.pi, n))) <= 1.5


# ---- partial shifts (I2) ----
def _box(m, lo, hi, off, width=15):
    per = np.zeros(m)
    per[lo:hi] = off
    return np.convolve(np.pad(per, width // 2, mode="edge"), np.ones(width) / width, mode="valid")


def test_insert_breakpoints_keeps_the_drawn_curve():
    from annotation_qa.geometry import _dist_to_polyline, insert_breakpoints
    poly = {"vertices": [[0, 0], [200, 0], [400, 200], [600, 200], [800, 200]],
            "types": "LCCLL", "closed": False}
    pts, _ = resample(dense(poly))
    m = len(pts)
    new = insert_breakpoints(poly, _box(m, m // 5, m // 2, 10.0))
    assert len(new["vertices"]) > len(poly["vertices"])
    assert new["types"].count("C") > poly["types"].count("C")  # the Bezier was split
    d = np.asarray(dense(new), float)
    assert _dist_to_polyline(np.asarray(dense(poly), float), d).max() < 0.3
    assert _dist_to_polyline(d, np.asarray(dense(poly), float)).max() < 0.3


def test_partial_shift_on_bezier_lands_on_target():
    from annotation_qa.geometry import SHIFT_RESIDUAL_MAX, partial_shift, shift_residual
    poly = {"vertices": [[0, 0], [300, 0], [500, 300], [800, 300]], "types": "LCCL", "closed": False}
    pts, nrm = resample(dense(poly))
    m = len(pts)
    offs = _box(m, m // 3, 2 * m // 3, -9.0, width=61)
    new = partial_shift(poly, offs)
    assert shift_residual(poly, new, offs) <= SHIFT_RESIDUAL_MAX
    assert np.allclose(new["vertices"][0], [0, 0], atol=1.0)
    assert np.allclose(new["vertices"][-1], [800, 300], atol=1.0)


def test_partial_shift_step_offset_is_not_representable():
    from annotation_qa.geometry import ShiftNotRepresentable, partial_shift
    poly = {"vertices": [[0, 0], [300, 0], [500, 300], [800, 300]], "types": "LCCL", "closed": False}
    m = len(resample(dense(poly))[0])
    offs = 8.0 * (-1.0) ** np.arange(m)  # flips every sample: finer than any Bezier piece
    with pytest.raises(ShiftNotRepresentable, match="redraw by hand"):
        partial_shift(poly, offs)


from annotation_qa.geometry import SHIFT_RESIDUAL_MAX  # noqa: E402


def _target_dev(poly, new, offs):
    from annotation_qa.geometry import shift_residual
    return shift_residual(poly, new, offs)


def test_partial_shift_long_line_middle_only_is_simple():
    from annotation_qa.geometry import partial_shift
    poly = {"vertices": [[0, 0], [600, 0]], "types": "LL", "closed": False}
    m = len(resample(dense(poly))[0])
    offs = _box(m, m // 3, 2 * m // 3, 8.0, width=5)
    new = partial_shift(poly, offs)
    assert len(new["vertices"]) <= 6 and set(new["types"]) == {"L"}
    assert _target_dev(poly, new, offs) <= SHIFT_RESIDUAL_MAX


def test_partial_shift_30_percent_of_long_line():
    from annotation_qa.geometry import partial_shift
    poly = {"vertices": [[0, 0], [3000, 0]], "types": "LL", "closed": False}
    m = len(resample(dense(poly))[0])
    offs = _box(m, int(m * 0.4), int(m * 0.7), -6.0, width=9)
    new = partial_shift(poly, offs)
    assert len(new["vertices"]) <= 6
    assert _target_dev(poly, new, offs) <= SHIFT_RESIDUAL_MAX


def test_partial_shift_curve_vertex_budget():
    from annotation_qa.geometry import partial_shift
    poly = {"vertices": [[0, 0], [300, 0], [500, 300], [800, 300]], "types": "LCCL", "closed": False}
    m = len(resample(dense(poly))[0])
    offs = _box(m, m // 3, 2 * m // 3, -9.0, width=61)
    new = partial_shift(poly, offs)
    assert "C" in new["types"] and len(new["vertices"]) <= len(poly["vertices"]) + 6
    assert _target_dev(poly, new, offs) <= SHIFT_RESIDUAL_MAX


def test_change_anchor():
    from annotation_qa.geometry import change_anchor
    line = {"vertices": [[0, 0], [100, 0]], "types": "LL", "closed": False}
    moved = {"vertices": [[0, 0], [50, 0], [50, 20], [60, 20], [60, 0], [100, 0]],
             "types": "LLLLLL", "closed": False}
    b = {"category": "a", "poly2d": [line]}
    x, y = change_anchor(b, {"category": "a", "poly2d": [moved]})
    assert 50 <= x <= 60 and y == 0
    for a in (None, {"category": "b", "poly2d": [line]}):
        mx, my = change_anchor(b, a)
        assert abs(mx - 50) <= 3 and my == 0
