"""Automatic verification of every change in a fix round.

Writes OUT/rounds/<tag>_verify.json = {frame_name: {label_id: {"verdict": "ok"|"check"|"bad",
"reasons": [...], ...metrics}}}, so the reviewer only has to look closely at doubtful ones.
Hand frames appear as {frame_name: {"__hand__": {"verdict": "check", ...}}}.

``shift`` changes are scored on the paint ridge of the part that moved (before vs after) --
road edges on the road-boundary strength of ``edge_position`` instead (``edge_on_paint``
shifts on how much of the moved part is still drawn on paint) -- plus a
neighbour-overlap and a new-kink test; ``set_category`` changes are judged from the
features stored with the issue; deletes / vertex drops are always "check"; ``set_poly``
(self-crossing removal) and ``join_into`` (two overlapping pieces joined into one line,
``_join``) are judged on the geometry alone.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from annotation_qa.data import REPO_ROOT, image_path
from annotation_qa.geometry import (LINE_WIDTH, _dist_to_polyline, dense, doubled_length,
                                      resample, self_crossings)
from annotation_qa.imaging import FrameImage, profile
from annotation_qa.issues import load_issues_tolerant
from annotation_qa.rounds import (VERIFY_META, read_verdicts, round_files, split_changes,
                                  suggest_view)
from annotation_qa.viewb import frames_by_name

HAND = "__hand__"
MOVED_PX = 2.0
OK_SCORE, OK_GAIN, OK_MOVE = 25.0, 15.0, 25.0
BAD_SCORE, BAD_GAIN = 15.0, 5.0
NEIGH_PX, NEIGH_COS, NEIGH_FRAC = 3.0, 0.9, 0.30
KINK_DEG = 35.0
KINK_K = 3  # samples (~9 px) each side of a vertex when measuring a turn
R = 25
EDGE_CATS = ("curb_road_edge", "without_curb_road_edge")
# road-edge shifts (edge_position): boundary strength at the line, FLOOR = 40
EDGE_OK_SCORE, EDGE_OK_GAIN = 40.0, 20.0
EDGE_BAD_SCORE, EDGE_BAD_GAIN = 20.0, 5.0
# edge_on_paint shifts: share of the moved part's stretches still drawn on paint
PAINT_OK_BEFORE, PAINT_OK_AFTER, PAINT_BAD_AFTER = 0.4, 0.2, 0.5


def verdict_path(out: Path, tag: str) -> Path:
    return Path(out) / "rounds" / f"{tag}_verify.json"


def _tangents(nrm: np.ndarray) -> np.ndarray:
    return np.stack([nrm[:, 1], -nrm[:, 0]], axis=1)


def _turns(P: np.ndarray) -> np.ndarray:
    """Turn angle (deg) at every sample, between the chords k samples back and forward."""
    n, k = len(P), KINK_K
    out = np.zeros(n)
    if n < 2 * k + 1:
        return out
    a = P[k:-k] - P[:-2 * k]
    b = P[2 * k:] - P[k:-k]
    na, nb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    cos = (a * b).sum(axis=1) / np.maximum(na * nb, 1e-9)
    out[k:-k] = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    return out


def _dilate(mask: np.ndarray, k: int) -> np.ndarray:
    out = mask.copy()
    for s in range(1, k + 1):
        out[s:] |= mask[:-s]
        out[:-s] |= mask[s:]
    return out


def _score(prof, ridge: np.ndarray, rows: np.ndarray) -> float:
    """nan-mean ridge at offset 0 (+-1 px) over the selected resampled points."""
    cells = ridge[rows][:, R - 1:R + 2]
    if cells.size == 0 or np.isnan(cells).all():
        return float("nan")
    return float(np.nanmean(cells))


def _neighbour_frac(P: np.ndarray, T: np.ndarray, neighbours: list[np.ndarray]) -> float:
    """Share of points within NEIGH_PX of another label's line, tangent aligned."""
    if len(P) == 0:
        return 0.0
    hit = np.zeros(len(P), bool)
    for pts, tans in neighbours:
        d, j = cKDTree(pts).query(P)
        cos = np.abs((T * tans[j]).sum(axis=1))
        hit |= (d <= NEIGH_PX) & (cos >= NEIGH_COS)
    return float(hit.mean())


