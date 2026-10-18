"""set_poly / join_into types strings must be valid Scalabel curves (review 2, minor), and the
geometry that makes them always produces valid ones (fuzz regression of the review probe)."""

import random

import numpy as np
import pytest

from annotation_qa.fix import _apply_op, _set_poly, types_problem
from annotation_qa.geometry import dense, join_overlapping, remove_self_crossings


@pytest.mark.parametrize("types,n,closed", [
    ("LL", 2, False), ("LCCL", 4, False), ("LCCLCCL", 7, False), ("LLCC", 4, True),
    ("LCCLL", 5, False),
])
def test_valid_types(types, n, closed):
    assert types_problem(types, n, closed) is None


@pytest.mark.parametrize("types,n,closed,why", [
    ("LL", 3, False, "3 vertices but 2 types"),
    ("CL", 2, False, "bad types"),
    ("LXL", 3, False, "bad types"),
    ("LCL", 3, False, "C run of 1"),
    ("LCCCL", 5, False, "C run of 3"),
    ("LCCCCL", 6, False, "C run of 4"),
    ("LLCC", 4, False, "no end point"),
    ("", 0, False, "bad types"),
])
def test_invalid_types(types, n, closed, why):
    assert why in types_problem(types, n, closed)


def _verts(n):
    return [[float(i), float(i % 2)] for i in range(n)]


@pytest.mark.parametrize("types", ["LCL", "LCCCL", "LLCC"])
def test_set_poly_rejects_bad_curve_runs(types):
    poly = {"vertices": [[0, 0], [9, 0]], "types": "LL", "closed": False}
    with pytest.raises(ValueError, match="set_poly"):
        _set_poly(poly, {"op": "set_poly", "vertices": _verts(len(types)), "types": types,
                         "closed": False})


def test_join_into_rejects_bad_types():
    label = {"id": "a", "category": "x", "poly2d": [{"vertices": [[0, 0], [9, 0]], "types": "LL",
                                                     "closed": False}]}
    with pytest.raises(ValueError, match="C run of 1"):
        _apply_op(label, {"op": "join_into", "target": "b", "vertices": _verts(3), "types": "LCL",
                          "closed": False})
    assert _apply_op(label, {"op": "join_into", "target": "b", "vertices": _verts(4),
                             "types": "LCCL", "closed": False}) is False


def _rand_poly(rng, n_seg, closed=False):
    pts, types = [[rng.uniform(0, 300), rng.uniform(0, 300)]], "L"
    for _ in range(n_seg):
        if rng.random() < 0.4:
            pts += [[rng.uniform(0, 300), rng.uniform(0, 300)] for _ in range(3)]
            types += "CCL"
        else:
            pts.append([rng.uniform(0, 300), rng.uniform(0, 300)])
            types += "L"
    return {"vertices": pts, "types": types, "closed": closed}


def _check(poly):
    assert types_problem(poly["types"], len(poly["vertices"]), bool(poly.get("closed"))) is None
    assert np.isfinite(np.asarray(poly["vertices"], float)).all()


def test_fuzz_uncross_and_join_emit_valid_types():
    rng = random.Random(7)
    made = {"uncross": 0, "join": 0}
    for _ in range(600):
        p = _rand_poly(rng, rng.randint(2, 7), closed=rng.random() < 0.2)
        r = remove_self_crossings(p)
        if r:
            made["uncross"] += 1
            _check(r[0])
        b = _rand_poly(rng, rng.randint(2, 6))
        d = dense(b)
        if len(d) < 10:
            continue
        i0 = rng.randint(0, len(d) // 2)
        i1 = min(len(d) - 1, i0 + rng.randint(5, len(d)))
        seg = d[i0:i1 + 1:max(1, (i1 - i0) // 4)] + rng.uniform(-1, 1)
        ext = seg[-1] + np.array([rng.uniform(-60, 60), rng.uniform(-60, 60)])
        verts = [list(map(float, v)) for v in seg] + [list(map(float, ext))]
        a = {"vertices": verts[::-1] if rng.random() < 0.5 else verts,
             "types": "L" * len(verts), "closed": False}
        new, _ = join_overlapping(a, b)
        if new is not None:
            made["join"] += 1
            _check(new)
    assert made["uncross"] > 10 and made["join"] > 10  # the fuzz exercised both
