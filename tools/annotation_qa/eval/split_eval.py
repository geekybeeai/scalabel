"""Evaluate (and fit) ``curb_soft_split`` against Roshan's curb_soft_v2 review (split_gt.py).

    cd tools && python3 -m annotation_qa.eval.split_eval [--workers 6] [--rebuild] [--write-model]

Step 1 (slow, cached): stretch votes + features of every pre-review edge poly at several
stretch lengths, computed on the PRE-REVIEW frames (the round items files) and their images.
Cache: OUT/eval/curb_soft_split_votes.pkl (--rebuild recomputes it).
Step 2: out-of-fold P(curb) per stretch (stretch model, 5 folds by frame).
Step 3: per setting, candidates from those probabilities; the line model is fitted per fold on
the other folds' candidates; then, per line-score threshold: label-level precision / recall
of "needs split", cut-position error of the true positives (px along the line), class
agreement of the proposed pieces with his classes (by length), and the piece-margin check
that verify uses. Writes OUT/eval/curb_soft_split_report.md (and prints it).
--write-model refits both models on everything and writes
detectors/curb_soft_split_model.json with the chosen operating point.
"""

from __future__ import annotations

import argparse
import multiprocessing
import pickle
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from annotation_qa.data import REPO_ROOT, image_path
from annotation_qa.detectors import curb_soft_split as css
from annotation_qa.detectors.curb_soft_split import (LINE_KEYS, PARAMS, SplitParams, candidate,
                                                     line_road_side, stretch_votes)
from annotation_qa.eval import split_gt
from annotation_qa.eval import split_model as sm
from annotation_qa.eval.split_gt import CURB, OUT, SOFT

SIZES = (60.0, 90.0, 150.0)
T_LINES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
MIN_PROPOSED = 15  # an operating point with fewer proposals is too noisy to pick


def _job(args):
    stem, frame, polys, out, sizes = args
    from annotation_qa.context import FrameContext
    from annotation_qa.data import Export
    from annotation_qa.imaging import FrameImage
    img = FrameImage(image_path(frame, REPO_ROOT / "local-data"), Path(out) / "roi_cache")
    try:
        img.bgr  # noqa: B018
    except FileNotFoundError as e:
        return stem, frame["name"], None, str(e)
    ctx = FrameContext(Export(Path(f"{stem}.json"), [frame], {}), frame, img)
    labs = {l["id"]: l for l in frame.get("labels") or []}
    res = {}
    try:
        for lid, pi in polys:
            poly = labs[lid]["poly2d"][pi]
            side = line_road_side(frame, labs[lid], poly)
            res[(lid, pi)] = {sz: stretch_votes(ctx, poly, sz, features=True, road_side=side)
                              for sz in sizes}
    finally:
        img.release()
    return stem, frame["name"], res, None


def build_cache(lines, out: Path, path: Path, workers: int, sizes=SIZES) -> dict:
    jobs = {}
    for g in lines:
        jobs.setdefault((g.stem, g.frame), []).append((g.label_id, g.poly_index))
    frames = {s: split_gt._frames(split_gt.items_path(s, out)) for s in {k[0] for k in jobs}}
    args = [(s, frames[s][f], polys, str(out), sizes) for (s, f), polys in sorted(jobs.items())]
    cache: dict = {}
    pool = multiprocessing.get_context("spawn").Pool(workers) if workers > 1 else None
    try:
        it = pool.imap_unordered(_job, args) if pool else map(_job, args)
        for n, (stem, fname, res, err) in enumerate(it, 1):
            if err:
                print(f"[{n}/{len(args)}] {fname}: {err}", flush=True)
                continue
            for (lid, pi), v in res.items():
                cache[(stem, fname, lid, pi)] = v
            if n % 20 == 0 or n == len(args):
                print(f"[{n}/{len(args)}] votes cached", flush=True)
    finally:
        if pool:
            pool.close()
            pool.join()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(cache))
    return cache


def stretch_oof(lines, cache, size):
    """(names, rows, {line index: P(curb) array}) with out-of-fold probabilities."""
    names = sm.feature_names(cache, size)
    rows = sm.stretch_table(lines, cache, size, names)
    oof = sm.oof_probs(rows)
    probs = {}
    for li, g in enumerate(lines):
        st = (cache.get((g.stem, g.frame, g.label_id, g.poly_index)) or {}).get(size) or []
        probs[li] = np.array([oof.get((li, i), np.nan) for i in range(len(st))])
    return names, rows, probs


def candidates(lines, cache, cad, probs, p: SplitParams) -> dict:
    """{label key: (line index, candidate)} keeping the best-contrast poly per label."""
    out = {}
    for li, g in enumerate(lines):
        st = (cache.get((g.stem, g.frame, g.label_id, g.poly_index)) or {}).get(p.stretch_px)
        if not st or g.closed:
            continue
        c = candidate(st, st[-1]["s1"], g.category, probs[li], cad.get(g.frame), p)
        if c is not None and (g.key not in out or
                              c["line"]["contrast"] > out[g.key][1]["line"]["contrast"]):
            out[g.key] = (li, c)
    return out