def _neighbour_lines(frame: dict, label_id: str) -> list[tuple[np.ndarray, np.ndarray]]:
    lines = []
    for lab in frame.get("labels") or []:
        if lab.get("id") == label_id:
            continue
        for poly in lab.get("poly2d") or []:
            pts = dense(poly)
            if not np.isfinite(pts).all():
                continue
            P, N = resample(pts, 1.5)
            if P is not None:
                lines.append((P, _tangents(N)))
    return lines


def _fmt(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.1f}"


def _verdict_shift_poly(img: FrameImage, cat: str, channel: str | None, bpoly: dict, apoly: dict,
                        neighbours, issue_type: str | None = None,
                        frame: dict | None = None) -> dict:
    B, A = np.asarray(dense(bpoly), float), np.asarray(dense(apoly), float)
    Pb, Nb = resample(B)
    Pa, Na = resample(A)
    if Pb is None or Pa is None:
        return {"verdict": "check", "reasons": ["line too short to verify"]}
    moved_b = _dist_to_polyline(Pb, A) > MOVED_PX
    if moved_b.sum() < 2:
        return {"verdict": "check", "reasons": ["no part of the line moved more than 2 px"]}
    nearest = cKDTree(Pb).query(Pa)[1]
    moved_a = moved_b[nearest] | (_dist_to_polyline(Pa, B) > MOVED_PX)
    max_move = float(_dist_to_polyline(Pb[moved_b], A).max())
    if max_move <= LINE_WIDTH:  # R33 safety net: within the training line width
        return {"verdict": "bad", "reasons": ["move within line width"], "max_move": max_move}
    neigh = _neighbour_frac(Pa[moved_a], _tangents(Na)[moved_a], neighbours)
    turn_a = float(_turns(Pa)[_dilate(moved_a, KINK_K)].max())
    turn_b = float(_turns(Pb)[_dilate(moved_b, KINK_K)].max())
    kink = turn_a > KINK_DEG and turn_b <= KINK_DEG
    if cat in EDGE_CATS and issue_type == "edge_on_paint":
        return _verdict_on_paint(img, bpoly, apoly, Pb, Nb, A, moved_b, moved_a, max_move,
                                 neigh, kink, turn_a, turn_b, frame)
    if cat in EDGE_CATS:
        return _verdict_edge(img, bpoly, apoly, moved_b, moved_a, max_move, neigh, kink,
                             turn_a, turn_b)
    from annotation_qa.detectors.position import select_channel
    pb, pa = profile(img, bpoly, R=R), profile(img, apoly, R=R)
    if pb is None or pa is None:
        return {"verdict": "check", "reasons": ["no usable image data under the line"]}
    if channel not in ("white", "yellow"):
        channel, _ = select_channel(cat, pa)
    rb = pb.yellow if channel == "yellow" else pb.white
    ra = pa.yellow if channel == "yellow" else pa.white
    before, after = _score(pb, rb, moved_b), _score(pa, ra, moved_a)
    m = {"channel": channel, "before_score": before, "after_score": after,
         "max_move": max_move, "neighbour_frac": neigh, "turn_after": turn_a,
         "turn_before": turn_b, "moved_len": float(moved_b.sum() * 3.0)}
    if np.isnan(after) or np.isnan(before):
        return {"verdict": "check", "reasons": ["paint score unavailable (outside ROI)"], **m}
    bad, reasons = [], []
    if after < BAD_SCORE:
        bad.append(f"after paint score {_fmt(after)} < {BAD_SCORE:.0f}: still not on paint")
    if after < before + BAD_GAIN:
        bad.append(f"after {_fmt(after)} not clearly better than before {_fmt(before)} "
                   f"(< +{BAD_GAIN:.0f})")
    if neigh >= NEIGH_FRAC:
        bad.append(f"{neigh:.0%} of the moved part lies on another label")
    if kink:
        bad.append(f"new {turn_a:.0f} deg turn in the moved part (before {turn_b:.0f})")
    if bad:
        return {"verdict": "bad", "reasons": bad, **m}
    ok = (after >= OK_SCORE and after >= before + OK_GAIN and max_move <= OK_MOVE)
    reasons = [f"paint score {_fmt(before)} -> {_fmt(after)} ({channel})",
               f"max move {max_move:.0f} px", f"neighbour overlap {neigh:.0%}",
               f"max turn {turn_a:.0f} deg"]
    if not ok:
        if after < OK_SCORE:
            reasons.insert(0, f"after score {_fmt(after)} < {OK_SCORE:.0f}")
        if after < before + OK_GAIN:
            reasons.insert(0, f"gain {_fmt(after - before)} < {OK_GAIN:.0f}")
        if max_move > OK_MOVE:
            reasons.insert(0, f"moved {max_move:.0f} px > {OK_MOVE:.0f}")
    return {"verdict": "ok" if ok else "check", "reasons": reasons, **m}


