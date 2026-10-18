"""Per-stretch curb/soft model for ``curb_soft_split``, fitted on Roshan's review.

Each stretch of a pre-review edge line gets the class his reviewed lines give it (majority of
its covered 3 px samples, split_gt). Inputs: ``curb_soft_split.stretch_features`` + the
curb_soft vote + the label's category. Model: L2 logistic regression (numpy IRLS) on
standardised inputs; ``predict`` gives P(curb). Out-of-fold probabilities come from
frame-grouped cross-validation, so the detection metrics in split_eval are not fitted on the
frames they are measured on (5 folds by frame).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from annotation_qa.detectors.curb_soft_split import VOTE_KEYS, feature_vector

L2 = 1.0


def feature_names(cache: dict, size: float) -> list[str]:
    for v in cache.values():
        for rec in v.get(size) or []:
            if rec.get("f"):
                return sorted(rec["f"]) + list(VOTE_KEYS)
    raise ValueError("no stretch features in the cache")


N_FOLDS = 5


def fold_of(stem: str, frame: str) -> int:
    """Cross-validation fold of a frame (stable hash): outcomes cluster by frame."""
    import hashlib
    return int(hashlib.sha1(f"{stem}|{frame}".encode()).hexdigest(), 16) % N_FOLDS


def stretch_table(lines, cache: dict, size: float, names: list[str]):
    """Rows for every judged stretch: (line index, stretch index, x, y (1 curb / 0 soft /
    -1 not covered), group = cross-validation fold of the frame)."""
    rows = []
    for li, g in enumerate(lines):
        st = (cache.get((g.stem, g.frame, g.label_id, g.poly_index)) or {}).get(size) or []
        for si, rec in enumerate(st):
            x = feature_vector(rec, g.category, names)
            if x is None or not np.isfinite(x).all():
                continue
            m = (g.s >= rec["s0"]) & (g.s < max(rec["s1"], rec["s0"] + 1e-6)) & (g.cats >= 0)
            y = -1 if not m.any() else int(np.mean(g.cats[m] == 0) > 0.5)
            rows.append((li, si, x, y, fold_of(g.stem, g.frame)))
    return rows


def fit(X: np.ndarray, y: np.ndarray, l2: float = L2, iters: int = 30) -> dict:
    mu, sd = X.mean(0), X.std(0)
    sd[sd < 1e-9] = 1.0
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd])
    w = np.zeros(Z.shape[1])
    reg = np.full(Z.shape[1], l2)
    reg[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Z @ w, -30, 30)))
        W = p * (1 - p)
        H = (Z * W[:, None]).T @ Z + np.diag(reg)
        g = Z.T @ (p - y) + reg * w
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-6:
            break
    return {"mean": mu.tolist(), "std": sd.tolist(), "w": w.tolist()}


def predict(model: dict, X: np.ndarray) -> np.ndarray:
    Z = (X - np.asarray(model["mean"])) / np.asarray(model["std"])
    z = model["w"][0] + Z @ np.asarray(model["w"][1:])
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def oof_probs(rows, l2: float = L2) -> dict:
    """{(line index, stretch index): P(curb)} with each fold's frames predicted by a model
    fitted on the other folds (training rows: covered stretches only)."""
    groups = sorted({r[4] for r in rows})
    X = np.array([r[2] for r in rows])
    y = np.array([r[3] for r in rows])
    grp = np.array([r[4] for r in rows])
    out = {}
    for gname in groups:
        tr = (grp != gname) & (y >= 0)
        model = fit(X[tr], y[tr].astype(float), l2)
        te = np.flatnonzero(grp == gname)
        p = predict(model, X[te])
        for k, pk in zip(te, p):
            out[(rows[k][0], rows[k][1])] = float(pk)
    return out


def fit_all(rows, names: list[str], size: float, l2: float = L2) -> dict:
    X = np.array([r[2] for r in rows if r[3] >= 0])
    y = np.array([r[3] for r in rows if r[3] >= 0], float)
    m = fit(X, y, l2)
    return {"names": names, "stretch_px": size, "l2": l2, "n_train": int(len(y)), **m}


def save(model: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(model, indent=1))
