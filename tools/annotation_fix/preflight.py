"""Diagnose why auto-correction is not running on this machine.

Correction is deliberately non-fatal: if python is missing, a dependency is
absent, or a path resolves wrongly, project creation logs a line and keeps the
ORIGINAL annotations. That is the right trade for imports, but it means a
misconfigured machine looks identical to a working one — the project is created,
just uncorrected.

This script checks every environmental dependency the correction has and says
which one is broken, so the failure is visible instead of silent.

Run from the repo root:

    python3 tools/annotation_fix/preflight.py
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

# (module, pip name, what breaks without it)
REQUIRED = [
    ("PIL", "pillow", "reading the orthomosaic to build the ROI mask"),
    ("numpy", "numpy", "all mask and geometry maths"),
    ("scipy", "scipy", "hole filling and the nearest-inside lookup"),
]


def _ok(msg: str) -> None:
    print(f"  OK    {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


#: Names the server tries, in order, when SCALABEL_PYTHON is unset. Must match
#: PYTHON_CANDIDATES in app/src/server/annotation_fix.ts.
CANDIDATES = ["python3", "python", "py"]


def resolve_interpreter() -> "tuple[str, str] | tuple[None, None]":
    """Find the interpreter the server would spawn.

    Mirrors the server: try each candidate and accept the first that reports a
    version cleanly, which rules out the Windows Store alias that exits
    non-zero without being a real interpreter.
    """
    configured = os.environ.get("SCALABEL_PYTHON")
    names = [configured] if configured else CANDIDATES
    for name in names:
        try:
            out = subprocess.run(
                [name, "--version"], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            return name, (out.stdout or out.stderr).strip()
    return None, None


def check_python() -> bool:
    """The interpreter the server will spawn, honouring SCALABEL_PYTHON."""
    exe, version = resolve_interpreter()
    if exe is not None:
        _ok(f"interpreter '{exe}' -> {version}")
        return True

    tried = os.environ.get("SCALABEL_PYTHON") or ", ".join(CANDIDATES)
    _fail(f"no interpreter found (tried: {tried})")
    print("        the server spawns these by name; install Python, or set")
    print("        SCALABEL_PYTHON to an absolute path")
    if sys.platform == "win32" and not os.environ.get("SCALABEL_PYTHON"):
        # Windows installers provide python.exe / py.exe. "python3" usually
        # exists only as a Store alias that is not a real interpreter, which is
        # why the server probes each name rather than trusting one.
        print()
        print("        On Windows install Python from python.org and tick")
        print("        'Add python.exe to PATH', or set SCALABEL_PYTHON=python")
    return False


def check_deps() -> bool:
    """Third-party packages, which are NOT installed by npm install."""
    missing = []
    for module, pip_name, why in REQUIRED:
        try:
            mod = importlib.import_module(module)
        except ImportError:
            _fail(f"{module} missing — needed for {why}")
            missing.append(pip_name)
            continue
        _ok(f"{module} {getattr(mod, '__version__', '?')}")
    if missing:
        print()
        print(f"        fix: pip install {' '.join(missing)}")
        return False
    return True


def check_paths(skip_image_root: bool = False) -> bool:
    """tools/ and local-data/ resolve against the server's WORKING DIRECTORY.

    :param skip_image_root: skip the data directory, which during a container
        build is still an unmounted volume.
    """
    good = True
    tools_dir = os.environ.get("SCALABEL_ANNOTATION_FIX_DIR") or os.path.join(
        os.getcwd(), "tools"
    )
    if os.path.isdir(os.path.join(tools_dir, "annotation_fix")):
        _ok(f"tools dir {tools_dir}")
    else:
        _fail(f"tools dir not found at {tools_dir}")
        print("        start the server from the repo root, or set")
        print("        SCALABEL_ANNOTATION_FIX_DIR")
        good = False

    if skip_image_root:
        print("  SKIP  data directory is mounted at runtime (--build)")
        return good

    image_root = os.environ.get(
        "SCALABEL_ANNOTATION_FIX_IMAGE_ROOT"
    ) or os.path.join(os.getcwd(), "local-data")
    if os.path.isdir(image_root):
        _ok(f"image root {image_root}")
    else:
        # Clamping silently no-ops when images cannot be found; auto-connect
        # still runs, so this looks like a half-working correction.
        _fail(f"image root not found at {image_root}")
        print("        without it every image is 'missing' and the ROI clamp")
        print("        is skipped while auto-connect still runs")
        print("        set SCALABEL_ANNOTATION_FIX_IMAGE_ROOT")
        good = False
    return good


def check_roundtrip() -> bool:
    """Run the real stdio entry point the server uses."""
    import json

    payload = {
        "document": [
            {
                "name": "preflight.png",
                "url": "preflight.png",
                "labels": [
                    {
                        "id": "a",
                        "category": "curb_road_edge",
                        "poly2d": [
                            {
                                "vertices": [[0, 0], [100, 0]],
                                "types": "LL",
                                "closed": False,
                            }
                        ],
                    },
                    {
                        "id": "b",
                        "category": "without_curb_road_edge",
                        "poly2d": [
                            {
                                "vertices": [[101, 0], [200, 0]],
                                "types": "LL",
                                "closed": False,
                            }
                        ],
                    },
                ],
            }
        ],
        "image_root": "/nonexistent",
        "clamp": False,
    }
    tools_dir = os.environ.get("SCALABEL_ANNOTATION_FIX_DIR") or os.path.join(
        os.getcwd(), "tools"
    )
    exe, _ = resolve_interpreter()
    if exe is None:
        _fail("no interpreter to run the correction child")
        return False
    try:
        proc = subprocess.run(
            [exe, "-m", "annotation_fix.stdio"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=tools_dir,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(f"could not run the correction child: {exc}")
        return False
    if proc.returncode != 0:
        _fail(f"correction child exited {proc.returncode}")
        print(f"        {proc.stderr.strip().splitlines()[-1:] or ['']}")
        return False
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        _fail("correction child produced no usable JSON")
        return False
    labels = result.get("document", [{}])[0].get("labels", [])
    if len(labels) == 1:
        _ok("end-to-end: two touching road-edge lines merged into one")
        return True
    _fail(f"end-to-end: expected 1 merged label, got {len(labels)}")
    return False


def main() -> int:
    """Run every check and report what needs fixing.

    ``--build`` skips the checks that only make sense at runtime, so a
    container build can verify the interpreter and packages are present while
    the data directory is still an unmounted volume.
    """
    print("Scalabel annotation auto-correct preflight")
    print()
    print("interpreter")
    ok_python = check_python()
    print()
    print("python packages")
    ok_deps = check_deps()
    print()
    build_only = "--build" in sys.argv
    print("paths (resolved against the CURRENT working directory)")
    ok_paths = check_paths(skip_image_root=build_only)
    print()
    print("end-to-end")
    ok_run = check_roundtrip() if (ok_python and ok_deps and ok_paths) else False
    # The round trip needs no images (clamp is off), so it is meaningful even
    # during a build.
    if not (ok_python and ok_deps and ok_paths):
        print("  SKIP  earlier checks failed")

    print()
    if ok_python and ok_deps and ok_paths and ok_run:
        print("RESULT: auto-correction will work on this machine.")
        return 0
    print("RESULT: auto-correction will SILENTLY SKIP on this machine.")
    print("Projects will still be created — with UNCORRECTED annotations.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
