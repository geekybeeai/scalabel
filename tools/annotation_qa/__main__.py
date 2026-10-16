"""CLI: python3 -m annotation_qa scan --exports <dir|files> --out OUT --types a,b"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from annotation_qa.data import REPO_ROOT
from annotation_qa.scan import run_scan

DEFAULT_ROOT = REPO_ROOT / "local-data"
DEFAULT_OUT = REPO_ROOT / "local-data/items/Full_Verfication/qa_2026_10_17"


def _expand(items: list[str]) -> list[Path]:
    paths: list[Path] = []
    for it in items:
        p = Path(it)
        paths.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="annotation_qa")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="run detectors and write issues + crops")
    sc.add_argument("--exports", nargs="+", required=True, help="a directory or export files")
    sc.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sc.add_argument("--out", type=Path, default=DEFAULT_OUT)
    sc.add_argument("--types", required=True, help="comma-separated issue types")
    sc.add_argument("--subset", choices=["reviewer+random20"], default=None)
    rv = sub.add_parser("review", help="serve the review page")
    rv.add_argument("--out", type=Path, default=DEFAULT_OUT)
    rv.add_argument("--port", type=int, default=8790)
    fx = sub.add_parser("fix", help="apply accepted fixes and build review item files")
    fx.add_argument("--type", required=True)
    fx.add_argument("--out", type=Path, default=DEFAULT_OUT)
    fx.add_argument("--version", type=int, required=True)
    fx.add_argument("--create-projects", action="store_true")
    fx.add_argument("--server", default="http://localhost:8686")
    mg = sub.add_parser("merge", help="merge a reviewed export into the working copy")
    mg.add_argument("--stem", required=True)
    mg.add_argument("--reviewed", type=Path, required=True)
    mg.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    if a.cmd == "fix":
        from annotation_qa.fix import apply
        r = apply(a.type, a.out, a.version)
        for stem in sorted(set(r.changed) | set(r.hand)):
            print(f"{stem}: changed={r.changed.get(stem, [])} hand={r.hand.get(stem, [])}")
            if a.create_projects:
                from annotation_qa.scalabel_project import create
                tag = f"{stem}_fix_{a.type}_v{a.version}"
                print(create(a.out / "rounds" / f"{tag}_items.json", tag, a.server))
        return 0
    if a.cmd == "merge":
        from annotation_qa.merge import merge_reviewed
        print(f"merged {merge_reviewed(a.stem, a.reviewed, a.out)} frames")
        return 0
    if a.cmd == "review":
        from annotation_qa.review_server import serve
        serve(a.out, a.port)
        return 0
    s = run_scan(_expand(a.exports), a.root, a.out,
                 list(dict.fromkeys(t for t in a.types.split(",") if t)), subset=a.subset)
    print(f"frames={s.frames} skipped={len(s.skipped)} counts={s.counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