def _verdict_edge(img, bpoly, apoly, moved_b, moved_a, max_move, neigh, kink,
                  turn_a, turn_b) -> dict:
    """A shifted road edge is scored on the road-boundary strength at the line
    (``edge_position.line_score``) of the part that moved, before vs after; the road side
    of the BEFORE line is kept for the after line."""
    from annotation_qa.detectors.edge_position import line_score
    before, side = line_score(img, bpoly, moved_b)
    after, _ = line_score(img, apoly, moved_a, side=side)
    m = {"channel": "edge", "before_score": before, "after_score": after,
         "max_move": max_move, "neighbour_frac": neigh, "turn_after": turn_a,
         "turn_before": turn_b, "moved_len": float(moved_b.sum() * 3.0)}
    if np.isnan(after) or np.isnan(before):
        return {"verdict": "check", "reasons": ["edge score unavailable (outside ROI)"], **m}
    bad = []
    if after < EDGE_BAD_SCORE:
        bad.append(f"after edge score {_fmt(after)} < {EDGE_BAD_SCORE:.0f}: "
                   "still not on the boundary")
    if after < before + EDGE_BAD_GAIN:
        bad.append(f"after {_fmt(after)} not clearly better than before {_fmt(before)} "
                   f"(< +{EDGE_BAD_GAIN:.0f})")
    if neigh >= NEIGH_FRAC:
        bad.append(f"{neigh:.0%} of the moved part lies on another label")
    if kink:
        bad.append(f"new {turn_a:.0f} deg turn in the moved part (before {turn_b:.0f})")
    if bad:
        return {"verdict": "bad", "reasons": bad, **m}
    ok = after >= EDGE_OK_SCORE and after >= before + EDGE_OK_GAIN and max_move <= OK_MOVE
    reasons = [f"edge score {_fmt(before)} -> {_fmt(after)}", f"max move {max_move:.0f} px",
               f"neighbour overlap {neigh:.0%}", f"max turn {turn_a:.0f} deg"]
    if not ok:
        if after < EDGE_OK_SCORE:
            reasons.insert(0, f"after edge score {_fmt(after)} < {EDGE_OK_SCORE:.0f}")
        if after < before + EDGE_OK_GAIN:
            reasons.insert(0, f"gain {_fmt(after - before)} < {EDGE_OK_GAIN:.0f}")
        if max_move > OK_MOVE:
            reasons.insert(0, f"moved {max_move:.0f} px > {OK_MOVE:.0f}")
    return {"verdict": "ok" if ok else "check", "reasons": reasons, **m}


