"""Duplicate / overlap detector: labels drawn on top of each other in one frame.

A label's points are all its polys' resampled points (3 px). overlap(A, B) is
the fraction of A's samples within 4 px of B AND running along it (tangents
within ~20 deg), so a stub or chevron that merely touches or crosses a long
line is not an overlap. For each pair the shorter label A
(by total length; ties by id) carries the issue, so a pair is reported once:

- same category, overlap >= 0.8      -> exact duplicate, delete A
- different category, overlap >= 0.8 -> two classes on one feature, hand fix
- same category, 0.3 <= overlap < 0.8 -> overlapping pieces: auto-joined into the other
  label (``join_into``) when ``geometry.join_overlapping`` can splice them into one simple
  line, else a hand fix

A label that pairs with several others keeps its most severe pair (kind, then
overlap); the rest are listed in ``features["other_pairs"]``.

Overlapping pieces (Roshan, after calibrating them 14/14 real: "Use auto join for fixing such
cases"): the fix ``{"op": "join_into", "target": B, "poly_index", "vertices", "types",
"closed"}`` sets B's poly to the joined line and deletes A (the issue label) -- B keeps its
curve and id, A's part beyond the overlap is spliced onto B's end (or, when A bridges both of
B's ends, B is closed into a ring; when A lies wholly on B, B is kept as it is). A fork, pieces
overlapping the wrong way, or a result that would cross itself stay a hand fix.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from annotation_qa.context import FrameContext
from annotation_qa.geometry import _join_runs, dense, join_overlapping, length, resample
from annotation_qa.issues import Issue

NEAR = 4.0  # px
PARALLEL_COS = 0.94  # |cos| of the angle between tangents: within ~20 deg counts as along
FULL = 0.8
PARTIAL = 0.3
BBOX_PAD = 4.0
SEVERITY = {"exact_duplicate": 3, "two_classes": 2, "overlapping_pieces": 1}
CONF = {"exact_duplicate": 0.9, "two_classes": 0.8, "overlapping_pieces": 0.6}
REASON = {"exact_duplicate": "duplicate of {o} ({p:.0%} overlap); delete the shorter",
          "two_classes": "two classes on one feature: also {o} ({oc}, {p:.0%} overlap)",
          "overlapping_pieces": "overlapping pieces; join by hand with {o} ({p:.0%} overlap)"}
JOIN_TEXT = {"extend": "auto-join: extend {o} with the part beyond the overlap",
             "ring": "auto-join: close {o} into a ring with this piece",
             "contained": "auto-join: lies on {o}; delete it"}


def _label_points(label: dict):
    chunks, norms, total = [], [], 0.0
    for poly in label.get("poly2d") or []:
        if not poly.get("vertices"):
            continue
        pts = dense(poly)
        total += length(pts)
        P, N = resample(pts)
        if P is not None:
            chunks.append(P)
            norms.append(N)
    if not chunks:
        return None
    P = np.vstack(chunks)
    return {"label": label, "P": P, "N": np.vstack(norms), "len": total, "tree": cKDTree(P),
            "lo": P.min(axis=0) - BBOX_PAD, "hi": P.max(axis=0) + BBOX_PAD}


def _join_fix(short: dict, other: dict) -> tuple[dict | None, dict, str | None]:
    """(join_into fix, features, reason) for an overlapping_pieces pair, or (None, features,
    None) when ``join_overlapping`` refuses (the reason is in features["join"])."""
    polys = [p for p in short.get("poly2d") or [] if p.get("vertices")]
    if len(polys) != 1:
        return None, {"join": "refused: the shorter label has several polys"}, None
    a = polys[0]
    best = None
    for k, b in enumerate(other.get("poly2d") or []):
        if len(b.get("vertices") or []) < 2:
            continue
        an = _join_runs(a, b) if not b.get("closed") else None
        along = sum(r["a1"] - r["a0"] for r in an["runs"]) if an else 0.0
        if best is None or along > best[0]:
            best = (along, k, b)
    if best is None:
        return None, {"join": "refused: the other label has no line"}, None
    _, k, b = best
    new, info = join_overlapping(a, b)
    if new is None:
        return None, {"join": f"refused: {info}"}, None
    fix = {"op": "join_into", "target": other["id"], "poly_index": k,
           "vertices": new["vertices"], "types": new["types"], "closed": bool(new["closed"])}
    feats = {"join": info["mode"], "join_splices": info["splices"], "join_gaps": info["gaps"],
             "join_added_len": info["added_len"], "join_dropped_len": info.get("dropped_len")}
    return fix, feats, JOIN_TEXT[info["mode"]].format(o=other["id"])


def keep_issue(issue: Issue) -> bool:
    """Calibrated rule on a stored duplicates issue: every issue is kept (28/30 real)."""
    return True


def _kind(same_cat: bool, ov: float) -> str | None:
    if ov >= FULL:
        return "exact_duplicate" if same_cat else "two_classes"
    if same_cat and ov >= PARTIAL:
        return "overlapping_pieces"
    return None


def detect(ctx: FrameContext) -> list[Issue]:
    items = [x for x in (_label_points(l) for l in ctx.frame.get("labels") or []) if x]
    stem, fname = ctx.export.stem, ctx.frame.get("name", "")
    cands: dict[str, list[dict]] = {}
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if (a["lo"] > b["hi"]).any() or (b["lo"] > a["hi"]).any():
                continue
            # short = the label that carries the issue
            short, long_ = (a, b) if (a["len"], a["label"]["id"]) <= (b["len"], b["label"]["id"]) \
                else (b, a)
            d, idx = long_["tree"].query(short["P"], distance_upper_bound=NEAR)
            near = np.isfinite(d)
            along = np.zeros(len(near), bool)
            along[near] = np.abs(np.einsum(
                "ij,ij->i", short["N"][near], long_["N"][idx[near]])) >= PARALLEL_COS
            ov = float(along.mean())
            sl, ll = short["label"], long_["label"]
            kind = _kind(sl["category"] == ll["category"], ov)
            if kind is None:
                continue
            cands.setdefault(sl["id"], []).append(
                {"kind": kind, "overlap": ov, "short": short, "other": ll, "other_len": long_["len"]})
    issues = []
    for lid, cs in cands.items():
        cs.sort(key=lambda c: (-SEVERITY[c["kind"]], -c["overlap"], c["other"]["id"]))
        best, rest = cs[0], cs[1:]
        sl, ol = best["short"]["label"], best["other"]
        feats = {"kind": best["kind"], "overlap": best["overlap"],
                 "length": best["short"]["len"], "other_length": best["other_len"],
                 "other_label_id": ol["id"], "other_category": ol["category"],
                 "other_pairs": [{"label_id": c["other"]["id"], "category": c["other"]["category"],
                                  "kind": c["kind"], "overlap": c["overlap"]} for c in rest]}
        P = best["short"]["P"]
        anchor = tuple(float(v) for v in P[len(P) // 2])
        reason = REASON[best["kind"]].format(o=ol["id"], oc=ol["category"], p=best["overlap"])
        fix = {"op": "delete"} if best["kind"] == "exact_duplicate" else None
        if best["kind"] == "overlapping_pieces":
            fix, jf, how = _join_fix(sl, ol)
            feats.update(jf)
            if fix is not None:
                reason = f"overlapping pieces ({best['overlap']:.0%} overlap); {how}"
                if jf["join_splices"]:
                    anchor = tuple(float(v) for v in jf["join_splices"][0])
        issues.append(Issue(
            issue_type="duplicates", export_stem=stem, frame_name=fname, label_id=sl["id"],
            category=sl["category"], confidence=CONF[best["kind"]], reason=reason,
            anchor=anchor, fix=fix, hand_fix=fix is None,
            related_label_ids=[ol["id"]], features=feats))
    return issues
