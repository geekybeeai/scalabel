"""The Issue model and its JSONL / marks persistence."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Calibration (spec: "in a random sample of 30 issues per type, at least about
# two thirds should be real"). The sample is the fixed seed-0 draw /calibrate shows.
CALIBRATION_N = 30
CALIBRATION_MIN_ANSWERED = 20


@dataclass
class Issue:
    issue_type: str  # curb_soft | position | shape | duplicates | solid_dashed | yellow_structure
    export_stem: str
    frame_name: str
    label_id: str
    category: str
    confidence: float  # 0..1
    reason: str  # one line, human readable
    anchor: tuple[float, float]  # image x, y for the crop centre
    # {"op":"set_category","to":str} | {"op":"shift","poly_index":int,"offsets":list[float]}
    # | {"op":"delete"} | {"op":"drop_vertices","poly_index":int,"indices":list[int]} | None
    fix: dict | None
    hand_fix: bool  # True -> cannot auto-fix; frame goes to review unchanged
    related_label_ids: list[str] = field(default_factory=list)
    # numbers that drove the decision, so thresholds can be refit from marks later
    features: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.export_stem}|{self.frame_name}|{self.label_id}|{self.issue_type}"


def write_issues(path: Path, issues: list[Issue]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        for i in issues:
            f.write(json.dumps(asdict(i)) + "\n")
    os.replace(tmp, path)


def append_issues(path: Path, issues: list[Issue]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for i in issues:
            f.write(json.dumps(asdict(i)) + "\n")


def read_issues(path: Path) -> list[Issue]:
    out = []
    with open(path) as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                d["anchor"] = tuple(d["anchor"])
                out.append(Issue(**d))
    return out


def load_issues_tolerant(path: Path) -> list[Issue]:
    """Like read_issues, but skips (and logs) malformed lines, e.g. a truncated last line."""
    out = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                d["anchor"] = tuple(d["anchor"])
                out.append(Issue(**d))
            except (ValueError, KeyError, TypeError) as e:
                log.warning("%s:%d skipped malformed line (%s)", path, n, e)
    return out


def read_marks(path: Path) -> dict[str, str]:
    """Key -> "real" | "false". A missing file gives {}."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def write_marks(path: Path, marks: dict[str, str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(marks, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


# ---- scan metadata: OUT/issues_<type>.meta.json = {export_stem: {"sha1", "scanned_at"}} ----
def meta_path(out: Path, issue_type: str) -> Path:
    return Path(out) / f"issues_{issue_type}.meta.json"


def read_meta(out: Path, issue_type: str) -> dict[str, dict] | None:
    """The scan metadata of one type, or None when the file is missing or unreadable."""
    p = meta_path(out, issue_type)
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    # tolerate the bare {stem: sha1} form
    return {k: (v if isinstance(v, dict) else {"sha1": v}) for k, v in d.items()}


def write_meta(out: Path, issue_type: str, meta: dict[str, dict]) -> None:
    p = meta_path(out, issue_type)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(meta, indent=1, sort_keys=True))
    os.replace(tmp, p)


def file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- calibration gate ----
def calibration_sample(issues: list[Issue]) -> list[Issue]:
    """The fixed calibration sample: 30 issues drawn with seed 0 from the key-sorted list."""
    mine = sorted(issues, key=lambda i: i.key)
    if len(mine) > CALIBRATION_N:
        mine = random.Random(0).sample(mine, CALIBRATION_N)
    return mine


def calibration_status(issues: list[Issue], marks: dict[str, str]) -> dict:
    """Answered k/n, real count, precision and PASS / FAIL / NOT CALIBRATED.

    PASS needs precision >= 2/3 over the answered issues of the sample, with at
    least 20 answered (or all of them when the type has fewer than 20 issues).
    """
    sample = calibration_sample(issues)
    n = len(sample)
    answers = [marks.get(i.key) for i in sample]
    answers = [a for a in answers if a in ("real", "false")]
    k, real = len(answers), answers.count("real")
    need = min(CALIBRATION_MIN_ANSWERED, n)
    if n == 0 or k < need:
        status = "NOT CALIBRATED"
    elif 3 * real >= 2 * k:
        status = "PASS"
    else:
        status = "FAIL"
    return {"n": n, "answered": k, "real": real, "need": need,
            "precision": (real / k) if k else None, "status": status}


def format_calibration(issue_type: str, st: dict) -> str:
    p = "-" if st["precision"] is None else f"{st['precision']:.2f}"
    return (f"{issue_type} calibration {st['status']}: answered {st['answered']}/{st['n']}, "
            f"real {st['real']}, precision {p} (PASS needs >= 2/3 with >= {st['need']} answered)")
