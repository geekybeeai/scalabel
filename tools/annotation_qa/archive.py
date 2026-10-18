"""archive-round: set a fix round aside so it can be rebuilt, or a later round built.

Moves into OUT/backup/archived_rounds/<tag>/ (``<tag>.<timestamp>`` when that exists):
``rounds/<tag>_{items,changes,verify,base}.json``, ``work/<tag>.json``, and the round's
entries of ``reverts.json`` and ``viewb_progress.json`` (written there as JSON files of their
own). The round's merge markers in ``rounds/merged.json`` are kept: what was merged stays
merged, and `fix` refuses to reuse a merged tag.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from annotation_qa import progress, reverts, rounds
from annotation_qa.fix import ROUND_SIDE_FILES


def archive_round(out: Path, tag: str) -> dict:
    """Archive one round; ValueError when nothing of it exists. Returns what was moved."""
    out = Path(out)
    files = [p for p in [out / "rounds" / f"{tag}_{k}.json" for k in ROUND_SIDE_FILES]
             + [out / "work" / f"{tag}.json"] if p.exists()]
    rev = reverts.read_reverts(out)
    prog = progress.read_progress(out)
    if not files and tag not in rev and tag not in prog:
        raise ValueError(f"no round {tag} in {out} (no round files, reverts or progress)")
    root = out / "backup" / "archived_rounds"
    dest = root / tag
    if dest.exists():
        dest = root / f"{tag}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    dest.mkdir(parents=True)
    had_rev, had_prog = tag in rev, tag in prog
    if had_rev:
        (dest / "reverts.json").write_text(json.dumps({tag: rev[tag]}, indent=1, sort_keys=True))
        rev.pop(tag)
        reverts.write_reverts(out, rev)
    if had_prog:
        (dest / "viewb_progress.json").write_text(
            json.dumps({tag: prog[tag]}, indent=1, sort_keys=True))
        prog.pop(tag)
        progress.write_progress(out, prog)
    moved = []
    for p in files:
        target = dest / p.parent.name / p.name  # rounds/... or work/...
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(target))
        moved.append(str(target))
    stem = tag.split("_fix_")[0]
    return {"dest": str(dest), "files": moved, "reverts": had_rev, "progress": had_prog,
            "merged": (stem, tag) in rounds.merged_tags(out)}
