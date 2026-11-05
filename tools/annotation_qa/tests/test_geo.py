import os
import time

from annotation_qa.geo import GeoIndex, links


def test_reads_the_batch_review_csv_and_matches_by_image_name(tmp_path):
    p = tmp_path / "image_locations.csv"
    p.write_text("image_name,dwg_name,latitude,longitude\n"
                 "854_verification.png,854,33.5,-117.25\n"
                 '"SLC 4_verification.png",SLC 4,37.6,-122.4\n'
                 "bad.png,x,abc,1\n")
    g = GeoIndex([p])
    assert g.lookup("items/a/854_verification.png") == {
        "lat": 33.5, "lon": -117.25,
        "maps": "https://www.google.com/maps/search/?api=1&query=33.5,-117.25",
        "streetview": "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=33.5,-117.25"}
    assert g.lookup("items/b/SLC 4_verification_z20.png")["lat"] == 37.6  # _z20, spaces
    assert g.lookup("items/a/855_verification.png") is None
    assert g.count() == 2


def test_semicolon_csv_and_header_variants(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("﻿file;lat;lng\n100466_200674_input.png;10;20\n")
    assert GeoIndex([p]).lookup("100466_200674_verification.png")["lon"] == 20.0


def test_missing_file_is_empty_and_a_changed_file_is_read_again(tmp_path):
    p = tmp_path / "image_locations.csv"
    g = GeoIndex([p])
    assert g.lookup("1.png") is None and g.count() == 0
    p.write_text("image,lat,lon\n1.png,1,2\n")
    assert g.lookup("1.png")["lat"] == 1.0
    p.write_text("image,lat,lon\n1.png,5,6\n")
    os.utime(p, (time.time() + 5, time.time() + 5))  # a new mtime, as an edit would give
    assert g.lookup("1.png")["lat"] == 5.0


def test_later_files_win(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("image,lat,lon\n1.png,1,1\n")
    b.write_text("image,lat,lon\n1.png,2,2\n")
    assert GeoIndex([a, b]).lookup("1.png")["lat"] == 2.0


def test_links():
    assert links(1.5, -2.25)["streetview"].endswith("viewpoint=1.5,-2.25")


def test_cards_and_view_b_rows_carry_the_lat_lon(tmp_path, monkeypatch):
    from annotation_qa import geo
    from annotation_qa.issues import Issue
    from annotation_qa.review_server import ReviewState, LB_JS, CAL_JS, INDEX_JS
    from annotation_qa.viewb import VIEWB_JS
    p = tmp_path / "image_locations.csv"
    p.write_text("image_name,latitude,longitude\nf1_verification.png,33.5,-117.25\n")
    monkeypatch.setattr(geo, "_default", geo.GeoIndex([p]))
    st = ReviewState(tmp_path, tmp_path, tmp_path)
    hit = Issue("shape", "s_export_x", "items/a/f1_verification.png", "L", "curb_road_edge", 0.9,
                "r", (1.0, 2.0), None, True, [], {})
    miss = Issue("shape", "s_export_x", "items/a/f2.png", "L", "curb_road_edge", 0.9,
                 "r", (1.0, 2.0), None, True, [], {})
    assert st.card(hit, {})["geo"]["streetview"].endswith("viewpoint=33.5,-117.25")
    assert st.card(miss, {})["geo"] is None
    # the pages draw it next to "Open in Scalabel"
    assert "function geoHtml" in LB_JS and "geoHtml(it.geo)" in INDEX_JS and "geoHtml(it.geo)" in CAL_JS
    assert "geoH(r.geo)" in VIEWB_JS


def test_view_b_row_has_geo(tmp_path, monkeypatch):
    from annotation_qa import geo, viewb
    p = tmp_path / "image_locations.csv"
    p.write_text("image,lat,lon\nf1.png,1.5,2.5\n")
    monkeypatch.setattr(geo, "_default", geo.GeoIndex([p]))
    out = tmp_path / "out"
    (out / "rounds").mkdir(parents=True)
    (out / "rounds" / "s_fix_shape_v1_changes.json").write_text(
        '{"items/f1.png": {"1": {"before": {"category": "x", "poly2d": []}, "after": null}}}')
    (out / "rounds" / "s_fix_shape_v1_items.json").write_text(
        '{"frames": [{"name": "items/f1.png", "url": "items/f1.png", "labels": []}]}')
    rows = viewb.build_rows(out, tmp_path)
    assert rows and rows[0]["geo"]["lat"] == 1.5
