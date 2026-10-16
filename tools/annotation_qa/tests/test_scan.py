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
    frames += [_frame("x208199.png"), _frame("MLC 1 a.png")]
    exp = _write_export(tmp_path / "ex.json", frames)
    out = tmp_path / "out"
    run_scan([exp], root, out, ["fake"], subset="reviewer+random20")
    names = [i.frame_name for i in read_issues(out / "issues_fake.jsonl")]
    assert {"x208199.png", "MLC 1 a.png"} <= set(names)
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
