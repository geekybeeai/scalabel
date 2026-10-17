"""Revert requests from View B: OUT/reverts.json = {round_tag: {frame: {label_id: {time, reason?}}}}.

Nothing is written to Scalabel. ``merge_reviewed`` applies the reverts of a round
when it merges that round's reviewed export.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def reverts_path(out: Path) -> Path:
    return Path(out) / "reverts.json"


def read_reverts(out: Path) -> dict:
    try:
        d = json.loads(reverts_path(out).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write_reverts(out: Path, data: dict) -> None:
    p = reverts_path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, p)


def set_revert(out: Path, tag: str, frame: str, label: str, revert: bool,
               reason: str | None = None) -> bool:
    """Add or remove one revert (atomic write); return the new state. Caller holds the lock."""
    data = read_reverts(out)
    rnd = data.setdefault(tag, {})
    if revert:
        e = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if reason:
            e["reason"] = reason
        rnd.setdefault(frame, {})[label] = e
    else:
        rnd.get(frame, {}).pop(label, None)
        if not rnd.get(frame, True):
            rnd.pop(frame)
        if not rnd:
            data.pop(tag)
    write_reverts(out, data)
    return revert


def round_reverts(out: Path, tag: str) -> dict:
    d = read_reverts(out).get(tag)
    return d if isinstance(d, dict) else {}
