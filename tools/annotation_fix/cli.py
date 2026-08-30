"""Command line entry point.

    python -m annotation_fix input.json -o corrected.json --image-root local-data/

Writes a new file rather than editing in place, so a bad run never costs the
original annotations. ``--dry-run`` reports what would change without writing
anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .clamp import DEFAULT_FLAG_DISTANCE, DEFAULT_INSET
from .core import Options, process_document
from .mask import DEFAULT_THRESHOLD
from .autoconnect import DEFAULT_MIN_ANGLE, DEFAULT_TOLERANCE


def build_parser() -> argparse.ArgumentParser:
    """Define the command line interface."""
    parser = argparse.ArgumentParser(
        prog="annotation_fix",
        description=(
            "Clamp out-of-ROI annotation vertices back inside the imagery and "
            "auto-connect nearly-touching polyline endpoints."
        ),
    )
    parser.add_argument("input", help="Scalabel export JSON to correct")
    parser.add_argument(
        "-o", "--output", help="where to write the corrected JSON"
    )
    parser.add_argument(
        "--report", help="where to write the JSON run report"
    )
    parser.add_argument(
        "--image-root",
        default="",
        help="prefix for the relative image paths in frame names",
    )
    parser.add_argument(
        "--cache-dir",
        help="directory for memoised ROI masks (recommended for repeat runs)",
    )
    parser.add_argument(
        "--no-clamp", action="store_true", help="skip the ROI clamp stage"
    )
    parser.add_argument(
        "--no-connect", action="store_true", help="skip the auto-connect stage"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"black-padding threshold (default {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"endpoint connect radius in image px (default {DEFAULT_TOLERANCE})",
    )
    parser.add_argument(
        "--min-angle",
        type=float,
        default=DEFAULT_MIN_ANGLE,
        help="reject junctions straighter than this many degrees (0 disables)",
    )
    parser.add_argument(
        "--inset",
        type=float,
        default=DEFAULT_INSET,
        help=f"px to nudge clamped vertices inward (default {DEFAULT_INSET})",
    )
    parser.add_argument(
        "--flag-distance",
        type=float,
        default=DEFAULT_FLAG_DISTANCE,
        help=f"report corrections beyond this many px (default {DEFAULT_FLAG_DISTANCE})",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on a missing image instead of skipping its clamp stage",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing output",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="only process the first N frames (for a quick check)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the CLI. Returns a process exit code."""
    args = build_parser().parse_args(argv)

    if not args.dry_run and not args.output:
        print("error: --output is required unless --dry-run", file=sys.stderr)
        return 2

    with open(args.input, "r", encoding="utf-8") as handle:
        document = json.load(handle)

    if args.limit is not None and isinstance(document, dict):
        frames = document.get("frames") or []
        document = dict(document, frames=frames[: args.limit])

    options = Options(
        clamp=not args.no_clamp,
        connect=not args.no_connect,
        threshold=args.threshold,
        tolerance=args.tolerance,
        min_angle=args.min_angle,
        inset=args.inset,
        flag_distance=args.flag_distance,
        image_root=args.image_root,
        cache_dir=args.cache_dir,
        strict=args.strict,
    )

    corrected, report = process_document(document, options)

    print(f"frames processed : {len(report.frames)}")
    print(f"vertices clamped : {report.total_clamped}")
    print(f"lines merged     : {report.total_merged}")
    print(f"flagged for review: {report.total_flagged}")
    missing = report.missing_images
    if missing:
        print(f"images not found : {len(missing)}")
        for name in missing[:5]:
            print(f"  - {name}")
        if len(missing) > 5:
            print(f"  ... and {len(missing) - 5} more")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2)
        print(f"report written   : {args.report}")

    if args.dry_run:
        print("(dry run: no output written)")
        return 0

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(corrected, handle)
    print(f"output written   : {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
