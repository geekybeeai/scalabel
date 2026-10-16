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
    cy = sub.add_parser("calibrate-yellow", help="write OUT/calibration/yellow_stats.json")
    cy.add_argument("--exports", nargs="+", required=True, help="a directory or export files")
    cy.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    cy.add_argument("--out", type=Path, default=DEFAULT_OUT)
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
    mg.add_argument("--round", required=True, help="round tag, e.g. <stem>_fix_<type>_v<N>")
    mg.add_argument("--out", type=Path, default=DEFAULT_OUT)
    vb = sub.add_parser("viewb", help="serve the review page (View B is at /viewb)")
    vb.add_argument("--out", type=Path, default=DEFAULT_OUT)
    vb.add_argument("--port", type=int, default=8790)
    br = sub.add_parser("bar", help="check the 8 reviewer frames have real-marked issues")
    br.add_argument("--out", type=Path, default=DEFAULT_OUT)
    fn = sub.add_parser("final", help="copy merged working copies to OUT/final")
    fn.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    if a.cmd == "bar":
        from annotation_qa.final import bar_report
        rep = bar_report(a.out)
        for sub_, r in rep.items():
            types = " ".join(f"{t}={n}" for t, n in sorted(r["types"].items())) or "no issues"
            print(f"{sub_}: {types} real={r['real']}")
        print("No real-marked issue: " + (", ".join(s for s, r in rep.items() if not r["real"])
                                          or "none"))
        return 0
    if a.cmd == "final":
        from annotation_qa.final import finalize
        counts, unmerged = finalize(a.out)
        for stem, n in sorted(counts.items()):
            print(f"{stem}: {n} changes -> final/{stem}_qa_2026_10_17.json")
        for stem, tags in sorted(unmerged.items()):
            print(f"NOT EXPORTED {stem} - unmerged rounds: {', '.join(tags)}")
        if not counts and not unmerged:
            print("no fix rounds found; nothing copied")
        return 0
    if a.cmd == "viewb":
        from annotation_qa.review_server import serve
        serve(a.out, a.port, page="/viewb")
        return 0
    if a.cmd == "calibrate-yellow":
        import json
        from annotation_qa.calibrate import calibrate_yellow
        print(json.dumps(calibrate_yellow(_expand(a.exports), a.root, a.out), indent=1))
        return 0
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
        print(f"merged {merge_reviewed(a.stem, a.reviewed, a.out, round_tag=a.round)} frames")
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
