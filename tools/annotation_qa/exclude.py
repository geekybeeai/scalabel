"""OUT/excluded.json: issue keys a later fix round must leave out (auto-fix rejected by verifier)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from annotation_qa.rounds import read_verdicts

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
    verdicts = read_verdicts(out, tag)
    if verdicts is None:
        raise ValueError(f"the verify file of {tag} is stale or unreadable (written for another "
                         f"changes.json); run verify --round {tag} again")
    changes = json.loads((out / "rounds" / f"{tag}_changes.json").read_text())
    cur = read_excluded(out)
    added = 0
    for frame, labels in verdicts.items():
        for label_id, v in labels.items():
            rec = (changes.get(frame) or {}).get(label_id)
            if v.get("verdict") != verdict or rec is None:
                continue
            # a combined round (e.g. "edges") lists its real issue types per change; a
            # single-type round has no issue_types, so the tag's type is the only one
            types = rec["issue_types"] if "issue_types" in rec else [m["type"]]
            for t in types:
                key = f"{m['stem']}|{frame}|{label_id}|{t}"
                if key in cur:
                    continue
                cur[key] = {"reason": REASON, "round": tag, "verdict": verdict,
                            "reasons": list(v.get("reasons") or [])}
                added += 1
    if added:
        write_excluded(out, cur)
    return added


def write_excluded(out: Path, data: dict) -> None:
    p = excluded_path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, p)


def add_excluded(out: Path, entries: dict[str, dict]) -> int:
    """Add ``entries`` (key -> record) to excluded.json, keeping existing keys; return added."""
    cur = read_excluded(out)
    new = {k: v for k, v in entries.items() if k not in cur}
    if new:
        write_excluded(out, {**cur, **new})
    return len(new)
