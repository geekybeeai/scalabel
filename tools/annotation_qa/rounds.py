"""Fix-round files in OUT/rounds and the merge markers in OUT/rounds/merged.json.

A light module (no image or detector imports) shared by fix, merge, scan,
viewb and final.
"""

from __future__ import annotations

import hashlib
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


def last_marker(out: Path, stem: str, tag: str) -> dict | None:
    """The newest merge marker of one round, or None."""
    hits = [m for m in merge_markers(out) if (m.get("stem"), m.get("tag")) == (stem, tag)]
    return hits[-1] if hits else None


# ---- round base snapshots: OUT/rounds/<tag>_base.json ----
def labels_sha1(labels) -> str:
    """Canonical sha1 of a frame's label list (key order does not matter)."""
    return hashlib.sha1(json.dumps(labels or [], sort_keys=True, separators=(",", ":"))
                        .encode()).hexdigest()


def base_path(out: Path, tag: str) -> Path:
    return Path(out) / "rounds" / f"{tag}_base.json"


def write_base(out: Path, stem: str, tag: str, frames: dict[str, list]) -> dict:
    """Record when the round was built and the sha1 of each round frame's labels in the
    working copy it was built from (``frames``: name -> labels), atomically."""
    rec = {"stem": stem, "tag": tag,
           "built_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
           "frames": {n: labels_sha1(l) for n, l in sorted(frames.items())}}
    p = base_path(out, tag)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    os.replace(tmp, p)
    return rec


def read_base(out: Path, tag: str) -> dict | None:
    """The round's base snapshot, or None for a legacy round (built before base files)."""
    try:
        d = json.loads(base_path(out, tag).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("frames"), dict) else None


def built_at(out: Path, tag: str, base: dict | None = None) -> datetime | None:
    """When the round was built: the base snapshot's time, else (a legacy round) the
    mtime of its changes / items file."""
    base = base if base is not None else read_base(out, tag)
    if base is not None:
        t = marker_time({"timestamp": base.get("built_at")})
        if t is not None:
            return t
    for suffix in ("changes", "items"):
        p = Path(out) / "rounds" / f"{tag}_{suffix}.json"
        if p.exists():
            return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    return None


# ---- verify files: OUT/rounds/<tag>_verify.json ----
VERIFY_META = "__meta__"
_sha_cache: dict[Path, tuple[int, int, str]] = {}


def _file_sha1(path: Path) -> str:
    """sha1 of a file's bytes, cached by (mtime_ns, size) (View B asks on every request)."""
    st = path.stat()
    hit = _sha_cache.get(path)
    if hit and hit[:2] == (st.st_mtime_ns, st.st_size):
        return hit[2]
    h = hashlib.sha1(path.read_bytes()).hexdigest()
    _sha_cache[path] = (st.st_mtime_ns, st.st_size, h)
    return h


def verdicts_current(out: Path, tag: str, raw: dict) -> bool:
    """Was the verify file ``raw`` written for the round's current changes.json?

    It records ``__meta__.changes_sha1``; a legacy file without it counts while it is not
    older than changes.json."""
    rounds_dir = Path(out) / "rounds"
    changes = rounds_dir / f"{tag}_changes.json"
    if not changes.exists():
        return False
    meta = raw.get(VERIFY_META) if isinstance(raw, dict) else None
    if isinstance(meta, dict) and meta.get("changes_sha1"):
        return meta["changes_sha1"] == _file_sha1(changes)
    vpath = rounds_dir / f"{tag}_verify.json"
    try:
        return vpath.stat().st_mtime_ns >= changes.stat().st_mtime_ns
    except OSError:
        return False


def read_verdicts(out: Path, tag: str) -> dict | None:
    """{frame: {label: verdict}} of the round, without the meta entry; None when the file is
    missing or unreadable, or was written for another version of changes.json."""
    p = Path(out) / "rounds" / f"{tag}_verify.json"
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not verdicts_current(out, tag, raw):
        return None
    return {k: v for k, v in raw.items() if k != VERIFY_META}
