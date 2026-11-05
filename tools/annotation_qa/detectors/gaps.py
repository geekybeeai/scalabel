"""Gap detector: one line labelled as two pieces with a small break between them.

Map QA checks a line's geometry for gaps as well as overlaps and self-crossings; the AI base
reproduces such breaks as broken curbs and stripes. Two open polys of the SAME category form a
gap when an end of one faces an end of the other:

- the ends are 0 .. GAP_MAX px apart;
- the pieces leave their ends in opposite directions (end tangents within ~20 deg of
  opposite, measured over the last TANGENT_LEN px), and the step from one end to the other
  runs along them (lateral offset at most LATERAL_MAX px);

Each end pairs with at most one other end (closest first). The SHORTER label of a pair carries
the issue; a label with gaps at both ends carries its closest one (the others are listed in
``features["other_gaps"]`` and come back on the next scan, after a merge).

The fix joins the pieces into one line: ``{"op": "join_into", "target": the longer label,
"poly_index", "vertices", "types", "closed": False}`` -- the target keeps its id and direction;
the piece is turned to meet it and joined across the gap by one straight edge, and the piece
label is deleted (the same op as the duplicates "overlapping pieces" join). A carrier with
several polys, or a joined line that would cross itself, stays a hand fix.

Features for calibration: gap_px, lateral_px, angle_deg, the categories of other labels that
cross the gap (``crossed_by``: a lane line broken by a curb is an intersection, not a gap), and
for lane-line classes the paint ridge on the gap vs on the pieces' ends (paint continuing across
the gap suggests the break is a labelling slip; no paint suggests a real break in the marking).

Pre-filter (``keep_issue``), from looking at all 476 gaps of the 2026-10-17 working copies:
most breaks are not mistakes. Under 8 px the two pieces practically touch -- harmless at a 4 px
line width, so not worth a review; a gap another class crosses is an intersection or a
crosswalk; a gap running outside the image's valid area (ROI) is where the image ends; a break
in a lane line with no paint across it is a real break in the marking (crosswalk, median nose,
parking aisle); any break of 40 px or more is a crosswalk, driveway, gore or median end
(crosswalk stripes are paint, so the paint test alone keeps them). What is left are
likely omissions: paint continuing across a lane-line gap, and short curb / edge breaks (e.g. a
curb hidden under a parked car). ``detect_all`` returns every gap, ``detect`` the kept ones.
Not calibrated yet: the YES/NO sample decides whether these rules are right.
"""

from __future__ import annotations

import math

import numpy as np

from annotation_qa.context import FrameContext
from annotation_qa.geometry import dense, length, self_crossings
from annotation_qa.imaging import _inside, profile
from annotation_qa.issues import Issue

GAP_MAX = 60.0  # px between the facing ends
LATERAL_MAX = 6.0  # px off the line of the pieces
FACING_COS = 0.94  # end tangents within ~20 deg of opposite
TANGENT_LEN = 15.0  # px of line used for an end's direction
LANE_CLASSES = {"solid_single_line", "dashed_white_line", "double_yellow_line",
                "center_turn_lane", "continuous_white_line"}
YELLOW_CLASSES = {"double_yellow_line", "center_turn_lane"}


MIN_REVIEW_GAP = 8.0  # px: smaller breaks are harmless at a 4 px line width
MAX_REVIEW_GAP = 40.0  # px: a longer break is a crosswalk, driveway, gore or median end
PAINT_RATIO = 0.5  # gap paint >= this x the pieces' paint: the marking continues
MIN_END_PAINT = 5.0  # the pieces themselves must show paint


def keep_issue(issue: Issue) -> bool:
    """Pre-filter rule (not calibrated yet): likely omissions only -- see the module notes."""
    f = issue.features or {}
    if not MIN_REVIEW_GAP <= f.get("gap_px", 0) < MAX_REVIEW_GAP or f.get("crossed_by"):
        return False
    if f.get("bridge_in_roi", 1.0) < 1.0:
        return False
    if issue.category in LANE_CLASSES:
        gp, ep = f.get("gap_paint"), f.get("end_paint")
        return gp is not None and ep is not None and ep >= MIN_END_PAINT and gp >= PAINT_RATIO * ep
    return True


def _end(P: np.ndarray, which: str) -> tuple[np.ndarray, np.ndarray] | None:
    """(point, outward unit direction) of an end of dense polyline P."""
    Q = P if which == "end" else P[::-1]
    seg = np.diff(Q, axis=0)
    L = np.hypot(seg[:, 0], seg[:, 1])
    if L.sum() < 1e-6:
        return None
    cum = np.cumsum(L[::-1])  # length from the end backwards
    k = int(np.searchsorted(cum, TANGENT_LEN))
    back = Q[max(len(Q) - 2 - k, 0)]
    d = Q[-1] - back
    n = np.hypot(*d)
    return (Q[-1], d / n) if n > 1e-6 else None