def line_oof(lines, cands: dict) -> dict:
    """{label key: out-of-fold line score} (line model fitted on the other folds)."""
    keys = list(cands)
    if not keys:
        return {}
    truth = split_gt.label_truth(lines)
    X = np.array([[cands[k][1]["line"][n] for n in LINE_KEYS] for k in keys], float)
    y = np.array([truth[k] for k in keys], float)
    fold = np.array([sm.fold_of(k[0], k[1]) for k in keys])
    score = np.zeros(len(keys))
    for f in range(sm.N_FOLDS):
        tr, te = fold != f, fold == f
        if not te.any():
            continue
        if y[tr].sum() == 0 or y[tr].sum() == tr.sum():
            score[te] = y[tr].mean()
            continue
        m = sm.fit(X[tr], y[tr], sm.L2)
        score[te] = sm.predict(m, X[te])
    return dict(zip(keys, score))


def _piece_cat(pieces, s):
    out = np.empty(len(s), dtype=object)
    for pc in pieces:
        out[(s >= pc["s0"]) & (s <= pc["s1"])] = pc["category"]
    return out


def _verify_ok(c, ok_margin, ok_votes) -> bool:
    return all(pc["margin"] >= ok_margin and pc["n_votes"] >= ok_votes for pc in c["pieces"])


def metrics(lines, cands, scores, t_line, p: SplitParams, ok_margin: float | None = None,
            ok_votes: int | None = None) -> dict:
    from annotation_qa.verify import SPLIT_OK_MARGIN, SPLIT_OK_VOTES
    ok_margin = SPLIT_OK_MARGIN if ok_margin is None else ok_margin
    ok_votes = SPLIT_OK_VOTES if ok_votes is None else ok_votes
    truth = split_gt.label_truth(lines)
    n_pos = sum(truth.values())
    tp = fp = 0
    cut_err, agree_tp, total_tp, agree_after, agree_before, total = [], 0.0, 0.0, 0.0, 0.0, 0.0
    ok_n = ok_tp = 0
    for k, (li, c) in cands.items():
        if scores[k] < t_line:
            continue
        g = lines[li]
        t = truth[k]
        tp += t
        fp += not t
        if _verify_ok(c, ok_margin, ok_votes):
            ok_n += 1
            ok_tp += t
        cov = g.cats >= 0
        if cov.any():
            gt = np.where(g.cats[cov] == 0, CURB, SOFT)
            pcat = _piece_cat(c["pieces"], g.s[cov])
            a, b = float((pcat == gt).sum()), float((gt == g.category).sum())
            agree_after += a
            agree_before += b
            total += float(cov.sum())
            if t:
                agree_tp += a
                total_tp += float(cov.sum())
        if t and g.needs_split:
            cut_err += [min(abs(x - y) for y in c["cuts"]) for x in g.cuts]
    ce = np.asarray(cut_err) if cut_err else np.asarray([np.nan])
    nan = float("nan")
    return {"proposed": tp + fp, "tp": tp, "fp": fp, "positives": n_pos,
            "precision": tp / (tp + fp) if tp + fp else nan,
            "recall": tp / n_pos if n_pos else nan,
            "cut_med": float(np.nanmedian(ce)), "cut_p75": float(np.nanpercentile(ce, 75)),
            "cut_le_stretch": float(np.mean(ce <= p.stretch_px)) if cut_err else nan,
            "agree_tp": agree_tp / total_tp if total_tp else nan,
            "agree_after": agree_after / total if total else nan,
            "agree_before": agree_before / total if total else nan,
            "ok_n": ok_n, "ok_precision": ok_tp / ok_n if ok_n else nan}


def structures():
    base = PARAMS
    for sz in SIZES:
        for t_run in (0.2, 0.25, 0.3, 0.35):
            yield f"stretch {sz:.0f} t_run {t_run}", replace(base, stretch_px=sz, t_run=t_run)
    b = replace(base, t_run=0.3)
    for mrs in (1, 3):
        yield f"stretch 90 t_run 0.3 min_run_stretches {mrs}", replace(b, min_run_stretches=mrs)
    yield "stretch 90 t_run 0.3 smooth 1", replace(b, smooth=1)
    yield "stretch 90 t_run 0.3 min_piece 300", replace(b, min_piece_px=300.0)


def _f(x, pct=True):
    return "-" if x != x else (f"{x:.0%}" if pct else f"{x:.0f}")