def _verdict_on_paint(img, bpoly, apoly, Pb, Nb, A, moved_b, moved_a, max_move, neigh, kink,
                      turn_a, turn_b, frame=None) -> dict:
    """An edge_on_paint shift: the share of the moved part's stretches that are still a
    road edge drawn on paint (``edge_on_paint.line_score``), before vs after, and the
    move must go AWAY from the road (onto the curb), with the before line's road side."""
    from annotation_qa.detectors.edge_on_paint import line_score
    before, ctx = line_score(img, bpoly, moved_b, frame=frame)
    side = ctx[0] if ctx else None
    after, _ = line_score(img, apoly, moved_a, ctx=ctx if side is not None else None)
    u_move = float("nan")
    if side is not None:
        near = cKDTree(A).query(Pb[moved_b])[1]
        along = ((A[near] - Pb[moved_b]) * Nb[moved_b]).sum(axis=1)
        u_move = float(np.median(along)) * (1 if side == 0 else -1)
    m = {"channel": "on_paint", "before_score": before, "after_score": after,
         "u_move": u_move, "max_move": max_move, "neighbour_frac": neigh,
         "turn_after": turn_a, "turn_before": turn_b, "moved_len": float(moved_b.sum() * 3.0)}
    if np.isnan(after) or np.isnan(before) or np.isnan(u_move):
        return {"verdict": "check", "reasons": ["on-paint score unavailable"], **m}
    bad = []
    if u_move <= 0:
        bad.append(f"moved {abs(u_move):.1f} px toward the road, not onto the curb")
    if after >= PAINT_BAD_AFTER:
        bad.append(f"{after:.0%} of the moved part still on paint")
    if after >= before:
        bad.append(f"on-paint share {before:.0%} -> {after:.0%}: not better")
    if neigh >= NEIGH_FRAC:
        bad.append(f"{neigh:.0%} of the moved part lies on another label")
    if kink:
        bad.append(f"new {turn_a:.0f} deg turn in the moved part (before {turn_b:.0f})")
    if bad:
        return {"verdict": "bad", "reasons": bad, **m}
    ok = after <= PAINT_OK_AFTER and before >= PAINT_OK_BEFORE and max_move <= OK_MOVE
    reasons = [f"on-paint share {before:.0%} -> {after:.0%}",
               f"moved {u_move:.1f} px away from the road", f"max move {max_move:.0f} px",
               f"neighbour overlap {neigh:.0%}", f"max turn {turn_a:.0f} deg"]
    return {"verdict": "ok" if ok else "check", "reasons": reasons, **m}


_RANK = {"bad": 0, "check": 1, "ok": 2}


def _combine(parts: list[dict]) -> dict:
    worst = min(parts, key=lambda p: _RANK[p["verdict"]])
    if len(parts) == 1:
        return worst
    out = dict(worst)
    out["reasons"] = [f"poly {i}: {r}" for i, p in enumerate(parts) for r in p["reasons"]]
    return out


def _shift(img, cat, issue, rec, frame) -> dict:
    bp, ap = rec["before"].get("poly2d") or [], rec["after"].get("poly2d") or []
    if len(bp) != len(ap) or not bp:
        return {"verdict": "check", "reasons": ["polygon structure changed"]}
    neigh = _neighbour_lines(frame, rec.get("label_id", ""))
    channel = (issue.features or {}).get("channel") if issue else None
    parts = []
    for b, a in zip(bp, ap):
        if b == a:
            continue
        parts.append(_verdict_shift_poly(img, cat, channel, b, a, neigh,
                                         issue.issue_type if issue else None, frame))
    return _combine(parts) if parts else {"verdict": "check", "reasons": ["geometry unchanged"]}


# calibrated 2026-10-18 on Roshan's curb_soft_v2 review (see learn/report.md)
CAD_GREEN_OK_MAX = 5000  # soft->curb "ok" needs cad_green_len <= this


def _set_category(issue, rec) -> dict:
    to = rec["after"]["category"]
    if issue is None:
        return {"verdict": "check", "reasons": ["no stored issue to judge the category change"]}
    f = issue.features or {}
    if issue.issue_type != "curb_soft":
        return {"verdict": "check", "reasons": [f"{issue.issue_type} category change: judge by eye"]}
    nb, share, nv, cad = f.get("near_band_votes", 0), f.get("share", 0.0), f.get("n_votes", 0), \
        f.get("cad_soft")
    facts = f"near_band_votes {nb}, share {share:.2f}, n_votes {nv}, cad_soft {cad}"
    if to == "curb_road_edge":
        if nb >= 15 and f.get("outside_texture", float("inf")) <= 17:
            return {"verdict": "check", "reasons": [
                "long uniform bright strip — may be a concrete road or sidewalk, not a curb",
                facts]}
        ok = (nb >= 3 and share >= 0.9) or cad is False
    else:
        ok = nv >= 8 and cad is not False
    if to == "curb_road_edge" and ok:
        gl = f.get("cad_green_len") or 0
        if gl > CAD_GREEN_OK_MAX:
            return {"verdict": "check", "reasons": [
                f"frame has a lot of CAD soft edge (green {gl} px) — check carefully", facts]}
    return {"verdict": "ok" if ok else "check",
            "reasons": [("strong support: " if ok else "weak support: ") + facts]}


