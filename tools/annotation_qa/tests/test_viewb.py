import json
import threading
import urllib.error
import urllib.request

import cv2
import numpy as np
import pytest

from annotation_qa import __main__ as cli
from annotation_qa.issues import Issue, write_marks
from annotation_qa.review_server import make_server
from annotation_qa.viewb import build_rows

STEM = "proj_export_a"


def _label(lid, cat="solid_single_line", y=100.0):
    return {"id": lid, "category": cat, "attributes": {}, "manualShape": True,
            "poly2d": [{"vertices": [[20, y], [180, y]], "types": "LL", "closed": False}],
            "box2d": None, "box3d": None}


def _snap(label):
    return {"category": label["category"], "poly2d": label["poly2d"]}


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _export(frames):
    return {"frames": frames, "config": {}}


def _frame(name, labels):
    return {"name": name, "url": f"{name}", "labels": labels}


def _round(out, issue_type, v, changes, items_frames, after_frames=None):
    tag = f"{STEM}_fix_{issue_type}_v{v}"
    _write(out / "rounds" / f"{tag}_changes.json", changes)
    _write(out / "rounds" / f"{tag}_items.json", _export(items_frames))
    _write(out / "work" / f"{tag}.json", _export(after_frames or items_frames))
    return tag


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


@pytest.fixture(autouse=True)
def originals(tmp_path, monkeypatch):
    """Never read (or copy) the real originals in these tests."""
    from annotation_qa import data
    from annotation_qa import fix as fixmod
    orig = tmp_path / "orig"
    orig.mkdir()
    monkeypatch.setattr(data, "ORIGINALS_DIR", orig)
    monkeypatch.setattr(fixmod, "ORIGINALS_DIR", orig)
    return orig


@pytest.fixture
def repo(tmp_path):
    return tmp_path


def test_frame_changed_in_two_rounds_listed_once_with_both_changes(out, repo):
    a1 = _label("1", "curb_road_edge")
    _round(out, "shape", 1, {"f1.png": {"1": {"before": _snap(_label("1", y=100)),
                                              "after": _snap(_label("1", y=110))}}},
           [_frame("f1.png", [_label("1", y=110)])])
    _round(out, "solid_dashed", 1, {"f1.png": {"1": {"before": _snap(_label("1", "solid_single_line")),
                                                      "after": _snap(a1)},
                                               "2": {"before": _snap(_label("2")), "after": None}}},
           [_frame("f1.png", [a1])])
    rows = build_rows(out, repo)
    assert len(rows) == 1
    r = rows[0]
    assert (r["export_stem"], r["frame_name"]) == (STEM, "f1.png")
    got = {(c["issue_type"], c["label_id"]): c for c in r["changes"]}
    assert set(got) == {("shape", "1"), ("solid_dashed", "1"), ("solid_dashed", "2")}
    assert got[("shape", "1")]["what"] == "moved 10 px"
    assert got[("solid_dashed", "1")]["what"] == "solid_single_line → curb_road_edge"
    assert got[("solid_dashed", "2")]["what"] == "deleted"
    assert got[("shape", "1")]["round"] == "shape_v1"
    assert len(r["projects"]) == 2 and r["projects"][0]["project"].startswith(f"{STEM}_fix_")


def test_frames_sorted_by_change_count(out, repo):
    ch = lambda lid: {"before": _snap(_label(lid)), "after": None}  # noqa: E731
    _round(out, "shape", 1,
           {"few.png": {"1": ch("1")}, "many.png": {"1": ch("1"), "2": ch("2"), "3": ch("3")}},
           [_frame("few.png", []), _frame("many.png", [])])
    rows = build_rows(out, repo)
    assert [r["frame_name"] for r in rows] == ["many.png", "few.png"]
    assert [len(r["changes"]) for r in rows] == [3, 1]


