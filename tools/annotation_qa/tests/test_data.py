import json

import pytest

from annotation_qa.data import (ORIGINALS_DIR, image_path, iter_polys,
                                load_export, save_export)


def test_iter_polys_yields_every_entry(two_poly_label_frame):
    assert len(list(iter_polys(two_poly_label_frame))) == 2


def test_save_export_refuses_originals(tmp_path, small_export):
    with pytest.raises(ValueError):
        save_export(small_export, ORIGINALS_DIR / "x.json")


def test_save_load_roundtrip_and_image_path(tmp_path, small_export):
    out = tmp_path / "sub" / "e.json"
    save_export(small_export, out)
    assert set(json.loads(out.read_text())) == {"frames", "config"}
    exp = load_export(out)
    assert exp.stem == "e" and exp.frames == small_export.frames
    assert image_path(exp.frames[0], tmp_path) == tmp_path / "items/f1.png"