# calibrated 2026-10-18 on Roshan's curb_soft_v2 review (annotation_qa/eval/split_eval.py):
# a split is "ok" only when every piece's vote margin reaches this, else "check"
SPLIT_OK_MARGIN = 0.6
SPLIT_OK_VOTES = 2  # voting stretches each piece needs for "ok"


def _split(issue, rec) -> dict:
    """A curb_soft_split change: judged from the per-piece votes stored with the issue."""
    if issue is None or not (issue.features or {}).get("pieces"):
        return {"verdict": "check", "reasons": ["split: no stored votes to judge it by"]}
    pieces = issue.features["pieces"]
    weak = [pc for pc in pieces
            if pc.get("margin", 0.0) < SPLIT_OK_MARGIN or pc.get("n_votes", 0) < SPLIT_OK_VOTES]
    facts = [f"{'curb' if pc['category'] == 'curb_road_edge' else 'soft'} "
             f"{pc['s1'] - pc['s0']:.0f} px: margin {pc.get('margin', 0.0):.2f} "
             f"({pc.get('n_for', 0)}/{pc.get('n_votes', 0)} votes)" for pc in pieces]
    if weak:
        return {"verdict": "check", "reasons": [
            f"weak piece votes (margin < {SPLIT_OK_MARGIN} or < {SPLIT_OK_VOTES} votes)"] + facts}
    return {"verdict": "ok", "reasons": ["every piece strongly supported"] + facts}


# set_poly (shape self-crossing removal), calibrated 2026-10-18 on Roshan's own join fixes
# (283 MgLLWDKf5dOIzRXB, 937 l4eFvSQVYDndAHBN: removed loop/overshoot sticks out <= 0.4 px):
# "ok" only when what was removed is invisible at the training width and the rest is unchanged
SET_POLY_OK_EXTENT = 2 * LINE_WIDTH  # px: removed loop / overshoot sticks out at most this
SET_POLY_OK_FOLLOW = LINE_WIDTH / 2  # px: the new line stays this close to the old one


def _set_poly(issue, rec) -> dict:
    """A geometry replacement (shape self-crossing removal): "bad" when the new line still
    crosses itself; "ok" when the new line follows the old one within SET_POLY_OK_FOLLOW
    everywhere and the removed part sticks out at most SET_POLY_OK_EXTENT; else "check"
    (the shape may have changed -- confirm by eye)."""
    bp, ap = rec["before"].get("poly2d") or [], rec["after"].get("poly2d") or []
    if len(bp) != len(ap) or not bp:
        return {"verdict": "check", "reasons": ["polygon structure changed"]}
    pi = (issue.fix or {}).get("poly_index") if issue and issue.fix else None
    idx = [pi] if pi is not None and pi < len(bp) else [k for k in range(len(bp))
                                                        if bp[k] != ap[k]]
    if not idx:
        return {"verdict": "check", "reasons": ["geometry unchanged"]}
    worst = {"follow": 0.0, "extent": 0.0}
    reasons, crossing, ring = [], False, False
    for k in idx:
        b, a = bp[k], ap[k]
        B, A = np.asarray(dense(b), float), np.asarray(dense(a), float)
        if len(B) < 2 or len(A) < 2:
            return {"verdict": "check", "reasons": ["line too short to verify"]}
        Pa, _ = resample(A, 1.0)
        Pb, _ = resample(B, 1.0)
        Pa = A if Pa is None else Pa
        Pb = B if Pb is None else Pb
        worst["follow"] = max(worst["follow"], float(_dist_to_polyline(Pa, B).max()))
        worst["extent"] = max(worst["extent"], float(_dist_to_polyline(Pb, A).max()))
        crossing |= bool(self_crossings(a))
        ring |= bool(a.get("closed")) and not b.get("closed")
    m = {"max_follow": worst["follow"], "removed_extent": worst["extent"]}
    if issue is not None and (issue.features or {}).get("removed_len") is not None:
        m["removed_len"] = issue.features["removed_len"]
    if crossing:
        return {"verdict": "bad", "reasons": ["the new line still crosses itself"], **m}
    facts = [("ends joined into a ring; " if ring else "")
             + f"removed part sticks out {worst['extent']:.1f} px"
             + (f" ({m['removed_len']:.0f} px of line)" if "removed_len" in m else ""),
             f"rest of the line within {worst['follow']:.1f} px of the old one"]
    if worst["follow"] > SET_POLY_OK_FOLLOW:
        reasons.append(f"new line strays {worst['follow']:.1f} px > {SET_POLY_OK_FOLLOW:.0f} "
                       "from the old one: confirm the shape")
    if worst["extent"] > SET_POLY_OK_EXTENT:
        reasons.append(f"removed loop sticks out {worst['extent']:.0f} px > "
                       f"{SET_POLY_OK_EXTENT:.0f}: confirm the shape")
    return {"verdict": "check" if reasons else "ok", "reasons": reasons + facts, **m}