def _turn(poly: dict) -> dict:
    """The same line drawn from its other end (types reversed: a Bezier "LCCL" stays valid)."""
    return {"vertices": [list(v) for v in poly["vertices"][::-1]],
            "types": (poly.get("types") or "L" * len(poly["vertices"]))[::-1],
            "closed": False}


def joined_poly(target: dict, target_end: str, piece: dict, piece_end: str) -> dict:
    """``target`` and ``piece`` as one open line across the gap, in ``target``'s direction:
    ``target_end`` / ``piece_end`` ("start" | "end") are the ends that face each other."""
    t = {"vertices": [list(v) for v in target["vertices"]],
         "types": target.get("types") or "L" * len(target["vertices"]), "closed": False}
    if target_end == "end":  # target ... gap ... piece: the piece must START at the gap
        p = piece if piece_end == "start" else _turn(piece)
        return {"vertices": t["vertices"] + [list(v) for v in p["vertices"]],
                "types": t["types"] + (p.get("types") or "L" * len(p["vertices"])), "closed": False}
    p = piece if piece_end == "end" else _turn(piece)  # piece ... gap ... target
    return {"vertices": [list(v) for v in p["vertices"]] + t["vertices"],
            "types": (p.get("types") or "L" * len(p["vertices"])) + t["types"], "closed": False}


def _segments_cross(p1, p2, q1, q2) -> bool:
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
    d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
    return d1 * d2 < 0 and d3 * d4 < 0


def _crossed_by(a: np.ndarray, b: np.ndarray, others: list[tuple[str, np.ndarray]]) -> list[str]:
    cats = set()
    lo, hi = np.minimum(a, b) - 1, np.maximum(a, b) + 1
    for cat, P in others:
        if (P.max(axis=0) < lo).any() or (P.min(axis=0) > hi).any():
            continue
        if any(_segments_cross(a, b, P[k], P[k + 1]) for k in range(len(P) - 1)):
            cats.add(cat)
    return sorted(cats)


def _paint(ctx: FrameContext, a: np.ndarray, b: np.ndarray, ends: list[np.ndarray],
           yellow: bool) -> dict:
    """Paint ridge (the marking brighter than both sides) on the gap and on the pieces' last
    20 px. None when the image cannot be sampled there."""
    def ridge(p, q):
        if np.hypot(*(q - p)) < 9:
            return None
        prof = profile(ctx.image, {"vertices": [list(map(float, p)), list(map(float, q))],
                                   "types": "LL", "closed": False}, R=6)
        if prof is None:
            return None
        s = prof.yellow if yellow else prof.white
        mid = s[:, 4:9]  # offsets -2 .. 2
        v = np.nanmax(mid, axis=1)
        v = v[np.isfinite(v)]
        return float(np.median(v)) if len(v) else None
    gap = ridge(a, b)
    on = [x for x in (ridge(p, q) for p, q in ends) if x is not None]
    return {"gap_paint": gap, "end_paint": float(np.median(on)) if on else None}


def detect(ctx: FrameContext) -> list[Issue]:
    return [i for i in detect_all(ctx) if keep_issue(i)]


