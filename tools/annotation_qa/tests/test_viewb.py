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
    assert p["link"].endswith(f"project_name={tag}&task_index=1")


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
    a, b = _issue("shape", "1", "x_208199.png"), _issue("position", "2", "x_208199.png")
    c = _issue("shape", "3", "MLC 1 y.png")
    out.mkdir()
    for t, lst in (("shape", [a, c]), ("position", [b])):
        (out / f"issues_{t}.jsonl").write_text("".join(json.dumps(i.__dict__) + "\n" for i in lst))
    write_marks(out / "marks.json", {b.key: "real", c.key: "false"})
    assert cli.main(["bar", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    line = next(l for l in text.splitlines() if "208199" in l)
    assert "shape=1" in line and "position=1" in line and "real=1" in line
    assert "No real-marked issue: " in text
    missing = text.split("No real-marked issue: ")[1]
    assert "MLC 1" in missing and "208199" not in missing and "207851" in missing


def _merge(out, tag, tmp_path):
    from annotation_qa.merge import merge_reviewed
    rev = tmp_path / f"{tag}_reviewed.json"
    _write(rev, _export([_frame("f1.png", [])]))
    return merge_reviewed(STEM, rev, out, round_tag=tag)


def _two_changes():
    return {"f1.png": {"1": {"before": _snap(_label("1")), "after": None},
                       "2": {"before": _snap(_label("2")), "after": None}}}


def test_final_copies_merged_stems_and_counts(out, tmp_path, capsys):
    tag = _round(out, "shape", 1, _two_changes(), [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [_label("1")])]))
    _write(out / "work" / "other_export_b.json", _export([]))  # no rounds: not copied
    _merge(out, tag, tmp_path)
    assert cli.main(["final", "--out", str(out)]) == 0
    dest = out / "final" / f"{STEM}_qa_2026_10_17.json"
    assert json.loads(dest.read_text()) == _export([_frame("f1.png", [])])
    assert not list((out / "final").glob("other*"))
    assert f"{STEM}: 2 changes" in capsys.readouterr().out


def test_final_does_not_export_unmerged_stem(out, capsys):
    _round(out, "shape", 1, _two_changes(), [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [_label("1")])]))
    assert cli.main(["final", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert not (out / "final").exists() or not list((out / "final").glob("*.json"))
    assert "NOT EXPORTED" in text and "shape_v1" in text and STEM in text


def test_final_skips_stem_with_one_unmerged_round(out, tmp_path, capsys):
    t1 = _round(out, "shape", 1, _two_changes(), [_frame("f1.png", [])])
    _round(out, "position", 2, _two_changes(), [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [])]))
    _merge(out, t1, tmp_path)
    assert cli.main(["final", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert not list((out / "final").glob("*.json")) if (out / "final").exists() else True
    assert "NOT EXPORTED" in text and "position_v2" in text and "shape_v1" not in text.split(
        "NOT EXPORTED")[1]


def test_final_exports_when_all_rounds_merged_counting_both(out, tmp_path, capsys):
    t1 = _round(out, "shape", 1, _two_changes(), [_frame("f1.png", [])])
    t2 = _round(out, "position", 2, {"f1.png": {"3": {"before": _snap(_label("3")), "after": None}}},
                [_frame("f1.png", [])])
    _write(out / "work" / f"{STEM}.json", _export([_frame("f1.png", [])]))
    _merge(out, t1, tmp_path)
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
    assert _merge(out, t1, tmp_path) == 1
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