# join_into (duplicates overlapping pieces joined into one line): "ok" only when the joined
# line is simple and follows both pieces within half the training line width away from the
# splice, and the splice bridges at most 2x the line width
JOIN_OK_FOLLOW = LINE_WIDTH / 2  # px
JOIN_OK_GAP = 2 * LINE_WIDTH  # px between the two pieces at a splice
JOIN_SPLICE_R = 2 * LINE_WIDTH  # px around a splice point that is not compared
JOIN_OK_DOUBLED = 2 * LINE_WIDTH  # px of new doubled-back line


def _samples(poly: dict) -> np.ndarray:
    d = np.asarray(dense(poly), float)
    P, _ = resample(d, 1.0)
    return d if P is None else P


def _join(issue, rec) -> dict:
    """A join_into change (A deleted, B's poly replaced by the joined line): "bad" when the
    joined line crosses itself more than B did; "ok" when, outside JOIN_SPLICE_R of the
    splice points, the joined line stays within JOIN_OK_FOLLOW of A or B and every point of
    A and B is within JOIN_OK_FOLLOW of it, the splice gap is at most JOIN_OK_GAP and it does
    not double back; else "check"."""
    j = rec.get("join") or {}
    A, Bb, Ba = rec.get("before") or {}, j.get("before") or {}, j.get("after") or {}
    pi = int(j.get("poly_index") or 0)
    bp, ap = Bb.get("poly2d") or [], Ba.get("poly2d") or []
    if len(bp) != len(ap) or not 0 <= pi < len(bp):
        return {"verdict": "check", "reasons": ["join: polygon structure changed"]}
    b, new = bp[pi], ap[pi]
    polys_a = [p for p in A.get("poly2d") or [] if len(p.get("vertices") or []) >= 2]
    Pn, Pb = _samples(new), _samples(b)
    Pa = np.vstack([_samples(p) for p in polys_a]) if polys_a else np.zeros((0, 2))
    if len(Pn) < 2 or len(Pb) < 2:
        return {"verdict": "check", "reasons": ["line too short to verify"]}
    splices = np.asarray(j.get("splices") or [], float).reshape(-1, 2)

    def outside(P):
        if not len(splices) or not len(P):
            return P
        d = np.linalg.norm(P[:, None, :] - splices[None], axis=2).min(axis=1)
        return P[d > JOIN_SPLICE_R]

    Dn = np.asarray(dense(new), float)
    lines = [np.asarray(dense(b), float)] + [np.asarray(dense(p), float) for p in polys_a]
    on, ob, oa = outside(Pn), outside(Pb), outside(Pa)
    f_new = float(np.min([_dist_to_polyline(on, L) for L in lines], axis=0).max()) \
        if len(on) else 0.0
    f_b = float(_dist_to_polyline(ob, Dn).max()) if len(ob) else 0.0
    f_a = float(_dist_to_polyline(oa, Dn).max()) if len(oa) else 0.0
    gap = max([float(g) for g in j.get("gaps") or []] or [0.0])
    nx, bx = len(self_crossings(new)), len(self_crossings(b))
    dbl = max(0.0, doubled_length(new) - doubled_length(b))
    m = {"max_follow_new": f_new, "max_follow_b": f_b, "max_follow_a": f_a,
         "splice_gap": gap, "doubled": dbl, "mode": j.get("mode")}
    if nx > bx:
        return {"verdict": "bad", "reasons": ["the joined line crosses itself"], **m}
    mode = {"extend": "extended with the piece beyond the overlap",
            "ring": "closed into a ring", "contained": "kept as it is (the piece lies on it)"}
    facts = [f"{j.get('target')} {mode.get(j.get('mode'), 'joined')}; piece deleted",
             f"joined line within {f_new:.1f} px of the pieces; pieces within "
             f"{max(f_a, f_b):.1f} px of it (outside the splice)",
             f"splice gap {gap:.1f} px"]
    reasons = []
    if max(f_new, f_a, f_b) > JOIN_OK_FOLLOW:
        reasons.append(f"joined line and pieces differ by {max(f_new, f_a, f_b):.1f} px > "
                       f"{JOIN_OK_FOLLOW:.0f}: confirm which line is right")
    if gap > JOIN_OK_GAP:
        reasons.append(f"splice gap {gap:.1f} px > {JOIN_OK_GAP:.0f}: confirm the splice")
    if dbl > JOIN_OK_DOUBLED:
        reasons.append(f"joined line doubles back over {dbl:.0f} px: confirm the shape")
    if bx:
        reasons.append(f"{j.get('target')} already crossed itself ({bx}x): a shape issue")
    return {"verdict": "check" if reasons else "ok", "reasons": reasons + facts, **m}


