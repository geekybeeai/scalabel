"""Re-apply a calibrated detector rule (R26) to stored issues without rescanning."""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

from annotation_qa.issues import (CALIBRATION_N, calibration_sample_path, load_issues_tolerant,
                                  read_marks, write_issues)


def _keep(issue_type: str):
    if issue_type == "position":
        from annotation_qa.detectors.position import keep_issue
        return keep_issue
    raise ValueError(f"no refilter rule for {issue_type}")


def refilter(out: Path, issue_type: str) -> dict:
    out = Path(out)
    path = out / f"issues_{issue_type}.jsonl"
    issues = load_issues_tolerant(path)
    keep = _keep(issue_type)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = out / "backup" / f"issues_{issue_type}.{stamp}.jsonl"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(path.read_bytes())
    kept = [i for i in issues if keep(i)]
    write_issues(path, kept)

    marks = read_marks(out / "marks.json")
    answered = lambda k: marks.get(k) in ("real", "false")  # noqa: E731
    sp = calibration_sample_path(out, issue_type)
    try:
        old = [str(k) for k in json.loads(sp.read_text())["keys"]]
    except (OSError, ValueError, KeyError, TypeError):
        old = []
    alive = {i.key for i in kept}
    keys = [k for k in old if k in alive and answered(k)]
    if len(keys) < CALIBRATION_N:
        rest = sorted(alive - set(keys))
        n = min(CALIBRATION_N - len(keys), len(rest))
        keys += random.Random(1).sample(rest, n)
    sp.parent.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_name(sp.name + ".tmp")
    tmp.write_text(json.dumps(
        {"type": issue_type, "keys": keys,
         "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "n_issues_at_draw": len(kept)}, indent=1))
    os.replace(tmp, sp)
    return {"before": len(issues), "after": len(kept), "sample_before": len(old),
            "sample_after": len(keys), "sample_answered": sum(answered(k) for k in keys),
            "backup": str(backup)}