def _in_roi(ctx: FrameContext, a: np.ndarray, b: np.ndarray) -> float | None:
    """Fraction of the gap (sampled every 2 px) inside the image's valid area."""
    n = max(2, int(np.hypot(*(b - a)) // 2) + 1)
    t = np.linspace(0, 1, n)
    xs, ys = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
    try:
        return float(_inside(ctx.image, xs, ys).mean())
    except Exception:  # noqa: BLE001 -- no image / ROI: not known
        return None


def detect_all(ctx: FrameContext) -> list[Issue]:
    """Every gap, before the ``keep_issue`` pre-filter."""
    stem, fname = ctx.export.stem, ctx.frame.get("name", "")
    ends, lines, lengths, npolys = [], [], {}, {}
    for lab in ctx.frame.get("labels") or []:
        polys = [p for p in lab.get("poly2d") or [] if len(p.get("vertices") or []) >= 2]
        npolys[lab["id"]] = len(polys)
        total = 0.0
        for k, poly in enumerate(lab.get("poly2d") or []):
            if len(poly.get("vertices") or []) < 2:
                continue
            P = np.asarray(dense(poly), float)
            if not np.isfinite(P).all() or len(P) < 2:
                continue
            total += length(P)
            lines.append((lab["id"], lab.get("category"), P))
            if poly.get("closed"):
                continue
            for which in ("start", "end"):
                e = _end(P, which)
                if e is not None:
                    ends.append({"label": lab, "k": k, "which": which, "p": e[0], "d": e[1], "P": P})
        lengths[lab["id"]] = total
    # candidate pairs: same category, facing, in line, close
    pairs = []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            a, b = ends[i], ends[j]
            if a["label"]["id"] == b["label"]["id"] or a["label"].get("category") != b["label"].get("category"):
                continue
            v = b["p"] - a["p"]
            gap = float(np.hypot(*v))
            if gap > GAP_MAX or float(np.dot(a["d"], b["d"])) > -FACING_COS:
                continue
            axis = (a["d"] - b["d"]) / np.hypot(*(a["d"] - b["d"]))  # mean direction A -> B
            along = float(np.dot(v, axis))
            lateral = float(abs(axis[0] * v[1] - axis[1] * v[0]))
            if lateral > LATERAL_MAX or along < -LATERAL_MAX:
                continue
            angle = math.degrees(math.acos(max(-1.0, min(1.0, -float(np.dot(a["d"], b["d"]))))))
            pairs.append((gap, i, j, lateral, angle))
    pairs.sort()
    used, chosen = set(), []
    for gap, i, j, lateral, angle in pairs:
        if i in used or j in used:
            continue
        used.update((i, j))
        chosen.append((gap, ends[i], ends[j], lateral, angle))
    by_carrier: dict[str, list] = {}
    for gap, a, b, lateral, angle in chosen:
        la, lb = a["label"], b["label"]
        short, long_ = (a, b) if (lengths[la["id"]], str(la["id"])) <= (lengths[lb["id"]], str(lb["id"])) else (b, a)
        by_carrier.setdefault(short["label"]["id"], []).append((gap, short, long_, lateral, angle))
    issues = []
    for lid, cs in by_carrier.items():
        cs.sort(key=lambda c: c[0])
        gap, short, long_, lateral, angle = cs[0]
        sl, ll = short["label"], long_["label"]
        cat = sl.get("category")
        others = [(c, P) for (i_, c, P) in lines if i_ not in (sl["id"], ll["id"])]
        pa, pb = short["p"], long_["p"]
        feats = {"gap_px": gap, "lateral_px": lateral, "angle_deg": angle,
                 "length": lengths[sl["id"]], "other_length": lengths[ll["id"]],
                 "other_label_id": ll["id"], "crossed_by": _crossed_by(pa, pb, others),
                 "bridge_in_roi": _in_roi(ctx, pa, pb),
                 "other_gaps": [{"label_id": c[2]["label"]["id"], "gap_px": c[0]} for c in cs[1:]],
                 "join": "gap", "join_splices": [[float(v) for v in long_["p"]], [float(v) for v in short["p"]]],
                 "join_gaps": [gap], "join_added_len": gap}
        if cat in LANE_CLASSES:
            def tail(e):
                P = e["P"] if e["which"] == "end" else e["P"][::-1]
                cum = np.r_[0, np.cumsum(np.hypot(*np.diff(P[::-1], axis=0).T))]
                k = int(np.searchsorted(cum, 20.0))
                return P[::-1][min(k, len(P) - 1)], P[-1]
            feats.update(_paint(ctx, pa, pb, [tail(short), tail(long_)], cat in YELLOW_CLASSES))
        fix = None
        if npolys.get(sl["id"], 0) != 1:
            feats["join_refused"] = "the shorter label has several polys"
        else:
            target_poly = ll["poly2d"][long_["k"]]
            piece_poly = sl["poly2d"][short["k"]]
            new = joined_poly(target_poly, long_["which"], piece_poly, short["which"])
            if len(self_crossings(new)) > len(self_crossings(target_poly)):
                feats["join_refused"] = "the joined line would cross itself"
            else:
                fix = {"op": "join_into", "target": ll["id"], "poly_index": long_["k"],
                       "vertices": new["vertices"], "types": new["types"], "closed": False}
        mid = (pa + pb) / 2
        where = f" crossed by {', '.join(feats['crossed_by'])}" if feats["crossed_by"] else ""
        reason = (f"{gap:.0f} px gap to {ll['id']} (same class){where}; "
                  + ("auto-join the two pieces into one line" if fix else f"join by hand ({feats['join_refused']})"))
        issues.append(Issue(
            issue_type="gaps", export_stem=stem, frame_name=fname, label_id=sl["id"],
            category=cat, confidence=round(0.9 - 0.4 * min(1.0, gap / GAP_MAX), 3), reason=reason,
            anchor=(float(mid[0]), float(mid[1])), fix=fix, hand_fix=fix is None,
            related_label_ids=[ll["id"]], features=feats))
    return issues
