"""Fix-round files in OUT/rounds and the merge markers in OUT/rounds/merged.json.

A light module (no image or detector imports) shared by fix, merge, scan,
viewb and final.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

_CHANGES = re.compile(r"^(?P<stem>.+?)_fix_(?P<type>.+)_v(?P<v>\d+)_changes\.json$")


def round_tag(stem: str, issue_type: str, version: int) -> str:
    return f"{stem}_fix_{issue_type}_v{version}"


def round_files(out: Path) -> list[dict]:
    """One dict per fix round found in OUT/rounds, ordered by version, type, stem."""
    rounds = []
    for p in (Path(out) / "rounds").glob("*_changes.json"):
        m = _CHANGES.match(p.name)
        if m:
            tag = p.name[: -len("_changes.json")]
            rounds.append({"stem": m["stem"], "type": m["type"], "v": int(m["v"]), "tag": tag,
                           "round": f"{m['type']}_v{m['v']}",
                           "changes": p, "items": p.with_name(f"{tag}_items.json")})
    return sorted(rounds, key=lambda r: (r["v"], r["type"], r["stem"]))


def _markers_path(out: Path) -> Path:
    return Path(out) / "rounds" / "merged.json"


def merge_markers(out: Path) -> list[dict]:
    """Every merge recorded by merge_reviewed (oldest first); [] if none or unreadable."""
    try:
        entries = json.loads(_markers_path(out).read_text())
    except (OSError, ValueError):
        return []
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def merged_tags(out: Path) -> set[tuple[str, str]]:
    """(stem, round tag) pairs recorded by merge_reviewed."""
    return {(m["stem"], m["tag"]) for m in merge_markers(out) if "stem" in m and "tag" in m}


def marker_time(m: dict) -> datetime | None:
    try:
        t = datetime.fromisoformat(m["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _write_markers(out: Path, entries: list[dict]) -> None:
    path = _markers_path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(entries, indent=1))
    os.replace(tmp, path)


def record_merge(out: Path, stem: str, tag: str, reviewed: Path, frames: list[str],
                 extra: dict | None = None) -> None:
    """Append a marker (atomic), so `final`, `fix` and `scan --since-merge` know what was merged."""
    path = _markers_path(out)
    entries = json.loads(path.read_text()) if path.exists() else []
    entries.append({"stem": stem, "tag": tag, "reviewed_path": str(reviewed),
                    "merged_frames": len(frames), "frames": sorted(frames),
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    **(extra or {})})
    _write_markers(out, entries)


def drop_markers(out: Path, stem: str, tag: str) -> int:
    """Remove the markers of one round (used when a round is forcibly rebuilt)."""
    entries = merge_markers(out)
    keep = [e for e in entries if (e.get("stem"), e.get("tag")) != (stem, tag)]
    if len(keep) != len(entries):
        _write_markers(out, keep)
    return len(entries) - len(keep)
