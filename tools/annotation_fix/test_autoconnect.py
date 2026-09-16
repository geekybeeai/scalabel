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


def bridge(gap=10):
    """Build a vertical-tangent curve between two horizontal lines."""
    return [
        line("left", [[0, 0], [70 - gap, 0]]),
        line("curve", [[70, 0], [70, 30], [130, 30], [130, 0]], "LCCL"),
        line("right", [[130 + gap, 0], [200, 0]]),
    ]


class AtomicCurveBridgeTest(unittest.TestCase):
    def test_recovers_a_complete_line_curve_line_bridge(self):
        labels, result = connect_labels(bridge(10), tolerance=40, min_angle=150)

        self.assertEqual([label["id"] for label in labels], ["left"])
        self.assertEqual(len(result.connections), 2)
        self.assertEqual(
            labels[0]["poly2d"][0],
            {
                "vertices": [[0, 0], [60, 0], [70, 30], [130, 30], [130, 0], [200, 0]],
                "types": "LLCCLL",
                "closed": False,
            },
        )

    def test_connects_an_entire_maximal_curve_bridge_chain(self):
        labels = bridge(10) + [
            line("curve-2", [[210, 0], [210, 30], [270, 30], [270, 0]], "LCCL"),
            line("far-right", [[280, 0], [340, 0]]),
        ]
        labels[2]["poly2d"][0]["vertices"][1] = [200, 0]

        output, result = connect_labels(labels, tolerance=40, min_angle=150)

        self.assertEqual([label["id"] for label in output], ["left"])
        self.assertEqual(
            [connection.absorbed_id for connection in result.connections],
            ["curve", "right", "curve-2", "far-right"],
        )
        self.assertEqual(output[0]["poly2d"][0]["types"], "LLCCLLCCLL")

    def test_requires_reciprocal_nearest_pairs_before_activating_a_bridge(self):
        labels = bridge(10)
        labels.insert(
            1,
            line("closer-curve", [[65, 2.5], [65, 32.5], [300, 30], [300, 0]], "LCCL"),
        )

        output, result = connect_labels(labels, tolerance=40, min_angle=150)

        self.assertEqual(len(output), 4)
        self.assertEqual(result.connections, [])

    def test_breaks_equal_matches_by_original_label_index(self):
        labels = [
            line("chosen", [[0, 0], [60, 0]]),
            line("unchosen", [[0, 20], [60, 0]]),
            *bridge(10)[1:],
        ]

        output, result = connect_labels(labels, tolerance=40, min_angle=150)

        self.assertEqual([label["id"] for label in output], ["chosen", "unchosen"])
        self.assertEqual([connection.absorbed_id for connection in result.connections], ["curve", "right"])

    def test_breaks_same_label_endpoint_ties_by_start_side(self):
        labels = [
            line("double-ended", [[60, 0], [-100, 0], [60, 0]]),
            *bridge(10)[1:],
        ]

        output, _ = connect_labels(labels, tolerance=40, min_angle=150)

        self.assertEqual(
            output[0]["poly2d"][0]["vertices"],
            [[200, 0], [130, 0], [130, 30], [70, 30], [60, 0], [-100, 0], [60, 0]],
        )

    def test_rejects_a_two_label_curve_bridge_self_cycle(self):
        labels = [
            line("curve", [[70, 0], [70, 30], [130, 30], [130, 0]], "LCCL"),
            line("loop", [[60, 0], [0, 0], [0, 100], [200, 100], [200, 0], [140, 0]]),
        ]

        output, result = connect_labels(labels, tolerance=40, min_angle=150)

        self.assertEqual([label["id"] for label in output], ["curve", "loop"])
        self.assertEqual(result.connections, [])

    def test_preserves_lowest_survivor_metadata_orientation_types_and_reports(self):
        left, curve, right = bridge(10)
        curve["attributes"] = {"source": "curve-survivor"}
        curve["manualShape"] = False

        output, result = connect_labels([curve, right, left], tolerance=40, min_angle=150)

        self.assertEqual(output, [curve])
        self.assertEqual(output[0]["attributes"], {"source": "curve-survivor"})
        self.assertFalse(output[0]["manualShape"])
        self.assertEqual(
            output[0]["poly2d"][0]["vertices"],
            [[0, 0], [70, 0], [70, 30], [130, 30], [130, 0], [200, 0]],
        )
        self.assertEqual(output[0]["poly2d"][0]["types"], "LLCCLL")
        self.assertEqual((result.labels_before, result.labels_after), (3, 1))
        self.assertCountEqual(
            [connection.to_dict() for connection in result.connections],
            [
                {
                    "keptId": "curve",
                    "absorbedId": "left",
                    "category": "lane",
                    "junction": [70.0, 0.0],
                    "gap": 10.0,
                    "angle": 90.0,
                },
                {
                    "keptId": "curve",
                    "absorbedId": "right",
                    "category": "lane",
                    "junction": [130.0, 0.0],
                    "gap": 10.0,
                    "angle": 90.0,
                },
            ],
        )

    def test_rejects_parallel_and_forked_external_bridge_approaches(self):
        parallel = bridge(10)
        parallel[0]["poly2d"][0]["vertices"] = [[0, 20], [60, 20]]
        fork = bridge(10)
        fork[0]["poly2d"][0]["vertices"] = [[0, -60], [60, 0]]

        self.assertEqual(len(connect_labels(parallel, tolerance=40, min_angle=150)[0]), 3)
        self.assertEqual(len(connect_labels(fork, tolerance=40, min_angle=150)[0]), 3)

    def test_zero_angle_bypasses_atomic_matching_and_keeps_legacy_order(self):
        labels = bridge(10)
        labels.insert(1, line("closer", [[64, 0], [64, 50]]))

        output, result = connect_labels(labels, tolerance=40, min_angle=0)

        self.assertEqual([label["id"] for label in output], ["left", "curve"])
        self.assertEqual(
            result.connections[0].to_dict(),
            {
                "keptId": "left",
                "absorbedId": "closer",
                "category": "lane",
                "junction": [60.0, 0.0],
                "gap": 4.0,
            },
        )
        self.assertTrue(all(connection.angle is None for connection in result.connections))


if __name__ == "__main__":
    unittest.main()
