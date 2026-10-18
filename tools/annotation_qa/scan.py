"""Run detectors over Scalabel exports; write issues, crops and a summary.

Results for each type go to ``issues_<type>.jsonl.partial`` while the scan runs
and replace ``issues_<type>.jsonl`` only when it finishes, so an interrupted
run never destroys the previous results. ``issues_<type>.meta.json`` records,
per export, the sha1 of the export file that was scanned and when; ``fix``
uses it to refuse issues detected on a different version of the working copy,
and ``--since-merge`` to find the frames merged since.
"""

from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing
import os
import random
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2

from annotation_qa import rounds
from annotation_qa.context import FrameContext
from annotation_qa.data import (REVIEWER_FRAMES, Export, image_path, load_export,
                                reviewer_match)
from annotation_qa.detectors import REGISTRY
from annotation_qa.imaging import FrameImage
from annotation_qa.issues import (append_issues, file_sha1, load_issues_tolerant, read_meta,
                                  write_issues, write_meta)
from annotation_qa.render import issue_crop

log = logging.getLogger("annotation_qa.scan")

__all__ = ["FrameContext", "ScanSummary", "run_scan", "REVIEWER_FRAMES", "since_merge_frames"]

RANDOM_PER_EXPORT = 20


@dataclass
class ScanSummary:
    frames: int = 0
    skipped: list[str] = field(default_factory=list)  # unreadable images
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)  # exceptions / failed writes


def _subset(frames: list[dict], subset: str | None) -> list[dict]:
    if subset is None:
        return frames
    if subset != "reviewer+random20":
        raise ValueError(f"unknown subset: {subset}")
    is_rev = [reviewer_match(f["name"]) is not None for f in frames]
    rest = [i for i, r in enumerate(is_rev) if not r]
    picked = set(random.Random(0).sample(rest, min(RANDOM_PER_EXPORT, len(rest))))
    return [f for i, f in enumerate(frames) if is_rev[i] or i in picked]


def _save_summary(out: Path, s: ScanSummary) -> None:
    """Write this run's summary; the counts of types this run did not scan are kept
    from the previous summary, so a one-type scan does not wipe the others."""
    out.mkdir(parents=True, exist_ok=True)
    p = out / "scan_summary.json"
    try:
        old = json.loads(p.read_text()).get("counts") or {}
        old = old if isinstance(old, dict) else {}
    except (OSError, ValueError, AttributeError):
        old = {}
    counts = {**{k: v for k, v in old.items() if k not in s.counts}, **s.counts}
    p.write_text(json.dumps(
        {"frames": s.frames, "skipped": s.skipped, "counts": counts, "errors": s.errors},
        indent=1))


def _last_scan(out: Path, issue_type: str, stem: str, meta: dict | None) -> datetime:
    """When ``stem`` was last scanned for ``issue_type`` (meta, else the issues file mtime)."""
    rec = (meta or {}).get(stem) or {}
    try:
        t = datetime.fromisoformat(rec["scanned_at"])
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        p = out / f"issues_{issue_type}.jsonl"
        return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)


def since_merge_frames(out: Path, issue_type: str, stems: list[str]) -> dict[str, set[str]]:
    """stem -> frame names merged after the last ``issue_type`` scan of that stem."""
    out = Path(out)
    meta = read_meta(out, issue_type)
    res: dict[str, set[str]] = {}
    for m in rounds.merge_markers(out):
        stem = m.get("stem")
        if stem not in stems:
            continue
        t = rounds.marker_time(m)
        if t is None or t <= _last_scan(out, issue_type, stem, meta):
            continue
        names = m.get("frames")
        if names is None:  # an older marker: fall back to the round's items file
            try:
                names = [f["name"] for f in load_export(
                    out / "rounds" / f"{m['tag']}_items.json").frames]
            except (OSError, ValueError, KeyError) as e:
                log.warning("cannot list frames of merged round %s: %s", m.get("tag"), e)
                names = []
        res.setdefault(stem, set()).update(names)
    return res


def _fname(frame) -> str:
    return frame.get("name", "?") if isinstance(frame, dict) else "?"