def test_hand_fix_status_done_after_merge(out, repo):
    pre = [_label("1")]
    _round(out, "position", 1, {}, [_frame("h.png", pre), _frame("g.png", pre)])
    # nothing merged yet: work copy equals pre-round labels
    _write(out / "work" / f"{STEM}.json", _export([_frame("h.png", pre), _frame("g.png", pre)]))
    rows = {r["frame_name"]: r for r in build_rows(out, repo)}
    assert rows["h.png"]["hand"] == [{"round": "position_v1", "done": False}]
    # reviewer redrew h.png and merged
    _write(out / "work" / f"{STEM}.json",
           _export([_frame("h.png", [_label("1", y=140)]), _frame("g.png", pre)]))
    rows = {r["frame_name"]: r for r in build_rows(out, repo)}
    assert rows["h.png"]["hand"][0]["done"] is True
    assert rows["g.png"]["hand"][0]["done"] is False


def test_project_link_uses_task_index(out, repo):
    tag = _round(out, "shape", 1, {"b.png": {"1": {"before": _snap(_label("1")), "after": None}}},
                 [_frame("a.png", []), _frame("b.png", [])])
    _write(repo / "local-data/scalabel/projects" / tag / "project.json",
           {"config": {"taskSize": 1}})
    (p,) = build_rows(out, repo)[0]["projects"]
    assert p["link"].endswith(f"project_name={tag}&task_index=1&item=1")


def _serve(out, repo):
    srv = make_server(out, port=0, repo_root=repo, originals_dir=repo / "orig")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_viewb_page_rows_and_crops(out, repo):
    img = np.full((200, 200, 3), 90, np.uint8)
    (repo / "local-data").mkdir()
    cv2.imwrite(str(repo / "local-data" / "f1.png"), img)
    frame = _frame("f1.png", [_label("1", y=110)])
    _round(out, "shape", 1, {"f1.png": {"1": {"before": _snap(_label("1", y=100)),
                                              "after": _snap(_label("1", y=110))}}}, [frame])
    srv = _serve(out, repo)
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        html = urllib.request.urlopen(base + "/viewb").read().decode()
        assert "View B" in html
        rows = json.loads(urllib.request.urlopen(base + "/viewb/rows").read())
        c = rows[0]["changes"][0]
        for side in ("before", "after"):
            data = urllib.request.urlopen(base + c[side + "_crop"]).read()
            assert data[:2] == b"\xff\xd8"
        assert list((out / "viewb").glob("*.jpg"))
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/viewb/crop?stem=x&frame=y&label=z&round=q&side=before")
        assert e.value.code == 404
        assert "/viewb" in urllib.request.urlopen(base + "/").read().decode()
    finally:
        srv.shutdown()
        srv.server_close()


# ---- bar / final ----
def _issue(t, lid, frame, stem=STEM):
    return Issue(t, stem, frame, lid, "solid_single_line", 0.9, "r", (1.0, 2.0), None, False, [])


def test_bar_lists_reviewer_frames_and_missing_real(out, capsys):
    d = "items/Full_Verfication/batchwise_data/b/images/"
    a = _issue("shape", "1", d + "208199_verification.png")
    b = _issue("position", "2", d + "208199_verification.png")
    c = _issue("shape", "3", d + "MLC 1_verification_z20.png")
    fake = _issue("shape", "4", d + "113652_211565_verification.png")  # not 565_verification
    out.mkdir()
    for t, lst in (("shape", [a, c, fake]), ("position", [b])):
        (out / f"issues_{t}.jsonl").write_text("".join(json.dumps(i.__dict__) + "\n" for i in lst))
    write_marks(out / "marks.json", {b.key: "real", c.key: "false"})
    assert cli.main(["bar", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    line = next(l for l in text.splitlines() if "208199" in l)
    assert "shape=1" in line and "position=1" in line and "real=1" in line
    assert "No real-marked issue: " in text
    missing = text.split("No real-marked issue: ")[1]
    assert "MLC 1" in missing and "208199" not in missing and "207851" in missing
    assert "565_verification: no issues" in text  # the 211565 frame is not a false hit


def _merge(out, tag, tmp_path):
    from annotation_qa.merge import merge_reviewed
    rev = tmp_path / f"{tag}_reviewed.json"
    _write(rev, _export([_frame("f1.png", [])]))
    return merge_reviewed(STEM, rev, out, round_tag=tag)


def _two_changes():
    return {"f1.png": {"1": {"before": _snap(_label("1")), "after": None},
                       "2": {"before": _snap(_label("2")), "after": None}}}


def test_final_copies_merged_stems_and_counts(out, tmp_path, capsys, originals):
    tag = _round(out, "shape", 1, _two_changes(), [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [_label("1")])]))
    _write(out / "work" / "other_export_b.json", _export([]))  # no rounds: copied as is
    _write(originals / "third_export_c.json", _export([_frame("t.png", [_label("7")])]))
    _merge(out, tag, tmp_path)
    assert cli.main(["final", "--out", str(out)]) == 0
    dest = out / "final" / f"{STEM}_qa_2026_10_17.json"
    assert json.loads(dest.read_text()) == _export([_frame("f1.png", [])])
    text = capsys.readouterr().out
    assert f"{STEM}: 2 changes" in text
    # I9: one corrected export per batch, also for batches no round touched
    assert "other_export_b: 0 changes" in text and "third_export_c: 0 changes" in text
    third = json.loads((out / "final" / "third_export_c_qa_2026_10_17.json").read_text())
    assert third == _export([_frame("t.png", [_label("7")])])
    assert (out / "final" / "other_export_b_qa_2026_10_17.json").exists()