def verify_frame_change(img, issue, rec, frame, label_id) -> dict:
    before, after = rec.get("before"), rec.get("after")
    if rec.get("join"):
        return _join(issue, rec)
    if before is None or after is None:
        return {"verdict": "check", "reasons": ["label deleted: confirm by eye"]}
    if isinstance(after, list):  # a split: "after" lists the pieces
        return _split(issue, rec)
    op = (issue.fix or {}).get("op") if issue else None
    if op is None:
        op = "set_category" if before.get("category") != after.get("category") else "shift"
    if op == "set_category":
        return _set_category(issue, rec)
    if op == "drop_vertices":
        return {"verdict": "check", "reasons": ["vertices dropped: confirm by eye"]}
    if op == "set_poly":
        return _set_poly(issue, rec)
    if op == "delete":
        return {"verdict": "check", "reasons": ["label deleted: confirm by eye"]}
    return _shift(img, after.get("category"), issue, {**rec, "label_id": label_id}, frame)


def verify_combined(img, issues_by_type: dict, rec: dict, frame, label_id) -> dict:
    """A change made of several fixes (``issue_types`` / ``ops`` are parallel lists) gets the
    WORST verdict of its parts: geometry parts are scored as a plain shift, the class part
    by the set_category rules."""
    parts = []
    for t, op in zip(rec["issue_types"], rec["ops"]):
        issue = issues_by_type.get(t)
        if op == "split" or isinstance(rec.get("after"), list):
            v = _split(issue, rec)
        elif op == "set_category":
            v = _set_category(issue, rec)
        elif op == "shift":
            v = _shift(img, rec["after"].get("category"), issue, {**rec, "label_id": label_id},
                       frame)
        elif op == "drop_vertices":
            v = {"verdict": "check", "reasons": ["vertices dropped: confirm by eye"]}
        elif op == "set_poly":
            v = _set_poly(issue, rec)
        elif op == "join_into":
            v = _join(issue, rec)
        else:
            v = {"verdict": "check", "reasons": ["label deleted: confirm by eye"]}
        parts.append({**v, "reasons": [f"{t} {op}: {r}" for r in v["reasons"]]})
    worst = min(parts, key=lambda p: _RANK[p["verdict"]])
    return {**worst, "reasons": [r for p in parts for r in p["reasons"]]}