def _frame_job(job) -> dict:
    """Detect one frame (in the parent or a pool worker): load its image, run the
    detectors in ``types`` and render each issue's crop as JPEG bytes. The parent does
    all bookkeeping (dedup, crop files, .partial appends, progress, errors) in frame
    order, so results do not depend on the number of workers."""
    stem, frame, root, out, types = job
    try:
        img = FrameImage(image_path(frame, root), out / "roi_cache")
        img.bgr  # noqa: B018 - force the load
    except FileNotFoundError as e:
        return {"status": "skipped", "msg": str(e)}
    except Exception as e:
        return {"status": "error", "msg": f"{type(e).__name__}: {e}"}
    res = {"status": "ok", "found": {}, "failed": {}}
    ctx = FrameContext(Export(Path(f"{stem}.json"), [frame], {}), frame, img)
    try:
        for t in types:
            try:
                found = REGISTRY[t](ctx)
            except Exception as e:
                res["failed"][t] = f"{type(e).__name__}: {e}"
                continue
            rows = []
            for issue in found:
                try:
                    ok, buf = cv2.imencode(".jpg", issue_crop(img, frame, issue))
                    rows.append((issue, buf.tobytes() if ok else None, None))
                except Exception as e:
                    rows.append((issue, None, f"{type(e).__name__}: {e}"))
            res["found"][t] = rows
    finally:
        img.release()
    return res


_JOB = _frame_job  # what the workers run (a test swaps in a crashing one)
IN_FLIGHT_PER_WORKER = 2
CRASH_MSG = ("worker process died while detecting this frame (out of memory on a large "
             "image?); retried once in a fresh pool")