def test_final_does_not_export_unmerged_stem(out, capsys, originals):
    _round(out, "shape", 1, _two_changes(), [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [_label("1")])]))
    _write(originals / "other_export_b.json", _export([]))
    assert cli.main(["final", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert not (out / "final" / f"{STEM}_qa_2026_10_17.json").exists()
    assert "NOT EXPORTED" in text and "shape_v1" in text and STEM in text
    assert "other_export_b: 0 changes" in text  # the untouched batch still ships


def test_final_skips_stem_with_one_unmerged_round(out, tmp_path, capsys):
    t1 = _round(out, "shape", 1, _two_changes(), [_frame("f1.png", [])])
    _round(out, "position", 2, _two_changes(), [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [])]))
    _merge(out, t1, tmp_path)
    assert cli.main(["final", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert not (out / "final" / f"{STEM}_qa_2026_10_17.json").exists()
    assert "NOT EXPORTED" in text and "position_v2" in text and "shape_v1" not in text.split(
        "NOT EXPORTED")[1]


def test_final_exports_when_all_rounds_merged_counting_both(out, tmp_path, capsys):
    t1 = _round(out, "shape", 1, _two_changes(), [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [])]))
    _merge(out, t1, tmp_path)
    # round 2 is built after round 1 was merged (C2 refuses the other order)
    t2 = _round(out, "position", 2, {"f1.png": {"3": {"before": _snap(_label("3")), "after": None}}},
                [_frame("f1.png", [])])
    _merge(out, t2, tmp_path)
    assert cli.main(["final", "--out", str(out)]) == 0
    assert (out / "final" / f"{STEM}_qa_2026_10_17.json").exists()
    assert f"{STEM}: 3 changes" in capsys.readouterr().out


def test_viewb_marks_unmerged_rounds(out, repo, tmp_path):
    t1 = _round(out, "shape", 1, _two_changes(), [_frame("f1.png", [])])
    _round(out, "position", 2, {"f1.png": {"3": {"before": _snap(_label("3")), "after": None}}},
           [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [])]))
    _merge(out, t1, tmp_path)
    (r,) = build_rows(out, repo)
    merged = {c["round"]: c["merged"] for c in r["changes"]}
    assert merged == {"shape_v1": True, "position_v2": False}


def test_merge_marker_written(out, tmp_path):
    t1 = _round(out, "shape", 1, {}, [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [])]))
    assert _merge(out, t1, tmp_path).frames == 1
    (m,) = json.loads((out / "rounds" / "merged.json").read_text())
    assert m["stem"] == STEM and m["tag"] == t1 and m["merged_frames"] == 1
    assert m["reviewed_path"] and m["timestamp"]


def test_final_refuses_originals_dir(out, tmp_path, monkeypatch):
    from annotation_qa import data
    tag = _round(out, "shape", 1, {}, [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [])]))
    _merge(out, tag, tmp_path)
    monkeypatch.setattr(data, "ORIGINALS_DIR", out / "final")
    with pytest.raises(ValueError):
        cli.main(["final", "--out", str(out)])


