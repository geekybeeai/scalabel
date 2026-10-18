import json
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from annotation_qa.data import Export
from annotation_qa.detectors import REGISTRY
from annotation_qa.imaging import FrameImage
from annotation_qa.issues import (Issue, read_issues, read_marks, write_issues,
                                  write_marks)
from annotation_qa.render import issue_crop
from annotation_qa.scan import FrameContext, run_scan
from annotation_qa.__main__ import main


def _poly(y):
    return {"vertices": [[50, y], [350, y]], "types": "LL", "closed": False}


def _label(i, cat="solid_single_line", y=100):
    return {"id": str(i), "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [_poly(y)], "box2d": None, "box3d": None}


def _write_export(path: Path, frames):
    path.write_text(json.dumps({"frames": frames, "config": {}}))
    return path


def _img(root: Path, rel: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    img = np.zeros((400, 400, 3), np.uint8)
    img[10:390, 10:390] = 110
    cv2.imwrite(str(p), img)


def _frame(name, url="items/a.png", labels=None):
    return {"name": name, "url": url, "labels": labels or [_label(1)]}


@pytest.fixture
def fake_detector(monkeypatch):
    def det(ctx):
        lab = ctx.frame["labels"][0]
        return [Issue("fake", ctx.export.stem, ctx.frame["name"], lab["id"],
                      lab["category"], .9, "r", (200.0, 100.0), None, False)]
    monkeypatch.setitem(REGISTRY, "fake", det)
    return det


@pytest.fixture
def export_with_one_missing_image(tmp_path):
    root = tmp_path / "root"
    _img(root, "items/a.png")
    return _write_export(tmp_path / "ex1.json",
                         [_frame("a.png"), _frame("b.png", "items/missing.png")]), root


def test_scan_skips_missing_image_and_continues(tmp_path, export_with_one_missing_image, fake_detector):
    exp, root = export_with_one_missing_image
    out = tmp_path / "out"
    s = run_scan([exp], root, out, ["fake"])
    assert len(s.skipped) == 1 and s.counts["fake"] == 1 and s.frames == 1
    issues = read_issues(out / "issues_fake.jsonl")
    assert len(issues) == 1
    crop = out / "crops" / "fake" / (__import__("hashlib").sha1(issues[0].key.encode()).hexdigest()[:12] + ".jpg")
    assert crop.exists()
    summary = json.loads((out / "scan_summary.json").read_text())
    assert summary["frames"] == 1 and summary["counts"] == {"fake": 1} and len(summary["skipped"]) == 1


def test_scan_summary_keeps_other_types_counts(tmp_path, export_with_one_missing_image,
                                               fake_detector):
    exp, root = export_with_one_missing_image
    out = tmp_path / "out"
    out.mkdir()
    (out / "scan_summary.json").write_text(json.dumps({"counts": {"other": 7, "fake": 3}}))
    run_scan([exp], root, out, ["fake"])
    assert json.loads((out / "scan_summary.json").read_text())["counts"] == {"other": 7, "fake": 1}


def test_scan_unknown_type_raises(tmp_path, export_with_one_missing_image):
    exp, root = export_with_one_missing_image
    with pytest.raises(ValueError):
        run_scan([exp], root, tmp_path / "o", ["nope"])


def test_scan_prefers_working_copy(tmp_path, fake_detector):
    root = tmp_path / "root"
    _img(root, "items/a.png")
    orig = _write_export(tmp_path / "ex.json", [_frame("orig.png")])
    out = tmp_path / "out"
    (out / "work").mkdir(parents=True)
    _write_export(out / "work" / "ex.json", [_frame("work.png")])
    run_scan([orig], root, out, ["fake"])
    assert [i.frame_name for i in read_issues(out / "issues_fake.jsonl")] == ["work.png"]


def test_subset_keeps_reviewer_frames_and_caps_random(tmp_path, fake_detector):
    root = tmp_path / "root"
    _img(root, "items/a.png")
    frames = [_frame(f"f{i}.png") for i in range(60)]
    frames += [_frame("items/b/208199_verification.png"), _frame("MLC 1_verification_z20.png")]
    exp = _write_export(tmp_path / "ex.json", frames)
    out = tmp_path / "out"
    run_scan([exp], root, out, ["fake"], subset="reviewer+random20")
    names = [i.frame_name for i in read_issues(out / "issues_fake.jsonl")]
    assert {"items/b/208199_verification.png", "MLC 1_verification_z20.png"} <= set(names)
    assert len(names) == 22
    out2 = tmp_path / "out2"
    run_scan([exp], root, out2, ["fake"], subset="reviewer+random20")
    assert names == [i.frame_name for i in read_issues(out2 / "issues_fake.jsonl")]


def test_issue_key_distinguishes_exports():
    a = Issue("shape", "exportA", "f.png", "L1", "solid_single_line", .9, "r", (0, 0), None, False)
    b = replace(a, export_stem="exportB")
    assert a.key != b.key


def test_issues_roundtrip_and_marks(tmp_path):
    a = Issue("shape", "e", "f.png", "L1", "solid_single_line", .9, "r", (1.5, 2.0),
              {"op": "shift", "poly_index": 0, "offsets": [1.0, 2.0]}, False, ["L2"])
    b = replace(a, label_id="L3", fix=None, hand_fix=True, related_label_ids=[])
    write_issues(tmp_path / "i.jsonl", [a, b])
    assert read_issues(tmp_path / "i.jsonl") == [a, b]
    assert read_marks(tmp_path / "nomarks.json") == {}
    write_marks(tmp_path / "m.json", {a.key: "real", b.key: "false"})
    assert read_marks(tmp_path / "m.json") == {a.key: "real", b.key: "false"}


@pytest.fixture
def synthetic_frame_ctx(tmp_path):
    _img(tmp_path, "a.png")
    frame = _frame("a.png", labels=[_label(1, y=100), _label(2, "curb_road_edge", y=130)])
    img = FrameImage(tmp_path / "a.png", tmp_path / "roi")
    return FrameContext(Export(Path("e.json"), [frame], {}), frame, img)


def _issue(label="1", related=()):
    return Issue("shape", "e", "a.png", label, "solid_single_line", .9, "r",
                 (200.0, 100.0), None, False, list(related))


def test_crop_highlights_label_in_magenta(synthetic_frame_ctx):
    c = synthetic_frame_ctx
    crop = issue_crop(c.image, c.frame, _issue())
    right = crop[:, crop.shape[1] // 2 + 4:]
    left = crop[:, :crop.shape[1] // 2 - 4]
    assert ((right == [255, 0, 255]).all(-1)).sum() > 50
    assert not ((left == [255, 0, 255]).all(-1)).any()


def test_crop_related_in_cyan(synthetic_frame_ctx):
    c = synthetic_frame_ctx
    crop = issue_crop(c.image, c.frame, _issue(related=["2"]))
    right = crop[:, crop.shape[1] // 2 + 4:]
    assert ((right == [255, 255, 0]).all(-1)).sum() > 50


def test_crop_clipped_at_border(synthetic_frame_ctx):
    c = synthetic_frame_ctx
    iss = replace(_issue(), anchor=(0.0, 0.0))
    crop = issue_crop(c.image, c.frame, iss)
    assert crop.shape == issue_crop(c.image, c.frame, _issue()).shape


def test_cli_scan(tmp_path, export_with_one_missing_image, fake_detector):
    exp, root = export_with_one_missing_image
    out = tmp_path / "out"
    rc = main(["scan", "--exports", str(exp.parent), "--root", str(root),
               "--out", str(out), "--types", "fake"])
    assert rc == 0 and (out / "issues_fake.jsonl").exists()


def test_detector_error_is_recorded_and_scan_continues(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _img(root, "items/a.png")
    exp = _write_export(tmp_path / "ex.json", [_frame(f"f{i}.png") for i in range(3)])

    def det(ctx):
        if ctx.frame["name"] == "f1.png":
            raise RuntimeError("boom")
        return [Issue("fake", ctx.export.stem, ctx.frame["name"], "1", "c", .9, "r", (200., 100.), None, False)]
    monkeypatch.setitem(REGISTRY, "fake", det)
    out = tmp_path / "out"
    s = run_scan([exp], root, out, ["fake", "fake"])
    assert len(s.errors) == 1 and "boom" in s.errors[0] and s.counts == {"fake": 2}
    assert [i.frame_name for i in read_issues(out / "issues_fake.jsonl")] == ["f0.png", "f2.png"]
    assert len(json.loads((out / "scan_summary.json").read_text())["errors"]) == 1


def test_bad_export_is_recorded(tmp_path, fake_detector):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    s = run_scan([bad], tmp_path, tmp_path / "out", ["fake"])
    assert len(s.errors) == 1


def test_duplicate_key_keeps_first(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _img(root, "items/a.png")
    exp = _write_export(tmp_path / "ex.json", [_frame("f.png")])
    mk = lambda r: Issue("fake", "ex", "f.png", "1", "c", .9, r, (200., 100.), None, False)
    monkeypatch.setitem(REGISTRY, "fake", lambda ctx: [mk("first"), mk("second")])
    out = tmp_path / "out"
    s = run_scan([exp], root, out, ["fake"])
    assert [i.reason for i in read_issues(out / "issues_fake.jsonl")] == ["first"]
    assert s.counts["fake"] == 1


# ---- reviewer frames (I8) ----
def test_reviewer_match_is_a_basename_prefix():
    from annotation_qa.data import reviewer_match
    d = "items/Full_Verfication/batchwise_data/b/images/"
    assert reviewer_match(d + "565_verification.png") == "565_verification"
    assert reviewer_match(d + "113652_211565_verification.png") is None  # the false hit
    assert reviewer_match(d + "MLC 1_verification_z20.png") == "MLC 1"
    assert reviewer_match(d + "MLC 10_verification.png") is None
    assert reviewer_match(d + "208199_verification.png") == "208199"
    assert reviewer_match(d + "100292_200553_verification.png") == "100292_200553"


# ---- partial output, meta, CLI (M3, M4, C1) ----
def test_interrupted_scan_keeps_previous_results(tmp_path, monkeypatch, fake_detector):
    root = tmp_path / "root"
    _img(root, "items/a.png")
    exp = _write_export(tmp_path / "ex.json", [_frame(f"f{i}.png") for i in range(3)])
    out = tmp_path / "out"
    run_scan([exp], root, out, ["fake"])
    before = (out / "issues_fake.jsonl").read_text()
    assert len(before.splitlines()) == 3

    def boom(ctx):
        if ctx.frame["name"] == "f1.png":
            raise KeyboardInterrupt
        return fake_detector(ctx)
    monkeypatch.setitem(REGISTRY, "fake", boom)
    with pytest.raises(KeyboardInterrupt):
        run_scan([exp], root, out, ["fake"])
    assert (out / "issues_fake.jsonl").read_text() == before
    assert (out / "issues_fake.jsonl.partial").exists()


def test_scan_writes_meta_with_sha1_of_scanned_file(tmp_path, fake_detector):
    from annotation_qa.issues import file_sha1, read_meta
    root = tmp_path / "root"
    _img(root, "items/a.png")
    exp = _write_export(tmp_path / "ex.json", [_frame("a.png")])
    out = tmp_path / "out"
    run_scan([exp], root, out, ["fake"])
    meta = read_meta(out, "fake")
    assert meta["ex"]["sha1"] == file_sha1(exp) and meta["ex"]["scanned_at"]
    (out / "work").mkdir()
    work = _write_export(out / "work" / "ex.json", [_frame("w.png")])
    run_scan([exp], root, out, ["fake"])
    assert read_meta(out, "fake")["ex"]["sha1"] == file_sha1(work)


def test_cli_subset_needs_explicit_out(tmp_path, export_with_one_missing_image, fake_detector,
                                       capsys):
    exp, root = export_with_one_missing_image
    rc = main(["scan", "--exports", str(exp), "--root", str(root), "--types", "fake",
               "--subset", "reviewer+random20"])
    assert rc == 2 and "explicit --out" in capsys.readouterr().out


def test_cli_scan_reports_errors_and_exits_1(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    _img(root, "items/a.png")
    exp = _write_export(tmp_path / "ex.json", [_frame("a.png")])

    def det(ctx):
        raise RuntimeError("boom")
    monkeypatch.setitem(REGISTRY, "fake", det)
    rc = main(["scan", "--exports", str(exp), "--root", str(root), "--out", str(tmp_path / "o"),
               "--types", "fake"])
    text = capsys.readouterr().out
    assert rc == 1 and "errors=1" in text and "boom" in text


# ---- incremental re-detection (I11) ----
def _merged_env(tmp_path, monkeypatch):
    """A full scan of 3 frames, then a merged round touching f1 only."""
    from annotation_qa import fix as fixmod
    from annotation_qa.merge import merge_reviewed
    root = tmp_path / "root"
    _img(root, "items/a.png")
    orig_dir = tmp_path / "orig"
    orig_dir.mkdir()
    exp = _write_export(orig_dir / "ex.json", [_frame(f"f{i}.png") for i in range(3)])
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig_dir)
    calls = []

    def det(ctx):
        calls.append(ctx.frame["name"])
        lab = ctx.frame["labels"][0]
        return [Issue("fake", ctx.export.stem, ctx.frame["name"], lab["id"], lab["category"],
                      .9, f"cat={lab['category']}", (200.0, 100.0), None, False)]
    monkeypatch.setitem(REGISTRY, "fake", det)
    out = tmp_path / "out"
    run_scan([exp], root, out, ["fake"])
    tag = "ex_fix_fake_v1"
    (out / "rounds").mkdir()
    _write_export(out / "rounds" / f"{tag}_items.json", [_frame("f1.png")])
    (out / "rounds" / f"{tag}_changes.json").write_text("{}")
    rev = _write_export(tmp_path / "rev.json",
                        [_frame("f1.png", labels=[_label(1, "dashed_white_line")])])
    merge_reviewed("ex", rev, out, round_tag=tag)
    calls.clear()
    return exp, root, out, calls


def test_since_merge_rescans_only_merged_frames(tmp_path, monkeypatch):
    from annotation_qa.issues import file_sha1, read_meta
    exp, root, out, calls = _merged_env(tmp_path, monkeypatch)
    s = run_scan([exp], root, out, ["fake"], since_merge=True)
    assert calls == ["f1.png"] and s.frames == 1
    got = {i.frame_name: i.reason for i in read_issues(out / "issues_fake.jsonl")}
    assert got == {"f0.png": "cat=solid_single_line", "f1.png": "cat=dashed_white_line",
                   "f2.png": "cat=solid_single_line"}
    assert read_meta(out, "fake")["ex"]["sha1"] == file_sha1(out / "work" / "ex.json")
    # nothing merged since: a second run re-detects nothing and keeps everything
    run_scan([exp], root, out, ["fake"], since_merge=True)
    assert calls == ["f1.png"]
    assert len(read_issues(out / "issues_fake.jsonl")) == 3


def test_since_merge_then_fix_is_not_stale(tmp_path, monkeypatch):
    from annotation_qa import fix as fixmod
    from annotation_qa.fix import _staleness
    from annotation_qa.issues import read_meta
    exp, root, out, calls = _merged_env(tmp_path, monkeypatch)
    assert _staleness(out, "fake", "ex", read_meta(out, "fake"))  # merge made it stale
    main(["scan", "--exports", str(exp), "--root", str(root), "--out", str(out),
          "--types", "fake", "--since-merge"])
    assert _staleness(out, "fake", "ex", read_meta(out, "fake")) is None
    assert fixmod  # (ORIGINALS_DIR patched by _merged_env)


def test_since_merge_needs_a_previous_scan(tmp_path, fake_detector):
    exp = _write_export(tmp_path / "ex.json", [_frame("a.png")])
    with pytest.raises(ValueError, match="full scan first"):
        run_scan([exp], tmp_path, tmp_path / "o", ["fake"], since_merge=True)


def test_scan_with_two_workers_matches_one_worker(tmp_path):
    root = tmp_path / "root"
    img = np.zeros((400, 400, 3), np.uint8)
    img[10:390, 10:390] = 110
    img[10:200, 10:390] = 170  # concrete above y=200, asphalt below
    for i in range(3):
        p = root / f"items/e{i}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), img)
    frames = [_frame(f"e{i}.png", f"items/e{i}.png",
                     [_label(1, "curb_road_edge", 208 + i), _label(2, "curb_road_edge", 200)])
              for i in range(3)]
    frames.append(_frame("gone.png", "items/missing.png"))
    exp = _write_export(tmp_path / "exw.json", frames)
    res = {}
    for w in (1, 2):
        out = tmp_path / f"out{w}"
        s = run_scan([exp], root, out, ["edge_position", "position"], workers=w)
        crops = {p.relative_to(out).as_posix(): p.read_bytes()
                 for p in sorted((out / "crops").rglob("*.jpg"))}
        res[w] = (s.frames, s.skipped, s.counts, s.errors,
                  (out / "issues_edge_position.jsonl").read_text(),
                  (out / "issues_position.jsonl").read_text(),
                  json.loads((out / "scan_summary.json").read_text()), crops)
    assert res[1][2]["edge_position"] == 3 and len(res[1][1]) == 1
    assert res[1] == res[2]


# ---- review 2, I7: worker crashes, Ctrl-C, bounded submissions ----
def _die_on_crash_frames(job):
    """Worker job that kills its process on frames named crash*.png (like an OOM kill)."""
    import os as _os
    from annotation_qa import scan as _scan
    if job[1].get("name", "").startswith("crash"):
        _os._exit(3)
    return _scan._frame_job(job)


def _worker_export(tmp_path, names):
    root = tmp_path / "root"
    img = np.zeros((400, 400, 3), np.uint8)
    img[10:390, 10:390] = 110
    img[10:200, 10:390] = 170
    frames = []
    for n in names:
        p = root / f"items/{n}"
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), img)
        frames.append(_frame(n, f"items/{n}", [_label(1, "curb_road_edge", 210)]))
    return root, _write_export(tmp_path / "exw.json", frames)


def test_dead_worker_is_an_error_for_its_frame_not_a_hang(tmp_path, monkeypatch):
    from annotation_qa import scan
    monkeypatch.setattr(scan, "_JOB", _die_on_crash_frames)
    root, exp = _worker_export(tmp_path, ["e0.png", "crash1.png", "e2.png", "e3.png"])
    s = run_scan([exp], root, tmp_path / "out", ["edge_position"], workers=2)
    assert len(s.errors) == 1 and "crash1.png" in s.errors[0]
    assert "worker process died" in s.errors[0]
    assert s.frames == 3 and s.counts["edge_position"] == 3  # the other frames were detected
    names = {json.loads(l)["frame_name"] for l in
             (tmp_path / "out" / "issues_edge_position.jsonl").read_text().splitlines()}
    assert names == {"e0.png", "e2.png", "e3.png"}


def test_ctrl_c_terminates_the_pool(tmp_path, monkeypatch):
    from annotation_qa import scan
    killed = []
    real = scan._kill_pool
    monkeypatch.setattr(scan, "_kill_pool", lambda ex: (killed.append(ex), real(ex)))

    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(scan, "_scan_exports", boom)
    root, exp = _worker_export(tmp_path, ["e0.png"])
    with pytest.raises(KeyboardInterrupt):
        run_scan([exp], root, tmp_path / "out", ["edge_position"], workers=2)
    assert len(killed) == 1
    assert not (tmp_path / "out" / "issues_edge_position.jsonl").exists()  # old results kept


def test_in_flight_submissions_are_bounded():
    from concurrent.futures import Future
    from annotation_qa import scan

    class FakePool:
        workers = 3

        def __init__(self):
            self.submitted = 0

        def submit(self, job):
            self.submitted += 1
            f = Future()
            f.set_result({"job": job})
            return f
    pool = FakePool()
    gen = scan._ordered_results(range(100), pool)
    seen = []
    for res in gen:
        seen.append(res["job"])
        assert pool.submitted - len(seen) <= 2 * pool.workers
    assert seen == list(range(100))