class _Pool:
    """A spawn-context ProcessPoolExecutor that can be replaced after a worker dies."""

    def __init__(self, workers: int):
        self.workers = workers
        self.ex = self._new()

    def _new(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(self.workers, mp_context=multiprocessing.get_context("spawn"))

    def submit(self, job):
        return self.ex.submit(_JOB, job)

    def restart(self) -> None:
        _kill_pool(self.ex)
        self.ex = self._new()

    def close(self) -> None:
        self.ex.shutdown(wait=True)

    def kill(self) -> None:
        _kill_pool(self.ex)


def _kill_pool(ex: ProcessPoolExecutor) -> None:
    """Stop a pool now: drop queued jobs and terminate its worker processes (Ctrl-C, errors,
    a broken pool); ``shutdown`` alone would wait for running detections."""
    procs = list((getattr(ex, "_processes", None) or {}).values())
    ex.shutdown(wait=False, cancel_futures=True)
    for p in procs:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001 - already gone
            pass
    for p in procs:
        try:
            p.join(5)
        except Exception:  # noqa: BLE001
            pass


def _ordered_results(jobs, pool):
    """Yield the result of every job in order, with at most ``IN_FLIGHT_PER_WORKER`` x
    workers submitted and not yet yielded. When a worker dies (BrokenProcessPool), every
    job in flight is lost: the current job is retried alone in a fresh pool, and if that
    dies too it yields {"status": "crashed"}; the rest are resubmitted."""
    pending: deque = deque()
    it = iter(jobs)
    limit = max(1, IN_FLIGHT_PER_WORKER * pool.workers)

    def fill():
        while len(pending) < limit:
            job = next(it, None)
            if job is None:
                return
            pending.append((job, pool.submit(job)))
    fill()
    while pending:
        job, fut = pending.popleft()
        try:
            res = fut.result()
        except BrokenProcessPool:
            rest = [j for j, _ in pending]
            pending.clear()
            pool.restart()
            try:
                res = pool.submit(job).result()
            except BrokenProcessPool:
                pool.restart()
                res = {"status": "crashed", "msg": CRASH_MSG}
            for j in rest:
                pending.append((j, pool.submit(j)))
        fill()
        yield res


def run_scan(exports: list[Path], root: Path, out: Path, types: list[str],
             subset: str | None = None, since_merge: bool = False,
             workers: int = 1) -> ScanSummary:
    """Scan ``exports`` (each stem's working copy when there is one) for ``types``.

    ``workers`` > 1 detects frames in a spawn-context process pool (each worker loads
    its own images); issues, crops and the summary are identical to a 1-worker run. At most
    2 x workers frames are in flight. A frame whose worker dies twice is recorded as an
    error and the scan carries on; on Ctrl-C or an error the workers are terminated.

    ``since_merge``: re-detect only the frames listed in merge markers newer than
    each type's last scan, and replace exactly those frames' issues in
    ``issues_<type>.jsonl``, keeping all others.
    """
    types = list(dict.fromkeys(types))
    unknown = [t for t in types if t not in REGISTRY]
    if unknown:
        raise ValueError(f"unknown issue types: {unknown}; known: {sorted(REGISTRY)}")
    if since_merge and subset:
        raise ValueError("--since-merge and --subset cannot be combined")
    out = Path(out)
    summary = ScanSummary(counts={t: 0 for t in types})
    out.mkdir(parents=True, exist_ok=True)
    stems = [Path(p).stem for p in exports]
    final = {t: out / f"issues_{t}.jsonl" for t in types}
    partial = {t: out / f"issues_{t}.jsonl.partial" for t in types}
    wanted: dict[str, dict[str, set[str]]] = {}  # since_merge: type -> stem -> frame names
    meta: dict[str, dict] = {t: {} for t in types}
    if since_merge:
        missing = [t for t in types if not final[t].exists()]
        if missing:
            raise ValueError(f"no previous scan of {missing} in {out}; run a full scan first")
        for t in types:
            wanted[t] = since_merge_frames(out, t, stems)
            drop = {(s, n) for s, names in wanted[t].items() for n in names}
            kept = [i for i in load_issues_tolerant(final[t])
                    if (i.export_stem, i.frame_name) not in drop]
            write_issues(partial[t], kept)  # the untouched issues, then the new ones
            meta[t] = read_meta(out, t) or {}
            print(f"{t}: re-detecting {len(drop)} merged frames "
                  f"({len(kept)} other issues kept)", flush=True)
    else:
        for t in types:  # issues are appended frame by frame
            partial[t].write_text("")

    def fail(msg: str) -> None:
        log.error(msg)
        summary.errors.append(msg)

    pool = _Pool(workers) if workers > 1 else None
    try:
        _scan_exports(exports, root, out, types, subset, since_merge, wanted, meta, partial,
                      summary, fail, pool)
    except BaseException:  # Ctrl-C or an error: do not wait for (or leak) the workers
        if pool is not None:
            pool.kill()
        raise
    if pool is not None:
        pool.close()
    for t in types:  # the run finished: the new results replace the old ones
        os.replace(partial[t], final[t])
        write_meta(out, t, meta[t])
    _save_summary(out, summary)
    return summary


def _scan_exports(exports, root, out, types, subset, since_merge, wanted, meta, partial,
                  summary, fail, pool) -> None:
    for path in exports:
        stem = Path(path).stem
        work = out / "work" / f"{stem}.json"
        src = work if work.exists() else Path(path)
        started = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        try:
            sha = file_sha1(src)
            exp = load_export(src)
            if since_merge:
                todo = {t: wanted[t].get(stem, set()) for t in types}
                names = set().union(*todo.values())
                frames = [f for f in exp.frames if f.get("name") in names]
            else:
                todo = None
                frames = _subset(exp.frames, subset)
        except Exception as e:
            fail(f"{stem}: cannot load export: {type(e).__name__}: {e}")
            continue
        seen: set[str] = set()
        jobs = [(exp.stem, frame, Path(root), out,
                 [t for t in types if todo is None or _fname(frame) in todo[t]])
                for frame in frames]
        results = _ordered_results(jobs, pool) if pool is not None else map(_JOB, jobs)
        for n, (frame, res) in enumerate(zip(frames, results), 1):
            fname = _fname(frame)
            tag = f"{exp.stem}|{fname}"
            if res["status"] == "skipped":
                log.warning("skipping %s: %s", tag, res["msg"])
                summary.skipped.append(tag)
                print(f"[{n}/{len(frames)}] {tag}: skipped", flush=True)
                continue
            if res["status"] == "error":
                fail(f"{tag}: cannot open frame: {res['msg']}")
                continue
            if res["status"] == "crashed":
                fail(f"{tag}: {res['msg']}")
                print(f"[{n}/{len(frames)}] {tag}: worker crashed", flush=True)
                continue
            k = 0
            for t in types:
                if t not in res["found"] and t not in res["failed"]:
                    continue
                if t in res["failed"]:
                    fail(f"{tag}: detector {t} failed: {res['failed'][t]}")
                    continue
                kept = []
                for issue, jpg, err in res["found"][t]:
                    if issue.key in seen:
                        log.warning("duplicate issue key, keeping first: %s", issue.key)
                        continue
                    seen.add(issue.key)
                    name = hashlib.sha1(issue.key.encode()).hexdigest()[:12] + ".jpg"
                    if err is not None:
                        fail(f"{tag}: crop failed for {issue.key}: {err}")
                    elif jpg is None:
                        fail(f"{tag}: crop write failed for {issue.key}")
                    else:
                        try:
                            cdir = out / "crops" / t
                            cdir.mkdir(parents=True, exist_ok=True)
                            (cdir / name).write_bytes(jpg)
                        except Exception as e:
                            fail(f"{tag}: crop write failed for {issue.key}: "
                                 f"{type(e).__name__}: {e}")
                    kept.append(issue)
                append_issues(partial[t], kept)
                summary.counts[t] += len(kept)
                k += len(kept)
            summary.frames += 1
            print(f"[{n}/{len(frames)}] {tag}: {k} issues", flush=True)
        for t in types:
            meta[t][stem] = {"sha1": sha, "scanned_at": started}
        _save_summary(out, summary)