# ---- I5: View B shows what will ship ----
def _merge_with(out, tag, tmp_path, frames):
    from annotation_qa.merge import merge_reviewed
    rev = tmp_path / f"{tag}_rev.json"
    _write(rev, _export(frames))
    return merge_reviewed(STEM, rev, out, round_tag=tag)


def _three_changes(out):
    """Round shape_v1 on f1.png: 1 moved, 2 recategorised, 3 deleted by the tool."""
    after1, after2 = _label("1", y=110), _label("2", "dashed_white_line", y=130)
    ch = {"f1.png": {"1": {"before": _snap(_label("1", y=100)), "after": _snap(after1)},
                     "2": {"before": _snap(_label("2", y=130)), "after": _snap(after2)},
                     "3": {"before": _snap(_label("3", y=150)), "after": None}}}
    items = [_frame("f1.png", [after1, after2, _label("4", y=170)])]
    tag = _round(out, "shape", 1, ch, items)
    _write(out / "work" / f"{STEM}.json",
           _export([_frame("f1.png", [_label("1", y=100), _label("2", y=130), _label("3", y=150),
                                      _label("4", y=170)])]))
    return tag


def test_viewb_flags_review_changes_and_added_deleted(out, repo, tmp_path):
    tag = _three_changes(out)
    # review: 1 accepted as proposed, 2 put back to solid (reverted), 4 deleted, 5 added
    _merge_with(out, tag, tmp_path, [_frame("f1.png", [
        _label("1", y=110), _label("2", y=130), _label("5", y=60)])])
    (r,) = build_rows(out, repo)
    got = {c["label_id"]: c for c in r["changes"]}
    assert got["1"]["review"] == "" and got["1"]["merged"]
    assert got["2"]["review"] == "reverted"
    assert got["3"]["review"] == ""  # deleted by the tool, still deleted
    assert r["review_edits"] == [{"round": "shape_v1", "added": ["5"], "deleted": ["4"]}]


def test_viewb_changed_in_review_and_shipped_description(out, repo, tmp_path):
    tag = _three_changes(out)
    _merge_with(out, tag, tmp_path, [_frame("f1.png", [
        _label("1", y=120), _label("2", "dashed_white_line", y=130), _label("4", y=170)])])
    (r,) = build_rows(out, repo)
    c1 = next(c for c in r["changes"] if c["label_id"] == "1")
    assert c1["review"] == "changed in review" and c1["shipped"] == "moved 20 px"
    assert r["review_edits"] == []


def test_viewb_after_crop_of_merged_round_uses_working_copy(out, repo, tmp_path):
    from annotation_qa.viewb import frame_specs
    tag = _three_changes(out)
    specs = frame_specs(out, STEM, "f1.png")
    proposed = {l["id"]: l for l in specs[(tag, "1", "after")]["frame"]["labels"]}
    assert proposed["1"]["poly2d"][0]["vertices"][0][1] == 110  # not merged: the proposal
    _merge_with(out, tag, tmp_path, [_frame("f1.png", [_label("1", y=120)])])
    specs = frame_specs(out, STEM, "f1.png")
    shipped = {l["id"]: l for l in specs[(tag, "1", "after")]["frame"]["labels"]}
    assert shipped["1"]["poly2d"][0]["vertices"][0][1] == 120 and set(shipped) == {"1"}
    before = {l["id"]: l for l in specs[(tag, "1", "before")]["frame"]["labels"]}
    assert before["1"]["poly2d"][0]["vertices"][0][1] == 100


def test_describe_partial_shift_reports_curve_move():
    from annotation_qa.viewb import describe
    b = _snap(_label("1", y=100))
    a = {"category": b["category"], "poly2d": [{"vertices": [[20, 100], [60, 100], [80, 108],
                                                             [180, 108]],
                                                "types": "LLLL", "closed": False}]}
    assert describe(b, a) == "moved up to 8 px + vertices 2 → 4"