def _row(name, m):
    return (f"| {name} | {m['proposed']} | {m['tp']} | {_f(m['precision'])} | {_f(m['recall'])} | "
            f"{_f(m['cut_med'], False)} | {_f(m['cut_p75'], False)} | {_f(m['cut_le_stretch'])} | "
            f"{_f(m['agree_tp'])} | {_f(m['agree_before'])} → {_f(m['agree_after'])} | "
            f"{m['ok_n']} / {_f(m['ok_precision'])} |")


HEAD = ("| setting | proposed | TP | precision | recall | cut err median px | cut err p75 px | "
        "cuts within 1 stretch | class agreement (TP, by length) | agreement before → after "
        "(all proposed) | verify ok: n / precision |\n|---|---|---|---|---|---|---|---|---|---|---|")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--write-model", action="store_true")
    a = ap.parse_args(argv)
    out = a.out
    cpath = a.cache or out / "eval" / "curb_soft_split_votes.pkl"
    lines = split_gt.build(out)
    if cpath.exists() and not a.rebuild:
        cache = pickle.loads(cpath.read_bytes())
    else:
        cache = build_cache(lines, out, cpath, a.workers)
    from annotation_qa.cad_ref import CadRef
    cref = CadRef(out)
    cad = {g.frame: cref.info(g.frame) for g in lines}
    oof = {sz: stretch_oof(lines, cache, sz) for sz in SIZES}
    results = []
    rows = []
    for name, p in structures():
        cands = candidates(lines, cache, cad, oof[p.stretch_px][2], p)
        scores = line_oof(lines, cands)
        for t in T_LINES:
            m = metrics(lines, cands, scores, t, p)
            results.append((f"{name} t_line {t}", replace(p, t_line=t), m))
            rows.append(_row(f"{name} t_line {t}", m))
    n_lab = len({g.key for g in lines})
    n_pos = sum(split_gt.label_truth(lines).values())
    ok = [r for r in results if r[2]["precision"] >= 0.7 and r[2]["proposed"] >= MIN_PROPOSED]
    enough = [r for r in results if r[2]["proposed"] >= MIN_PROPOSED] or results
    best = max(ok, key=lambda r: (r[2]["recall"], r[2]["precision"])) if ok else max(
        enough, key=lambda r: (np.nan_to_num(r[2]["precision"]), r[2]["recall"]))
    txt = ["# curb_soft_split vs Roshan's curb_soft_v2 review\n",
           f"{n_lab} pre-review edge labels in {len({(g.stem, g.frame) for g in lines})} frames; "
           f"{n_pos} need a split (cut into pieces of different classes, both ≥ "
           f"{split_gt.MIN_CLASS_PX:.0f} px). Out-of-fold (5 folds by frame) for both models.\n",
           HEAD, *rows, ""]
    tag = "precision ≥ 70%, max recall" if ok else \
        f"NO setting reaches 70% precision with ≥ {MIN_PROPOSED} proposals; best precision"
    txt += [f"Chosen ({tag}): **{best[0]}**", HEAD, _row(best[0], best[2])]
    # verify's "ok" rule (every piece: curb_soft vote margin >= m with >= v votes) at the
    # chosen setting, for a few thresholds
    p = best[1]
    cands = candidates(lines, cache, cad, oof[p.stretch_px][2], p)
    scores = line_oof(lines, cands)
    txt += ["", "verify ok rule at the chosen setting (every piece: vote margin ≥ m, ≥ v votes)",
            "| m | v | ok n | ok precision |", "|---|---|---|---|"]
    for mg in (0.0, 0.5, 0.75, 1.0):
        for v in (1, 2, 3):
            mm = metrics(lines, cands, scores, p.t_line, p, mg, v)
            txt.append(f"| {mg} | {v} | {mm['ok_n']} | {_f(mm['ok_precision'])} |")
    if a.write_model:
        p = best[1]
        names, srows, probs = oof[p.stretch_px]
        stretch = sm.fit_all(srows, names, p.stretch_px)
        cands = candidates(lines, cache, cad, probs, p)
        truth = split_gt.label_truth(lines)
        keys = list(cands)
        X = np.array([[cands[k][1]["line"][n] for n in LINE_KEYS] for k in keys], float)
        y = np.array([truth[k] for k in keys], float)
        line = {"names": list(LINE_KEYS), "n_train": int(len(y)), **sm.fit(X, y, sm.L2)}
        model = {"stretch": stretch, "line": line, "params": asdict(p),
                 "fitted": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "eval": dict(best[2]), "setting": best[0]}
        sm.save(model, css.MODEL_PATH)
        css.load_model.cache_clear()
        txt += ["", f"Model written to {css.MODEL_PATH}"]
    rep = "\n".join(txt)
    (out / "eval").mkdir(parents=True, exist_ok=True)
    (out / "eval" / "curb_soft_split_report.md").write_text(rep)
    print(rep)


if __name__ == "__main__":
    main()
