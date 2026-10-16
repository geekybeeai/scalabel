"""The Issue model and its JSONL / marks persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


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

    @property
    def key(self) -> str:
        return f"{self.export_stem}|{self.frame_name}|{self.label_id}|{self.issue_type}"


def write_issues(path: Path, issues: list[Issue]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
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
    with open(path, "w") as f:
        json.dump(marks, f, indent=1, sort_keys=True)
