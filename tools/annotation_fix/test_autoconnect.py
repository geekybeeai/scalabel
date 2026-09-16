"""Regression tests for the auto-connect continuity guard."""

import unittest

from annotation_fix.autoconnect import connect_labels


def line(identifier, vertices, types=None):
    """Build an eligible open lane polyline."""
    return {
        "id": identifier,
        "category": "lane",
        "poly2d": [
            {
                "vertices": vertices,
                "types": types or "L" * len(vertices),
                "closed": False,
            }
        ],
    }


class ContinuityGuardTest(unittest.TestCase):
    def test_accepts_a_gentle_continuation_that_follows_both_tangents(self):
        labels, _ = connect_labels(
            [line("a", [[0, 0], [100, 0]]), line("b", [[105, 2], [199, 36]])],
            tolerance=40,
            min_angle=150,
        )

        self.assertEqual(len(labels), 1)

    def test_accepts_a_smooth_line_to_curve_seam_after_splicing(self):
        labels, _ = connect_labels(
            [
                line("line", [[0, 0], [100, 0]]),
                line(
                    "curve",
                    [[108, 12], [140, 0], [160, 0], [200, 0]],
                    "LCCL",
                ),
            ],
            tolerance=40,
            min_angle=150,
        )

        self.assertEqual(len(labels), 1)
        self.assertEqual(
            labels[0]["poly2d"][0],
            {
                "vertices": [[0, 0], [100, 0], [140, 0], [160, 0], [200, 0]],
                "types": "LLCCL",
                "closed": False,
            },
        )

    def test_joins_a_curve_across_a_five_pixel_sampling_gap(self):
        labels, _ = connect_labels(
            [
                line("line", [[0, 0], [100, 0]]),
                line(
                    "curve",
                    [[103, 4], [108, 13], [125, 25], [160, 30]],
                    "LCCL",
                ),
            ],
            tolerance=40,
            min_angle=150,
        )

        self.assertEqual(len(labels), 1)

    def test_rejects_a_curve_join_that_creates_a_sharp_resulting_seam(self):
        labels, _ = connect_labels(
            [
                line("line", [[0, 0], [100, 0]]),
                line(
                    "offset-curve",
                    [[105, 20], [115, 20], [130, 20], [200, 20]],
                    "LCCL",
                ),
            ],
            tolerance=40,
            min_angle=150,
        )

        self.assertEqual(len(labels), 2)

    def test_rejects_side_by_side_parallel_lines_in_both_directions(self):
        forward, _ = connect_labels(
            [line("a", [[0, 0], [100, 0]]), line("b", [[105, 20], [205, 20]])],
            tolerance=40,
            min_angle=150,
        )
        reverse, _ = connect_labels(
            [line("a", [[0, 0], [100, 0]]), line("b", [[205, 20], [105, 20]])],
            tolerance=40,
            min_angle=150,
        )

        self.assertEqual(len(forward), 2)
        self.assertEqual(len(reverse), 2)

    def test_rejects_directionless_lines_only_when_the_guard_is_enabled(self):
        guarded, _ = connect_labels(
            [line("a", [[0, 0], [100, 0]]), line("b", [[105, 0], [105, 0]])],
            tolerance=40,
            min_angle=150,
        )
        distance_only, _ = connect_labels(
            [line("a", [[0, 0], [100, 0]]), line("b", [[105, 0], [105, 0]])],
            tolerance=40,
            min_angle=0,
        )

        self.assertEqual(len(guarded), 2)
        self.assertEqual(len(distance_only), 1)

    def test_skips_duplicate_endpoint_vertices_before_alignment(self):
        labels, _ = connect_labels(
            [
                line("a", [[0, 0], [100, 0]]),
                line("b", [[105, 20], [105, 20], [205, 20]]),
            ],
            tolerance=40,
            min_angle=150,
        )

        self.assertEqual(len(labels), 2)


if __name__ == "__main__":
    unittest.main()
