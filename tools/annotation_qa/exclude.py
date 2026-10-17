"""OUT/excluded.json: issue keys a later fix round must leave out (auto-fix rejected by verifier)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

REASON = "auto-fix rejected by verifier"
_TAG = re.compile(r"^(?P<stem>.+?)_fix_(?P<type>.+)_v\d+$")


def excluded_path(out: Path) -> Path:
    return Path(out) / "excluded.json"


def read_excluded(out: Path) -> dict[str, dict]:
    try:
        d = json.loads(excluded_path(out).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def exclude_round(out: Path, tag: str, verdict: str = "bad") -> int:
    """Add the round's changes with ``verdict`` to excluded.json; return the keys newly added."""
    out = Path(out)
    m = _TAG.match(tag)
    if not m:
        raise ValueError(f"not a round tag: {tag}")
    vpath = out / "rounds" / f"{tag}_verify.json"
    if not vpath.exists():
        raise FileNotFoundError(f"no verify file for {tag}: run verify --round {tag} first")
    verdicts = json.loads(vpath.read_text())
    changes = json.loads((out / "rounds" / f"{tag}_changes.json").read_text())
    cur = read_excluded(out)
    added = 0
    for frame, labels in verdicts.items():
        for label_id, v in labels.items():
            if v.get("verdict") != verdict or label_id not in (changes.get(frame) or {}):
                continue
            key = f"{m['stem']}|{frame}|{label_id}|{m['type']}"
            if key in cur:
                continue
            cur[key] = {"reason": REASON, "round": tag, "verdict": verdict,
                        "reasons": list(v.get("reasons") or [])}
            added += 1
    if added:
        p = excluded_path(out)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(cur, indent=1, sort_keys=True))
        os.replace(tmp, p)
    return added
