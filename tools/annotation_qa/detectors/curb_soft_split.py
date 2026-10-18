"""Curb / soft-edge SPLIT detector: road-edge lines that are part curb and part soft edge.

Each open ``curb_road_edge`` / ``without_curb_road_edge`` poly is cut into ~90 px stretches.
The road side is taken for the whole line (lane lines nearby, else the side most stretches
read as road). Per stretch: ``curb_soft._vote`` (near curb band, kerb step, dirt / grass) plus
the across-line appearance (``stretch_features``) go into a logistic STRETCH model that gives
P(curb). Stretches whose smoothed P(opposite class) >= ``t_run`` are opposite; the class
sequence is smoothed (majority over 3), cut into runs, and pieces shorter than
``min_piece_px`` are merged into a neighbour. A candidate split needs a run of the OPPOSITE
class covering >= 25% of the line and >= 200 px while the label's own class keeps >= 200 px
(otherwise a mixed line is left to ``curb_soft``: a whole-label class change). A logistic
LINE model then scores the candidate (run contrast, line length, CAD green length / share,
...: the R34 learnings) and only candidates with score >= ``t_line`` are reported.

Both models are fitted on Roshan's curb_soft_v2 review by ``annotation_qa/eval/split_eval.py``
(``--write-model``) and stored in ``curb_soft_split_model.json`` next to this file.

Fix: ``{"op": "split", "poly_index": i, "cuts": [arc px, ...], "categories": [...]}``; a cut
sits on the boundary between the two stretches where the class changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from annotation_qa.context import FrameContext
from annotation_qa.data import iter_polys
from annotation_qa.detectors.curb_soft import (CURB, SOFT, _cad_for_ctx, _ring_road_side,
                                               _vote)
from annotation_qa.geometry import dense, point_at_arc, resample
from annotation_qa.issues import Issue

ISSUE_TYPE = "curb_soft_split"
STEP = 3.0  # resample step (px) of the stretch points
MODEL_PATH = Path(__file__).with_name("curb_soft_split_model.json")


@dataclass(frozen=True)
class SplitParams:
    stretch_px: float = 90.0
    t_run: float = 0.3  # smoothed P(opposite) at which a stretch counts as opposite
    smooth: int = 3  # majority window over judged stretches (1 = off)
    min_frac: float = 0.25  # an opposite run must cover this share of the line ...
    min_opp_px: float = 200.0  # ... and this many px
    min_own_px: float = 200.0  # the label's own class must keep this many px
    min_piece_px: float = 200.0  # shorter pieces are merged into a neighbour
    min_run_stretches: int = 2  # judged stretches an opposite run needs
    t_line: float = 0.5  # line-model score a candidate needs to be reported
    min_judged: int = 3  # judged stretches a line needs


PARAMS = SplitParams()


OUT_BANDS = ((1, 3), (4, 7), (8, 14), (15, 24), (25, 40))  # px outside the line
ROAD_BANDS = ((-7, -2), (-24, -8))  # px on the road side
PROF_MIN, PROF_MAX = -24, 40
MIN_PROFILE_PTS = 5


def stretch_features(ctx: FrameContext, sub: dict, ring_road: int | None = None) -> dict | None:
    """Across-line appearance of one stretch, oriented so +offset is the non-road side.

    Per band (OUT_BANDS outside, ROAD_BANDS road side): mean gray / sat / green (G-(R+B)/2) /
    brown (R-B) and gray texture; plus the strongest gray rise / dip next to the line and the
    raggedness of the boundary (std over points of where the steepest gray change sits).
    None when fewer than MIN_PROFILE_PTS points have their whole profile inside the ROI."""
    from annotation_qa.detectors.curb_soft import _road_side
    from annotation_qa.imaging import StripStats, _channels, _inside, _local_crop, _sample
    P, N = resample(dense(sub), STEP)
    if P is None or not np.isfinite(P).all():
        return None
    loc = _local_crop(ctx.image, P, max(-PROF_MIN, PROF_MAX) + 2)
    if loc is None:
        return None
    crop, x0, y0 = loc
    gray, sat, r, g, b = _channels(crop)
    planes = {"gray": gray, "sat": sat, "green": g - (r + b) / 2, "brown": r - b}
    off = np.arange(PROF_MIN, PROF_MAX + 1)
    ext = max(-PROF_MIN, PROF_MAX)
    allo = np.arange(-ext, ext + 1)  # either side may be the road
    X = P[:, 0:1] + allo[None, :] * N[:, 0:1]
    Y = P[:, 1:2] + allo[None, :] * N[:, 1:2]
    ok = _inside(ctx.image, X, Y)
    S = {k: _sample(v, X, Y, x0, y0) for k, v in planes.items()}

    def strip(sign):  # 6..20 px strips as in curb_soft
        m = (allo * sign >= 6) & (allo * sign <= 20)
        okm = ok[:, m]
        if okm.sum() < MIN_PROFILE_PTS:
            return None
        v = {k: S[k][:, m][okm] for k in S}
        return StripStats(gray=float(v["gray"].mean()), sat=float(v["sat"].mean()),
                          green=float(v["green"].mean()), brown=float(v["brown"].mean()),
                          texture=float(v["gray"].std()), n=int(okm.sum()))
    left, right = strip(-1), strip(1)
    if left is None or right is None:
        return None
    side_known = 1.0
    side = ring_road if ring_road is not None else _road_side(left, right)
    if side is None:
        side, side_known = (0 if left.gray <= right.gray else 1), 0.0
    sign = 1 if side == 0 else -1  # road on the left (-N): outside is +N
    cols = [int(np.searchsorted(allo, o * sign)) for o in off]
    okp = ok[:, cols]
    rows = okp.all(axis=1)
    if rows.sum() < MIN_PROFILE_PTS:
        return None
    Q = {k: S[k][:, cols][rows] for k in S}
    f = {"side_known": side_known}
    for name, bands in (("o", OUT_BANDS), ("r", ROAD_BANDS)):
        for a, z in bands:
            m = (off >= a) & (off <= z)
            tag = f"{name}{a}_{z}"
            for k in ("gray", "sat", "green", "brown"):
                f[f"{tag}_{k}"] = float(Q[k][:, m].mean())
            f[f"{tag}_tex"] = float(Q["gray"][:, m].std())
    prof = Q["gray"].mean(axis=0)
    i0 = int(np.searchsorted(off, -3))
    near = prof[i0:int(np.searchsorted(off, 12)) + 1]
    d3 = near[3:] - near[:-3]
    f["rise"] = float(d3.max()) if d3.size else 0.0
    f["dip"] = float(near.max() - near.min()) if near.size else 0.0
    gr = np.abs(np.diff(Q["gray"][:, i0:int(np.searchsorted(off, 15)) + 1], axis=1))
    f["edge_pos_std"] = float(np.argmax(gr, axis=1).std())
    f["edge_strength"] = float(gr.max(axis=1).mean())
    f["along_std"] = float(Q["gray"][:, (off >= 1) & (off <= 7)].mean(axis=1).std())
    for k in list(f):
        if k.startswith("o") and k.endswith("_gray"):
            f[k + "_rel"] = f[k] - f["r-7_-2_gray"]
    # mean across-line profiles in 3 px bins (gray relative to the road next to the line)
    sprof = Q["sat"].mean(axis=0)
    road = f["r-7_-2_gray"]
    for a in range(-12, 39, 3):
        m = (off >= a) & (off < a + 3)
        f[f"pg{a}"] = float(prof[m].mean() - road)
        f[f"ps{a}"] = float(sprof[m].mean())
    dprof = np.diff(prof)  # consistent (parallel) edges survive the mean over points
    for a, z in ((0, 6), (6, 15), (15, 39)):
        m = (off[:-1] >= a) & (off[:-1] < z)
        f[f"dpos{a}_{z}"] = float(dprof[m].max())
        f[f"dneg{a}_{z}"] = float(-dprof[m].min())
    return f


PAINT_CATS = ("solid_single_line", "dashed_white_line", "double_yellow_line", "center_turn_lane")
SIDE_SEARCH_PX = 400.0  # lane lines this close to the edge tell which side the road is on


def line_road_side(frame: dict, label: dict, poly: dict) -> int | None:
    """Road side of a whole open edge poly (0 = left / -normal, 1 = right), from the lane
    lines of the frame: the side most of the nearby painted-line samples lie on. A closed poly
    uses the ring orientation. None when no lane line is within SIDE_SEARCH_PX."""
    if poly.get("closed"):
        return _ring_road_side(poly)
    P, N = resample(dense(poly), 15.0)
    if P is None:
        return None
    from scipy.spatial import cKDTree
    Q = []
    for lab in frame.get("labels") or []:
        if lab.get("category") not in PAINT_CATS or lab.get("id") == label.get("id"):
            continue
        for q in lab.get("poly2d") or []:
            d = np.asarray(dense(q), float)
            if len(d) >= 2 and np.isfinite(d).all():
                r, _ = resample(d, 15.0)
                Q.append(r if r is not None else d)
    if not Q:
        return None
    Q = np.vstack(Q)
    tree = cKDTree(P)
    dist, j = tree.query(Q, distance_upper_bound=SIDE_SEARCH_PX)
    ok = np.isfinite(dist)
    if ok.sum() < 3:
        return None
    side = ((Q[ok] - P[j[ok]]) * N[j[ok]]).sum(axis=1)
    w = 1.0 / np.maximum(dist[ok], 10.0)
    score = float((np.sign(side) * w).sum() / w.sum())
    if abs(score) < 0.3:
        return None
    return 1 if score > 0 else 0


VOTE_KEYS = ("vote_curb", "vote_soft", "vote_none", "band", "step", "strength", "label_curb")


def feature_vector(rec: dict, category: str, names: list[str]) -> np.ndarray | None:
    """Model input of one stretch (``names`` from the model file); None when unjudged."""
    f = rec.get("f")
    if not f:
        return None
    extra = {"vote_curb": float(rec["cls"] == "C"), "vote_soft": float(rec["cls"] == "S"),
             "vote_none": float(rec["cls"] is None), "band": float(rec["band"]),
             "step": float(rec["step"]), "strength": float(rec["strength"]),
             "label_curb": float(category == CURB)}
    return np.array([extra[k] if k in extra else f.get(k, 0.0) for k in names], float)


def _majority_side(ctx: FrameContext, subs: list[dict]) -> int | None:
    from annotation_qa.detectors.curb_soft import _road_side
    from annotation_qa.imaging import side_strips
    votes = [0, 0]
    for sub in subs:
        st = side_strips(ctx.image, sub)
        sd = _road_side(*st) if st is not None else None
        if sd is not None:
            votes[sd] += 1
    if votes[0] == votes[1]:
        return None
    return 0 if votes[0] > votes[1] else 1


def stretch_votes(ctx: FrameContext, poly: dict, stretch_px: float = PARAMS.stretch_px,
                  features: bool = False, road_side: int | None = None) -> list[dict]:
    """Per stretch of ~``stretch_px``: {s0, s1, cls ('C'|'S'|None), strength, band, step,
    centre, judged}. Stretches are contiguous (a shared end point); s0/s1 are arc px along
    ``dense(poly)``. A last stretch shorter than a third is merged into the previous one."""
    pts = dense(poly)
    if not np.isfinite(pts).all():
        return []
    P, _ = resample(pts, STEP)
    if P is None:
        return []
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    k = max(int(round(stretch_px / STEP)), 2)
    cuts = list(range(0, len(P) - 1, k))
    if len(cuts) > 1 and (len(P) - 1 - cuts[-1]) < k / 3:
        cuts.pop()
    bounds = cuts + [len(P) - 1]
    subs = [{"vertices": P[a:b + 1].tolist(), "types": "L" * (b + 1 - a), "closed": False}
            for a, b in zip(bounds, bounds[1:])]
    ring_road = _ring_road_side(poly) if poly.get("closed") else road_side
    if ring_road is None:  # no lane lines: the side most stretches read as road
        ring_road = _majority_side(ctx, subs)
    out = []
    for (a, b), sub in zip(zip(bounds, bounds[1:]), subs):
        run = P[a:b + 1]
        rec = {"s0": float(s[a]), "s1": float(s[b]), "centre": [float(v) for v in run[len(run) // 2]],
               "cls": None, "strength": 0.0, "band": False, "step": False, "judged": False}
        v = _vote(ctx, sub, ring_road) if len(run) >= 2 else None
        if v is not None:
            rec["judged"] = True
            rec["strength"] = float(v[1])
            rec["band"], rec["step"] = bool(v[2]["band"]), bool(v[2]["step"])
            rec["cls"] = {CURB: "C", SOFT: "S"}.get(v[0])
        if features and len(run) >= 2:
            rec["f"] = stretch_features(ctx, sub, ring_road)
        out.append(rec)
    return out


def _smooth(cls: list[str], w: int) -> list[str]:
    if w <= 1 or len(cls) < 3:
        return list(cls)
    h = w // 2
    out = []
    for i, c in enumerate(cls):
        win = cls[max(0, i - h):i + h + 1]
        nc, ns = win.count("C"), win.count("S")
        out.append("C" if nc > ns else "S" if ns > nc else c)
    return out


def _merge_same(runs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in runs:
        if out and out[-1]["cls"] == r["cls"]:
            out[-1]["s1"] = r["s1"]
            out[-1]["members"] = out[-1]["members"] + r["members"]
        else:
            out.append({**r, "members": list(r["members"])})
    return out


@lru_cache(maxsize=4)
def load_model(path: str = str(MODEL_PATH)) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _logit(m: dict, X: np.ndarray) -> np.ndarray:
    Z = (X - np.asarray(m["mean"])) / np.asarray(m["std"])
    z = m["w"][0] + Z @ np.asarray(m["w"][1:])
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def stretch_probs(st: list[dict], category: str, model: dict) -> np.ndarray:
    """P(curb) per stretch from the stretch model; NaN for stretches without features."""
    m = model["stretch"]
    out = np.full(len(st), np.nan)
    rows = [(i, feature_vector(r, category, m["names"])) for i, r in enumerate(st)]
    rows = [(i, x) for i, x in rows if x is not None and np.isfinite(x).all()]
    if rows:
        out[[i for i, _ in rows]] = _logit(m, np.array([x for _, x in rows]))
    return out


LINE_KEYS = ("opp_mean", "own_mean", "contrast", "opp_frac", "log_length", "label_curb",
             "cad_known", "cad_log_green", "cad_green_share", "cad_green_mid", "n_runs",
             "p_opp_mean", "p_opp_std")
# R28/R34: CAD soft edge in the frame (>= 600 px of green strokes) but not a mostly-soft
# frame (> 8000 px): where Roshan's mixed lines are
CAD_GREEN_MID = (600.0, 8000.0)


def _cad_feats(cad: dict | None) -> dict:
    if not cad or cad.get("green_len") is None:
        return {"cad_known": 0.0, "cad_log_green": 0.0, "cad_green_share": 0.0,
                "cad_green_mid": 0.0}
    g, r = float(cad.get("green_len") or 0.0), float(cad.get("cad_curb_length") or 0.0)
    return {"cad_known": 1.0, "cad_log_green": float(np.log1p(g)),
            "cad_green_share": g / max(g + r, 1.0),
            "cad_green_mid": float(CAD_GREEN_MID[0] <= g <= CAD_GREEN_MID[1])}


def candidate(st: list[dict], length: float, category: str, pcurb: np.ndarray,
              cad: dict | None = None, p: SplitParams = PARAMS) -> dict | None:
    """Candidate split of one poly from per-stretch P(curb) (before the line gate), or None.

    Returns {"cuts", "categories", "pieces": [{s0, s1, category, n_stretches, p_mean,
    p_margin, n_votes, n_for, n_against, margin, n_band}], "line": {LINE_KEYS...}, "opp_px",
    "own_px"}. A piece's ``p_margin`` is 2 * mean P(its class) - 1 over its judged stretches;
    ``margin`` is the curb_soft vote margin (for - against) / votes inside it."""
    own = "C" if category == CURB else "S"
    opp = "S" if own == "C" else "C"
    pc = np.asarray(pcurb, float)
    idx = [i for i in range(len(st)) if np.isfinite(pc[i])]
    if len(idx) < p.min_judged or length <= 0:
        return None
    popp = (1 - pc) if own == "C" else pc
    v = popp[idx]
    sm = np.array([v[max(0, k - 1):k + 2].mean() for k in range(len(v))])  # mean over 3
    cls = _smooth([opp if x >= p.t_run else own for x in sm], p.smooth)
    if opp not in cls:
        return None
    runs: list[dict] = []
    for j, c in zip(idx, cls):
        if runs and runs[-1]["cls"] == c:
            runs[-1]["members"].append(j)
        else:
            runs.append({"cls": c, "members": [j]})
    for k, r in enumerate(runs):  # stretch over 0..length, gaps of unjudged cut midway
        r["s0"] = 0.0 if k == 0 else 0.5 * (st[runs[k - 1]["members"][-1]]["s1"]
                                            + st[r["members"][0]]["s0"])
        r["s1"] = length if k == len(runs) - 1 else 0.5 * (st[r["members"][-1]]["s1"]
                                                         + st[runs[k + 1]["members"][0]]["s0"])
    for r in runs:  # an opposite run needs enough judged stretches
        if r["cls"] == opp and len(r["members"]) < p.min_run_stretches:
            r["cls"] = own
    runs = _merge_same(runs)
    while len(runs) > 1:  # short pieces join their longer neighbour
        lens = [r["s1"] - r["s0"] for r in runs]
        k = int(np.argmin(lens))
        if lens[k] >= p.min_piece_px:
            break
        j = 1 if k == 0 else k - 1 if k == len(runs) - 1 else \
            (k - 1 if lens[k - 1] >= lens[k + 1] else k + 1)
        runs[k]["cls"] = runs[j]["cls"]
        runs = _merge_same(runs)
    if len(runs) < 2:
        return None
    opp_runs = [r["s1"] - r["s0"] for r in runs if r["cls"] == opp]
    own_px = sum(r["s1"] - r["s0"] for r in runs if r["cls"] == own)
    if max(opp_runs) < max(p.min_frac * length, p.min_opp_px) or own_px < p.min_own_px:
        return None
    pieces = []
    for r in runs:
        mem = [i for i in r["members"] if np.isfinite(pc[i])]
        pk = pc[mem] if r["cls"] == "C" else 1 - pc[mem]
        pm = float(pk.mean()) if len(mem) else 0.5
        n_for = sum(1 for i in r["members"] if st[i]["cls"] == r["cls"])
        n_vote = sum(1 for i in r["members"] if st[i]["cls"] in ("C", "S"))
        pieces.append({"s0": round(float(r["s0"]), 2), "s1": round(float(r["s1"]), 2),
                       "category": CURB if r["cls"] == "C" else SOFT,
                       "n_stretches": len(mem), "p_mean": round(pm, 4),
                       "p_margin": round(2 * pm - 1, 4),
                       # curb_soft votes inside the piece: (for - against) / votes
                       "n_votes": n_vote, "n_for": n_for, "n_against": n_vote - n_for,
                       "margin": round((2 * n_for - n_vote) / n_vote, 4) if n_vote else 0.0,
                       "n_band": sum(1 for i in r["members"] if st[i]["band"])})
    opp_m = [i for r in runs if r["cls"] == opp for i in r["members"] if np.isfinite(pc[i])]
    own_m = [i for r in runs if r["cls"] == own for i in r["members"] if np.isfinite(pc[i])]
    opp_mean = float(popp[opp_m].mean()) if opp_m else 0.5
    own_mean = float(1 - popp[own_m].mean()) if own_m else 0.5
    line = {"opp_mean": opp_mean, "own_mean": own_mean, "contrast": opp_mean + own_mean - 1,
            "opp_frac": float(sum(opp_runs) / length), "log_length": float(np.log(length)),
            "label_curb": float(own == "C"), "n_runs": float(len(runs)),
            "p_opp_mean": float(np.nanmean(popp)), "p_opp_std": float(np.nanstd(popp)),
            **_cad_feats(cad)}
    return {"cuts": [pc_["s0"] for pc_ in pieces[1:]],
            "categories": [pc_["category"] for pc_ in pieces], "pieces": pieces, "line": line,
            "opp_px": float(sum(opp_runs)), "own_px": float(own_px)}


def line_score(cand: dict, model: dict) -> float:
    m = model["line"]
    x = np.array([[cand["line"][k] for k in m["names"]]], float)
    return float(_logit(m, x)[0])


def propose(st: list[dict], length: float, category: str, cad: dict | None = None,
            p: SplitParams = PARAMS, model: dict | None = None,
            pcurb: np.ndarray | None = None) -> dict | None:
    """Reported split for one poly (candidate + line gate), or None. ``pcurb`` overrides the
    stretch model (the evaluation passes out-of-fold probabilities)."""
    model = model or load_model()
    if model is None:
        return None
    if pcurb is None:
        pcurb = stretch_probs(st, category, model)
    cand = candidate(st, length, category, pcurb, cad, p)
    if cand is None:
        return None
    cand["score"] = line_score(cand, model)
    return cand if cand["score"] >= p.t_line else None


def _length(poly: dict) -> float:
    d = np.asarray(dense(poly), float)
    return float(np.linalg.norm(np.diff(d, axis=0), axis=1).sum()) if len(d) > 1 else 0.0


def params_from_model(model: dict | None) -> SplitParams:
    """The operating point stored with the fitted models (else the defaults)."""
    if not model or not model.get("params"):
        return PARAMS
    keep = {k: v for k, v in model["params"].items() if k in SplitParams.__dataclass_fields__}
    return SplitParams(**keep)


def detect_label_polys(ctx: FrameContext, cad: dict | None, p: SplitParams, model: dict):
    """{label_id: (poly_index, proposal, label)} for every edge label with a proposal."""
    best: dict[str, tuple] = {}
    for label, pi, poly in iter_polys(ctx.frame):
        if label.get("category") not in (CURB, SOFT) or poly.get("closed"):
            continue
        if len(poly.get("vertices") or []) < 2:
            continue
        side = line_road_side(ctx.frame, label, poly)
        st = stretch_votes(ctx, poly, p.stretch_px, features=True, road_side=side)
        if not st:
            continue
        prop = propose(st, st[-1]["s1"], label["category"], cad, p, model)
        if prop is None:
            continue
        cur = best.get(label["id"])
        if cur is None or prop["score"] > cur[1]["score"]:
            best[label["id"]] = (pi, prop, label)
    return best


def detect(ctx: FrameContext, p: SplitParams | None = None, model: dict | None = None) -> list[Issue]:
    model = model or load_model()
    if model is None:
        return []
    p = p or params_from_model(model)
    cadref = _cad_for_ctx(ctx)
    frame_name = ctx.frame.get("name", "")
    cad = cadref.info(frame_name) if cadref is not None else None
    issues = []
    for lid, (pi, prop, label) in detect_label_polys(ctx, cad, p, model).items():
        poly = label["poly2d"][pi]
        cat = label["category"]
        words = " | ".join(f"{'curb' if pc['category'] == CURB else 'soft'} "
                           f"{pc['s1'] - pc['s0']:.0f}px" for pc in prop["pieces"])
        issues.append(Issue(
            issue_type=ISSUE_TYPE, export_stem=ctx.export.stem, frame_name=frame_name,
            label_id=lid, category=cat, confidence=round(prop["score"], 4),
            reason=f"{cat} is part curb, part soft edge: split into {words}",
            anchor=tuple(point_at_arc(poly, prop["cuts"][0])),
            fix={"op": "split", "poly_index": pi, "cuts": prop["cuts"],
                 "categories": prop["categories"]},
            hand_fix=False,
            features={"pieces": prop["pieces"], "line": prop["line"], "score": prop["score"],
                      "opp_px": prop["opp_px"], "own_px": prop["own_px"],
                      "length": _length(poly),
                      "min_margin": float(min(pc["margin"] for pc in prop["pieces"])),
                      "cad_soft": (cad or {}).get("soft"),
                      "cad_green_len": (cad or {}).get("green_len"),
                      "stretch_px": p.stretch_px}))
    return issues