def verify_round(out: Path, rnd: dict, repo_root: Path | None = None) -> dict:
    """Verdicts of one round (see module doc); decodes each frame image once."""
    out = Path(out)
    repo_root = Path(repo_root or REPO_ROOT)
    changes, suggest = split_changes(json.loads(Path(rnd["changes"]).read_text()))
    items = frames_by_name(rnd["items"])
    issues = {}
    types = {rnd["type"]}
    for labels in changes.values():
        for rec in labels.values():
            types.update(rec.get("issue_types") or [])
    for t in sorted(types):
        ip = out / f"issues_{t}.jsonl"
        if ip.exists():
            issues.update({i.key: i for i in load_issues_tolerant(ip)})
    res: dict[str, dict] = {}
    for fname, labels in changes.items():
        frame = items.get(fname, (0, {}))[1]
        img = None
        try:
            for lid, rec in labels.items():
                if suggest:  # score the PROPOSED geometry as if it had been applied
                    if rec.get("hand"):
                        res.setdefault(fname, {})[lid] = {"verdict": "check", "reasons": [
                            "hand fix, no proposal: " + str(rec.get("reason") or "draw by hand")]}
                        continue
                    rec = suggest_view(rec)
                its = rec.get("issue_types") or [rnd["type"]]
                by_t = {t: issues.get(f"{rnd['stem']}|{fname}|{lid}|{t}") for t in its}
                issue = by_t[its[0]]
                if img is None and rec.get("after") and rec.get("before") and frame.get("url"):
                    img = FrameImage(image_path(frame, Path(repo_root) / "local-data"),
                                     out / "roi_cache")
                try:
                    if len(rec.get("ops") or []) > 1 and rec.get("before") and rec.get("after"):
                        v = verify_combined(img, by_t, rec, frame, lid)
                    else:
                        v = verify_frame_change(img, issue, rec, frame, lid)
                except Exception as e:  # noqa: BLE001 - one bad change must not stop the round
                    v = {"verdict": "check", "reasons": [f"verification failed: {type(e).__name__}: {e}"]}
                res.setdefault(fname, {})[lid] = v
        finally:
            if img is not None:
                img.release()
    for fname in items:
        if fname not in changes:
            res.setdefault(fname, {})[HAND] = {"verdict": "check", "reasons": ["draw by hand"]}
    return res


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else round(float(o), 3)
    if isinstance(o, np.integer):
        return int(o)
    return o


def write_verify(out: Path, tag: str, verdicts: dict, changes_sha1: str | None = None) -> Path:
    """Write the verdicts with ``__meta__.changes_sha1`` (the changes.json they judge), so
    View B and ``exclude`` ignore them once the round is rebuilt."""
    p = verdict_path(out, tag)
    if changes_sha1 is None:
        changes_sha1 = hashlib.sha1(
            (Path(out) / "rounds" / f"{tag}_changes.json").read_bytes()).hexdigest()
    body = {**_clean(verdicts), VERIFY_META: {"changes_sha1": changes_sha1}}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(body))
    os.replace(tmp, p)
    return p


def counts(verdicts: dict) -> dict[str, int]:
    c = {"ok": 0, "check": 0, "bad": 0}
    for fname, labels in verdicts.items():
        if fname == VERIFY_META:
            continue
        for lid, v in labels.items():
            if lid != HAND:
                c[v["verdict"]] += 1
    return c


def run(out: Path, tag: str | None, all_rounds: bool, repo_root: Path | None = None) -> dict[str, dict]:
    """Verify one round (``tag``) or every round without a current verdict file (none, or
    one written for an older changes.json); {tag: counts}."""
    out = Path(out)
    rounds = round_files(out)
    if not all_rounds:
        rounds = [r for r in rounds if r["tag"] == tag]
        if not rounds:
            raise SystemExit(f"no such round: {tag}")
    else:
        rounds = [r for r in rounds if read_verdicts(out, r["tag"]) is None]
    summary = {}
    for r in rounds:
        sha = hashlib.sha1(Path(r["changes"]).read_bytes()).hexdigest()  # what is judged
        v = verify_round(out, r, repo_root)
        write_verify(out, r["tag"], v, sha)
        summary[r["tag"]] = counts(v)
        print(f"{r['tag']}: {summary[r['tag']]}", flush=True)
    return summary