# ---- I6: crops are throttled, cached and rendered per frame ----
def test_all_crops_of_a_frame_from_one_decode(out, repo, monkeypatch):
    from annotation_qa import viewb
    (repo / "local-data").mkdir()
    cv2.imwrite(str(repo / "local-data" / "f1.png"), np.full((200, 200, 3), 90, np.uint8))
    tag = _three_changes(out)
    reads = []
    real = cv2.imread
    monkeypatch.setattr(cv2, "imread", lambda *a, **k: reads.append(a[0]) or real(*a, **k))
    p = viewb.render_crop(out, repo, STEM, "f1.png", "2", tag, "after")
    assert p is not None and p.read_bytes()[:2] == b"\xff\xd8"
    assert len(reads) == 1 and len(list((out / "viewb").glob("*.jpg"))) == 6  # 3 labels x 2
    assert viewb.render_crop(out, repo, STEM, "f1.png", "3", tag, "before").exists()
    assert len(reads) == 1  # served from disk


def test_render_slots_limit_concurrency(out, repo, monkeypatch):
    from annotation_qa import viewb
    assert viewb.RENDER_SLOTS._value == 2
    (repo / "local-data").mkdir()
    cv2.imwrite(str(repo / "local-data" / "f1.png"), np.full((200, 200, 3), 90, np.uint8))
    _three_changes(out)
    active, peak = [0], [0]
    real = viewb.issue_crop
    lock = threading.Lock()

    def slow(*a, **k):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        import time
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return real(*a, **k)
    monkeypatch.setattr(viewb, "issue_crop", slow)
    # five different frames' worth of renders at once: never more than two in flight
    names = [f"g{i}.png" for i in range(5)]
    for n in names:
        cv2.imwrite(str(repo / "local-data" / n), np.full((200, 200, 3), 90, np.uint8))
        _round(out, f"t{n[1]}", 1, {n: {"1": {"before": _snap(_label("1")), "after": None}}},
               [_frame(n, [])])
    ths = [threading.Thread(target=viewb.render_frame, args=(out, repo, STEM, n)) for n in names]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert peak[0] <= 2 and len(list((out / "viewb").glob("*.jpg"))) == 10


def test_exports_parsed_once_per_mtime(out, repo, monkeypatch):
    from annotation_qa import viewb
    _three_changes(out)
    calls = []
    real = viewb.load_export
    monkeypatch.setattr(viewb, "load_export", lambda p: calls.append(p) or real(p))
    build_rows(out, repo)
    build_rows(out, repo)
    assert len(calls) == len(set(calls)) == 1  # the items file, parsed once (not merged yet)


def test_prerender_renders_everything(out, repo, capsys):
    from annotation_qa.viewb import prerender
    (repo / "local-data").mkdir()
    cv2.imwrite(str(repo / "local-data" / "f1.png"), np.full((200, 200, 3), 90, np.uint8))
    _three_changes(out)
    assert prerender(out, repo) == 6 and prerender(out, repo) == 0


def _many(out, n, taskSize=None, repo=None, hand=()):
    frames = [_frame(f"f{i}.png", []) for i in range(n)]
    ch = {f"f{i}.png": {"a b": {"before": _snap(_label("a b")), "after": None}}
          for i in (0, 49, 50, 51) if i < n}
    tag = _round(out, "shape", 1, ch, frames)
    if taskSize:
        _write(repo / "local-data/scalabel/projects" / tag / "project.json",
               {"config": {"taskSize": taskSize}})
    return tag


def test_link_task_index_and_item(out, repo):
    tag = _many(out, 60, 50, repo)
    rows = {r["frame_name"]: r for r in build_rows(out, repo)}
    for i, (t, k) in {0: (0, 1), 49: (0, 50), 50: (1, 1), 51: (1, 2)}.items():
        (p,) = rows[f"f{i}.png"]["projects"]
        assert p["link"] == (f"http://localhost:8686/label?project_name={tag}"
                             f"&task_index={t}&item={k}")
        assert (p["task_index"], p["item"]) == (t, k)


def test_link_default_task_size_and_label_encoded(out, repo):
    _many(out, 60)  # no project.json -> 50
    rows = {r["frame_name"]: r for r in build_rows(out, repo)}
    (c,) = rows["f50.png"]["changes"]
    assert "task_index=1&item=1&label=a%20b" in c["link"]


