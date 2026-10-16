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
