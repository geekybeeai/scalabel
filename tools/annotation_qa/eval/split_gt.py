"""Ground truth for ``curb_soft_split``: which road-edge lines Roshan cut into pieces of
different classes in his curb_soft_v2 review, and where.

pre-review = OUT/rounds/<stem>_fix_curb_soft_v2_items.json (the tool's proposal; curb_soft
changed only categories), reviewed = the latest OUT/reviewed/<tag>.reviewed_*.json (by mtime).
Every pre-review edge poly is resampled at 3 px; each sample takes the category of the
nearest reviewed edge label within COVER_PX that is either the same id or a NEW id (a piece
he cut or redrew). A change he reverted in View B (applied at merge time) counts as the
before category. Runs shorter than MIN_RUN_PX are merged into a neighbour (junction noise);
a line "needs split" when its covered samples map to >= 2 label ids and both classes keep
at least MIN_CLASS_PX. Cut positions = arc length (px along the pre-review dense curve)
midway between two runs of different class.

Read-only on OUT.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from annotation_qa.data import REPO_ROOT
from annotation_qa.geometry import _arc, _dist_to_polyline, _resample, dense

OUT = REPO_ROOT / "local-data/items/Full_Verfication/qa_2026_10_17"
SUFFIX = "_fix_curb_soft_v2"
CURB, SOFT = "curb_road_edge", "without_curb_road_edge"
EDGE = (CURB, SOFT)
STEP = 3.0
COVER_PX = 8.0
MIN_RUN_PX = 60.0
MIN_CLASS_PX = 150.0


@dataclass
class GTLine:
    stem: str
    frame: str
    label_id: str
    poly_index: int
    category: str  # pre-review category
    length: float  # px along the dense curve
    closed: bool
    s: np.ndarray  # arc positions of the 3 px samples
    cats: np.ndarray  # per sample: 0 curb, 1 soft, -1 not covered by a reviewed edge line
    runs: list = field(default_factory=list)  # [(s0, s1, category)] after smoothing
    cuts: list = field(default_factory=list)  # arc positions between runs of different class
    n_ids: int = 0  # distinct reviewed label ids covering the line
    needs_split: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.stem, self.frame, self.label_id)


def stems(out: Path = OUT) -> list[str]:
    return sorted(Path(f).name[: -len(SUFFIX + "_items.json")]
                  for f in glob.glob(str(Path(out) / "rounds" / f"*{SUFFIX}_items.json")))


def _frames(path) -> dict[str, dict]:
    d = json.loads(Path(path).read_text())
    fr = d["frames"] if isinstance(d, dict) else d
    return {f["name"]: f for f in fr}


def items_path(stem: str, out: Path = OUT) -> Path:
    return Path(out) / "rounds" / f"{stem}{SUFFIX}_items.json"


def reviewed_path(stem: str, out: Path = OUT) -> Path:
    cands = glob.glob(str(Path(out) / "reviewed" / f"{stem}{SUFFIX}.reviewed_*.json"))
    return Path(max(cands, key=os.path.getmtime))


def _polys(label: dict) -> list[dict]:
    return [p for p in (label.get("poly2d") or []) if len(p.get("vertices") or []) >= 2]


def smooth_runs(s: np.ndarray, cats: np.ndarray, min_run: float = MIN_RUN_PX) -> list:
    """[(s0, s1, cat)] over the covered samples, short runs merged into a neighbour,
    stretched to cover 0..s[-1] (gaps split at their midpoint)."""
    ok = cats >= 0
    if not ok.any():
        return []
    ss, cc = s[ok], cats[ok]
    runs = []  # [start idx, end idx (incl), cat] in the covered sequence
    a = 0
    for i in range(1, len(cc) + 1):
        if i == len(cc) or cc[i] != cc[a]:
            runs.append([a, i - 1, int(cc[a])])
            a = i
    while len(runs) > 1:  # merge the shortest too-short run into its longer neighbour
        lens = [ss[r[1]] - ss[r[0]] + STEP for r in runs]
        k = int(np.argmin(lens))
        if lens[k] >= min_run:
            break
        if k == 0:
            j = 1
        elif k == len(runs) - 1:
            j = k - 1
        else:
            j = k - 1 if lens[k - 1] >= lens[k + 1] else k + 1
        lo, hi = min(k, j), max(k, j)
        runs[lo] = [runs[lo][0], runs[hi][1], runs[j][2]]
        del runs[hi]
        merged = []  # neighbours with the same class join
        for r in runs:
            if merged and merged[-1][2] == r[2]:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        runs = merged
    out = []
    for k, (a, b, c) in enumerate(runs):
        s0 = 0.0 if k == 0 else 0.5 * (ss[runs[k - 1][1]] + ss[a])
        s1 = float(s[-1]) if k == len(runs) - 1 else 0.5 * (ss[b] + ss[runs[k + 1][0]])
        out.append((float(s0), float(s1), CURB if c == 0 else SOFT))
    return out


def run_cuts(runs: list) -> list[float]:
    return [r[0] for r in runs[1:]]


def build(out: Path = OUT, only_stems: list[str] | None = None) -> list[GTLine]:
    out = Path(out)
    try:
        reverts = json.loads((out / "reverts.json").read_text())
    except (OSError, ValueError):
        reverts = {}
    lines: list[GTLine] = []
    for stem in only_stems or stems(out):
        tag = stem + SUFFIX
        pre_f, rev_f = _frames(items_path(stem, out)), _frames(reviewed_path(stem, out))
        try:
            changes = json.loads((out / "rounds" / f"{tag}_changes.json").read_text())
        except (OSError, ValueError):
            changes = {}
        rtag = reverts.get(tag) or {}
        for fn, pf in pre_f.items():
            rf = rev_f.get(fn)
            if rf is None:
                continue
            pre = {l["id"]: l for l in pf.get("labels") or []}
            rev = {l["id"]: l for l in rf.get("labels") or []}
            new_ids = [i for i in rev if i not in pre and rev[i].get("category") in EDGE]
            new_dense = {i: [np.asarray(dense(p), float) for p in _polys(rev[i])] for i in new_ids}
            for lid, lab in pre.items():
                if lab.get("category") not in EDGE:
                    continue
                cands = list(new_ids)
                if lid in rev and rev[lid].get("category") in EDGE:
                    cands = [lid] + cands
                cand_dense = dict(new_dense)
                cand_cat = {i: rev[i]["category"] for i in new_ids}
                if lid in cands:
                    cand_dense[lid] = [np.asarray(dense(p), float) for p in _polys(rev[lid])]
                    cat = rev[lid]["category"]
                    rec = (changes.get(fn) or {}).get(lid)
                    if lid in (rtag.get(fn) or {}) and rec and isinstance(rec.get("after"), dict) \
                            and rec["after"].get("category") == cat and rec.get("before"):
                        cat = rec["before"]["category"]  # View B revert, applied at merge
                    cand_cat[lid] = cat
                for pi, poly in enumerate(_polys(lab)):
                    d = np.asarray(dense(poly), float)
                    r = _resample(d, STEP)
                    if r is None or not np.isfinite(d).all():
                        continue
                    s, P, _ = r
                    best_d = np.full(len(P), np.inf)
                    best_i = np.full(len(P), -1)
                    for k, cid in enumerate(cands):
                        for L in cand_dense[cid]:
                            if len(L) < 2:
                                continue
                            dd = _dist_to_polyline(P, L)
                            upd = dd < best_d
                            best_d[upd], best_i[upd] = dd[upd], k
                    cov = best_d <= COVER_PX
                    cats = np.full(len(P), -1)
                    for k, cid in enumerate(cands):
                        m = cov & (best_i == k)
                        cats[m] = 0 if cand_cat[cid] == CURB else 1
                    runs = smooth_runs(s, cats)
                    ids = {cands[k] for k in set(best_i[cov].tolist())} if cov.any() else set()
                    per = {CURB: 0.0, SOFT: 0.0}
                    for s0, s1, c in runs:
                        per[c] += s1 - s0
                    g = GTLine(stem, fn, lid, pi, lab["category"], float(_arc(d)[-1]),
                               bool(poly.get("closed")), s, cats, runs, run_cuts(runs), len(ids))
                    g.needs_split = (len(ids) >= 2 and len(runs) >= 2
                                     and min(per.values()) >= MIN_CLASS_PX)
                    if not g.needs_split:
                        g.cuts = []
                    lines.append(g)
    return lines


def label_truth(lines: list[GTLine]) -> dict[tuple[str, str, str], bool]:
    t: dict[tuple[str, str, str], bool] = {}
    for g in lines:
        t[g.key] = t.get(g.key, False) or g.needs_split
    return t


def main() -> None:
    lines = build()
    t = label_truth(lines)
    pos = [g for g in lines if g.needs_split]
    print(f"edge polys {len(lines)}, labels {len(t)}, needs split {sum(t.values())} labels "
          f"({len(pos)} polys), cuts {sum(len(g.cuts) for g in pos)}")
    by = {}
    for g in pos:
        k = (g.category, tuple(r[2] for r in g.runs))
        by[k] = by.get(k, 0) + 1
    for k, n in sorted(by.items(), key=lambda x: -x[1])[:12]:
        print(n, k[0], "->", " | ".join("C" if c == CURB else "S" for c in k[1]))


if __name__ == "__main__":
    main()
