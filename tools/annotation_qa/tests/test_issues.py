import json

from annotation_qa.issues import Issue, read_issues, write_issues


def _mk(**kw):
    return Issue("position", "ex", "f.png", "1", "solid_single_line", .5, "r", (1.0, 2.0),
                 None, True, **kw)


def test_features_roundtrip(tmp_path):
    p = tmp_path / "i.jsonl"
    write_issues(p, [_mk(features={"near": 3.5, "channel": "white"})])
    [i] = read_issues(p)
    assert i.features == {"near": 3.5, "channel": "white"}


def test_missing_features_reads_as_empty(tmp_path):
    p = tmp_path / "i.jsonl"
    write_issues(p, [_mk()])
    d = json.loads(p.read_text())
    del d["features"]
    p.write_text(json.dumps(d) + "\n")
    assert read_issues(p)[0].features == {}


def test_positional_first_ten_fields_still_work():
    i = Issue("a", "b", "c", "d", "e", .1, "f", (0, 0), None, False)
    assert i.related_label_ids == [] and i.features == {}