def test_change_link_carries_anchor(out, repo):
    frames = [_frame("f0.png", [])]
    rec = {"before": _snap(_label("7")), "after": None, "anchor": [123, 45]}
    _round(out, "shape", 1, {"f0.png": {"7": rec}}, frames)
    (c,) = build_rows(out, repo)[0]["changes"]
    assert c["anchor"] == [123, 45] and c["link"].endswith("&label=7&x=123&y=45")


def test_hand_frame_links(out, repo):
    tag = _round(out, "position", 1, {}, [_frame("x.png", []), _frame("h.png", [_label("1")])])
    rows = {r["frame_name"]: r for r in build_rows(out, repo)}
    (p,) = rows["h.png"]["projects"]
    assert p["link"].endswith(f"project_name={tag}&task_index=0&item=2")


def test_round_order_available_and_page_script(out, repo):
    import re
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    from annotation_qa.viewb import viewb_html
    _many(out, 60, 50, repo)
    rows = build_rows(out, repo)
    ordered = sorted([r for r in rows if r["changes"]], key=lambda r: r["projects"][0]["index"])
    assert [r["frame_name"] for r in ordered] == ["f0.png", "f49.png", "f50.png", "f51.png"]
    html = viewb_html("")
    assert 'id="round"' in html and "/viewb/view" in html and "isContentEditable" in html
    assert "URLSearchParams(location.search)" in html and "history.replaceState" in html
    if not shutil.which("node"):
        pytest.skip("node not installed")
    for js in re.findall(r"<script>(.*?)</script>", html, re.S):
        p = Path(tempfile.mkdtemp()) / "p.js"
        p.write_text(js)
        assert subprocess.run(["node", "--check", str(p)]).returncode == 0


# ---- category-change crops ----
def _cat_crop(highlight):
    from types import SimpleNamespace
    from annotation_qa.imaging import FrameImage
    from annotation_qa.render import issue_crop

    class Img:
        def crop(self, x0, y0, x1, y1):
            return np.full((y1 - y0, x1 - x0, 3), 90, np.uint8)
    lab = _label("1")
    iss = SimpleNamespace(anchor=(100, 100), label_id="1", related_label_ids=[],
                          highlight_category=highlight,
                          panel_tag=f"after: {highlight}" if highlight else None)
    return issue_crop(Img(), {"labels": [lab]}, iss)


def _count(img, bgr):
    return int((img == np.array(bgr, np.uint8)).all(axis=2).sum())


def test_category_change_crop_uses_category_colours():
    red = _cat_crop("curb_road_edge")
    green = _cat_crop("without_curb_road_edge")
    assert _count(red, (0, 0, 255)) > 100 and _count(red, (0, 220, 0)) == 0
    assert _count(green, (0, 220, 0)) > 100 and _count(green, (0, 0, 255)) == 0
    assert _count(red, (255, 0, 255)) == 0
    assert _count(red, (255, 255, 255)) > 100  # outline and tag text


def test_geometry_change_crop_stays_magenta():
    img = _cat_crop(None)
    assert _count(img, (255, 0, 255)) > 100 and _count(img, (0, 0, 255)) == 0


def test_category_change_specs_and_cache_key(out, repo, tmp_path):
    from annotation_qa import viewb
    assert viewb.RENDER_VERSION != "v1"
    a = _label("1", "without_curb_road_edge")
    b = _label("1", "curb_road_edge")
    tag = _round(out, "curb_soft", 1, {"f1.png": {"1": {"before": _snap(a), "after": _snap(b)}}},
                 [_frame("f1.png", [b])])
    specs = viewb.frame_specs(out, STEM, "f1.png")
    sb, sa = specs[(tag, "1", "before")], specs[(tag, "1", "after")]
    assert sb["issue"].highlight_category == "without_curb_road_edge"
    assert sa["issue"].highlight_category == "curb_road_edge"
    assert sb["issue"].panel_tag == "before: without_curb_road_edge"
    assert sa["issue"].panel_tag == "after: curb_road_edge"
    assert viewb.class_change(_snap(a), _snap(b)) == "soft edge → CURB"
    assert viewb.class_change(_snap(b), _snap(a)) == "curb → SOFT EDGE"
