"""CLI: python3 -m annotation_qa <command> ... (see tools/annotation_qa/README.md)"""

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


def _create_projects(out: Path, tags: list[str], server: str) -> int:
    """Create one review project per round tag; report and continue on failure.

    Returns the number of failures (an existing name is reported, not a failure)."""
    from annotation_qa.scalabel_project import ProjectExists, create
    failed = 0
    for tag in tags:
        try:
            print(f"{tag}: created {create(out / 'rounds' / f'{tag}_items.json', tag, server)}",
                  flush=True)
        except ProjectExists as e:
            print(f"{tag}: skipped, {e}", flush=True)
        except Exception as e:  # noqa: BLE001 - keep going with the next round
            failed += 1
            print(f"{tag}: FAILED to create project: {e}\n"
                  f"  retry with: python3 -m annotation_qa project --round {tag}", flush=True)
    return failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="annotation_qa")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="run detectors and write issues + crops")
    sc.add_argument("--exports", nargs="+", required=True, help="a directory or export files")
    sc.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sc.add_argument("--out", type=Path, default=None, help=f"default {DEFAULT_OUT}")
    sc.add_argument("--types", required=True, help="comma-separated issue types")
    sc.add_argument("--subset", choices=["reviewer+random20"], default=None)
    sc.add_argument("--since-merge", action="store_true",
                    help="re-detect only the frames merged since the last scan of each type")
    sc.add_argument("--workers", type=int, default=1,
                    help="detect frames in N worker processes (default 1)")
    cy = sub.add_parser("calibrate-yellow", help="write OUT/calibration/yellow_stats.json")
    cy.add_argument("--exports", nargs="+", required=True, help="a directory or export files")
    cy.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    cy.add_argument("--out", type=Path, default=DEFAULT_OUT)
    rv = sub.add_parser("review", help="serve the review page")
    rv.add_argument("--out", type=Path, default=DEFAULT_OUT)
    rv.add_argument("--port", type=int, default=8790)
    fx = sub.add_parser("fix", help="apply accepted fixes and build review item files")
    fx.add_argument("--type", required=True,
                    help="issue type, or comma-separated types for a combined round "
                         "(e.g. curb_soft,edge_on_paint)")
    fx.add_argument("--name", default=None,
                    help="type part of the round tag (default: the types joined by '+')")
    fx.add_argument("--out", type=Path, default=DEFAULT_OUT)
    fx.add_argument("--version", type=int, required=True)
    fx.add_argument("--create-projects", action="store_true")
    fx.add_argument("--server", default="http://localhost:8686")
    fx.add_argument("--force", action="store_true",
                    help="override the calibration / unmerged-round / existing-tag / stale guards")
    pj = sub.add_parser("project", help="(re)create the Scalabel review project of one round")
    pj.add_argument("--round", required=True, help="round tag, e.g. <stem>_fix_<type>_v<N>")
    pj.add_argument("--out", type=Path, default=DEFAULT_OUT)
    pj.add_argument("--server", default="http://localhost:8686")
    mg = sub.add_parser("merge", help="merge a reviewed export into the working copy")
    mg.add_argument("--stem", required=True)
    mg.add_argument("--reviewed", type=Path, required=True)
    mg.add_argument("--round", required=True, help="round tag, e.g. <stem>_fix_<type>_v<N>")
    mg.add_argument("--out", type=Path, default=DEFAULT_OUT)
    vb = sub.add_parser("viewb", help="serve the review page (View B is at /viewb)")
    vb.add_argument("--out", type=Path, default=DEFAULT_OUT)
    vb.add_argument("--port", type=int, default=8790)
    vb.add_argument("--prerender", action="store_true", help="render every crop before serving")
    pz = sub.add_parser("prerender-zoom", help="render the lightbox zoom crops ahead of time")
    pz.add_argument("--out", type=Path, default=DEFAULT_OUT)
    pz.add_argument("--calibration-only", action="store_true",
                    help="only the frozen calibration samples (OUT/calibration/sample_<type>.json)")
    cm_ = sub.add_parser("comments", help="print the review comments as plain text")
    cm_.add_argument("--out", type=Path, default=DEFAULT_OUT)
    br = sub.add_parser("bar", help="check the 8 reviewer frames have real-marked issues")
    br.add_argument("--out", type=Path, default=DEFAULT_OUT)
    fn = sub.add_parser("final", help="write one corrected export per batch to OUT/final")
    fn.add_argument("--out", type=Path, default=DEFAULT_OUT)
    rf = sub.add_parser("refilter", help="re-apply the calibrated rule to stored issues")
    rf.add_argument("--type", required=True, choices=["position", "curb_soft", "edge_position",
                                                      "edge_on_paint"])
    rf.add_argument("--out", type=Path, default=DEFAULT_OUT)
    vf = sub.add_parser("verify", help="auto-verify the changes of a fix round")
    vf.add_argument("--round", default=None, help="round tag")
    vf.add_argument("--all", action="store_true", help="every round without a verdict file")
    vf.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ex = sub.add_parser("exclude", help="leave a round's verifier-rejected auto-fixes out of later rounds")
    ex.add_argument("--round", required=True, help="round tag")
    ex.add_argument("--verdict", default="bad", choices=["ok", "check", "bad"])
    ex.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    if a.cmd == "exclude":
        from annotation_qa.exclude import exclude_round
        try:
            n = exclude_round(a.out, a.round, a.verdict)
        except (FileNotFoundError, ValueError) as e:
            print(f"REFUSED: {e}")
            return 2
        print(f"excluded {n} keys added to {a.out / 'excluded.json'}")
        return 0
    if a.cmd == "verify":
        if not a.all and not a.round:
            print("verify needs --round <tag> or --all")
            return 2
        from annotation_qa.verify import run
        run(a.out, a.round, a.all)
        return 0
    if a.cmd == "refilter":
        from annotation_qa.refilter import refilter
        r = refilter(a.out, a.type)
        print(f"issues: {r['before']} -> {r['after']}; sample: {r['sample_before']} -> "
              f"{r['sample_after']} keys, {r['sample_answered']} answered; "
              f"backup {r['backup']}; dropped by CAD {r['cad_dropped']}, "
              f"rescued by CAD {r['cad_rescued']}")
        return 0
    if a.cmd == "comments":
        from annotation_qa.comments import format_text, read_comments
        text = format_text(read_comments(a.out))
        print(text if text else "no comments")
        return 0
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
            print("no exports found; nothing copied")
        return 0
    if a.cmd == "prerender-zoom":
        import time
        from annotation_qa.data import ORIGINALS_DIR
        from annotation_qa.zoom import prerender
        t0 = time.time()
        n = prerender(a.out, REPO_ROOT, ORIGINALS_DIR, a.calibration_only)
        print(f"rendered {n} zoom crops in {time.time() - t0:.0f}s", flush=True)
        return 0
    if a.cmd == "viewb":
        from annotation_qa.review_server import serve
        if a.prerender:
            from annotation_qa.viewb import prerender
            print(f"rendered {prerender(a.out, REPO_ROOT)} crops", flush=True)
        serve(a.out, a.port, page="/viewb")
        return 0
    if a.cmd == "calibrate-yellow":
        import json
        from annotation_qa.calibrate import calibrate_yellow
        print(json.dumps(calibrate_yellow(_expand(a.exports), a.root, a.out), indent=1))
        return 0
    if a.cmd == "fix":
        from annotation_qa.fix import FixRefused, apply
        try:
            r = apply(a.type, a.out, a.version, force=a.force, name=a.name)
        except FixRefused as e:
            print(f"REFUSED: fix --type {a.type} --version {a.version}")
            for why in e.reasons:
                print(f"  - {why}")
            print("Nothing was written. Fix the above, or pass --force to override.")
            return 2
        for why in r.forced:
            print(f"warning: forced past: {why}")
        for stem in sorted(set(r.changed) | set(r.hand)):
            print(f"{stem}: changed={r.changed.get(stem, [])} hand={r.hand.get(stem, [])}")
        if not r.tags:
            print(f"no frames changed or flagged for {a.type}; no round written")
        if a.create_projects and _create_projects(a.out, r.tags, a.server):
            return 1
        return 0
    if a.cmd == "project":
        items = a.out / "rounds" / f"{a.round}_items.json"
        if not items.exists():
            print(f"no items file for round {a.round}: {items}")
            return 2
        return 1 if _create_projects(a.out, [a.round], a.server) else 0
    if a.cmd == "merge":
        from annotation_qa.merge import MergeRefused, merge_reviewed
        try:
            n = merge_reviewed(a.stem, a.reviewed, a.out, round_tag=a.round)
        except MergeRefused as e:
            print(f"REFUSED: {e}\nNothing was merged.")
            return 2
        print(f"merged {n} frames")
        return 0
    if a.cmd == "review":
        from annotation_qa.review_server import serve
        serve(a.out, a.port)
        return 0
    if a.out is None:
        if a.subset:
            print(f"refusing: --subset needs an explicit --out (it would replace the full "
                  f"results in {DEFAULT_OUT})")
            return 2
        a.out = DEFAULT_OUT
    s = run_scan(_expand(a.exports), a.root, a.out,
                 list(dict.fromkeys(t for t in a.types.split(",") if t)), subset=a.subset,
                 since_merge=a.since_merge, workers=a.workers)
    print(f"frames={s.frames} skipped={len(s.skipped)} errors={len(s.errors)} counts={s.counts}")
    for e in s.errors[:20]:
        print(f"  error: {e}")
    return 1 if s.errors else 0


if __name__ == "__main__":
    sys.exit(main())
