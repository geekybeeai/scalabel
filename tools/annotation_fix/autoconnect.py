"""Batch endpoint connection for imported polylines.

This is the offline equivalent of the editor's drag-an-endpoint-onto-another
gesture. In the app, ``Label2DList.findNearestEndpoint`` finds a target within
15 screen px and ``Polygon2D.mergeWith`` splices the two lines into one label.
Here the same rules run over the export JSON before import, so a batch of
predictions arrives already joined.

Two deliberate differences from the interactive path:

* Tolerance is in IMAGE pixels, not screen pixels. There is no zoom level
  offline, and results must not depend on one.
* Only same-``category`` pairs are merged. The editor also snaps endpoints
  across categories (coordinates align, labels stay distinct), but that relies
  on a visible indicator and one-step undo. Neither exists at import time, so
  moving vertices with nobody watching is not worth it.

The four splice orientations mirror ``Polygon2D.mergeWith`` exactly, including
dropping the duplicate junction vertex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# Matches the editor's 15 px snap radius, reinterpreted in image space.
DEFAULT_TOLERANCE = 15.0

# Optional straightness guard. Two lines meeting end-to-end at ~180 degrees are
# a real continuation; two meeting sharply are more likely a genuine fork. Off
# by default so the first runs are pure-distance and easy to reason about.
DEFAULT_MIN_ANGLE = 0.0

# Bezier anchors exported from adjacent model fragments can differ by a few
# pixels while still describing one curve. This stays far below the 40 px
# server search radius, so offset parallel lines remain guarded.
CURVE_SAMPLING_GAP = 5.0

# Category pairs that describe the SAME physical feature and may therefore be
# merged across the class boundary.
#
# A road edge changes curb status partway along constantly, which splits one
# continuous edge into two labels of different classes whose ends touch. Across
# a 145-frame batch this accounted for 209 of the 266 near-touching pairs the
# same-category rule refused.
#
# Paint markings are deliberately NOT in here: a yellow line meeting a white one
# is usually a real class boundary, and merging would silently rewrite one of
# their classes.
DEFAULT_MERGEABLE_CATEGORIES: Tuple[frozenset, ...] = (
    frozenset({"curb_road_edge", "without_curb_road_edge"}),
)


def _categories_compatible(
    category_a: str,
    category_b: str,
    mergeable: Sequence[frozenset],
) -> bool:
    """Whether two categories may be joined.

    Identical categories always may. Different ones may only when the pair is
    listed as describing one physical feature.
    """
    if category_a == category_b:
        return True
    pair = {category_a, category_b}
    return any(pair == allowed for allowed in mergeable)


@dataclass
class Connection:
    """One merge of two polylines."""

    kept_id: str
    absorbed_id: str
    category: str
    junction: Tuple[float, float]
    gap: float
    angle: Optional[float] = None

    def to_dict(self) -> dict:
        """Serialise for the run report."""
        payload = {
            "keptId": self.kept_id,
            "absorbedId": self.absorbed_id,
            "category": self.category,
            "junction": [round(v, 3) for v in self.junction],
            "gap": round(self.gap, 3),
        }
        if self.angle is not None:
            payload["angle"] = round(self.angle, 2)
        return payload


@dataclass
class ConnectResult:
    """Outcome of auto-connecting one frame."""

    connections: List[Connection] = field(default_factory=list)
    labels_before: int = 0
    labels_after: int = 0

    @property
    def merged_count(self) -> int:
        """How many merges were applied."""
        return len(self.connections)


def _open_polyline(label: dict) -> Optional[dict]:
    """Return a label's single open ``poly2d``, or None if not eligible.

    Closed rings never participate, matching the editor. Multi-polygon labels
    are skipped: splicing one part of a compound shape has no clear meaning.
    """
    polys = label.get("poly2d") or []
    if len(polys) != 1:
        return None
    poly = polys[0]
    if poly.get("closed"):
        return None
    vertices = poly.get("vertices") or []
    if len(vertices) < 2:
        return None
    return poly


def _endpoint(poly: dict, is_start: bool) -> Tuple[float, float]:
    """Coordinates of a polyline's start or end vertex."""
    vertices = poly["vertices"]
    vertex = vertices[0] if is_start else vertices[-1]
    return float(vertex[0]), float(vertex[1])


def _direction(poly: dict, is_start: bool) -> Optional[np.ndarray]:
    """Unit vector pointing outward from the given endpoint.

    Uses the neighbouring vertex, so it reflects the line's local heading where
    it terminates.
    """
    vertices = poly["vertices"]
    if len(vertices) < 2:
        return None
    tip = vertices[0] if is_start else vertices[-1]
    indices = range(1, len(vertices)) if is_start else range(len(vertices) - 2, -1, -1)
    for index in indices:
        neighbour = vertices[index]
        vector = np.array(
            [float(tip[0]) - float(neighbour[0]), float(tip[1]) - float(neighbour[1])],
            dtype=float,
        )
        norm = float(np.linalg.norm(vector))
        if norm >= 1e-9:
            return vector / norm
    return None


def _angle_between(a: np.ndarray, b: np.ndarray) -> float:
    """Return the angle in degrees between two unit vectors."""
    cosine = float(np.clip(np.dot(a, b), -1.0, 1.0))
    return float(np.degrees(np.arccos(cosine)))


def _junction_angle(
    poly_a: dict, is_start_a: bool, poly_b: dict, is_start_b: bool
) -> Optional[float]:
    """Angle in degrees at a prospective junction.

    180 means the two lines continue straight through each other; small values
    mean they double back sharply.
    """
    dir_a = _direction(poly_a, is_start_a)
    dir_b = _direction(poly_b, is_start_b)
    if dir_a is None or dir_b is None:
        return None
    # Each direction points outward from its own tip, so continuation shows up
    # as the vectors being opposed.
    return 180.0 - _angle_between(dir_a, -dir_b)


def _gap_follows_tangents(
    point_a: Tuple[float, float],
    dir_a: np.ndarray,
    point_b: Tuple[float, float],
    dir_b: np.ndarray,
    min_angle: float,
) -> bool:
    """Return whether the gap lies within both lines' continuation cones."""
    vector = np.array([point_b[0] - point_a[0], point_b[1] - point_a[1]], dtype=float)
    gap = float(np.linalg.norm(vector))
    if gap < 1e-9:
        return True
    connector = vector / gap
    max_deviation = 180.0 - min_angle
    return (
        _angle_between(dir_a, connector) <= max_deviation
        and _angle_between(dir_b, -connector) <= max_deviation
    )


def _endpoint_touches_curve(poly: dict, is_start: bool) -> bool:
    """Return whether the endpoint's usable neighbour is a Bezier control."""
    vertices = poly["vertices"]
    tip = vertices[0] if is_start else vertices[-1]
    indices = range(1, len(vertices)) if is_start else range(len(vertices) - 2, -1, -1)
    types = str(poly.get("types", ""))
    for index in indices:
        neighbour = vertices[index]
        if np.hypot(float(tip[0]) - float(neighbour[0]), float(tip[1]) - float(neighbour[1])) >= 1e-9:
            return types[index : index + 1] == "C"
    return False


def _vector_to_neighbour(
    vertices: List[List[float]], index: int, step: int
) -> Optional[np.ndarray]:
    """Return a unit vector from one vertex to its first distinct neighbour."""
    tip = vertices[index]
    neighbour_index = index + step
    while 0 <= neighbour_index < len(vertices):
        neighbour = vertices[neighbour_index]
        vector = np.array(
            [float(neighbour[0]) - float(tip[0]), float(neighbour[1]) - float(tip[1])],
            dtype=float,
        )
        norm = float(np.linalg.norm(vector))
        if norm >= 1e-9:
            return vector / norm
        neighbour_index += step
    return None


def _spliced_seam_follows_tangents(
    poly_a: dict,
    is_start_a: bool,
    poly_b: dict,
    is_start_b: bool,
    min_angle: float,
) -> bool:
    """Check the rendered seam created by a curve-adjacent splice.

    The editor merge drops B's endpoint and retains the rest of B. A curve
    therefore continues through its control point rather than the raw gap
    between A and B. The resulting seam must be continuous and must not turn
    B's surviving side beyond the configured deviation.
    """
    vertices, _ = _splice(
        poly_a["vertices"],
        str(poly_a.get("types", "")),
        is_start_a,
        poly_b["vertices"],
        str(poly_b.get("types", "")),
        is_start_b,
    )
    junction_index = len(poly_b["vertices"]) - 1 if is_start_a and not is_start_b else len(poly_a["vertices"]) - 1
    before = _vector_to_neighbour(vertices, junction_index, -1)
    after = _vector_to_neighbour(vertices, junction_index, 1)
    dir_b = _direction(poly_b, is_start_b)
    if before is None or after is None or dir_b is None:
        return False

    seam_angle = 180.0 - _angle_between(before, -after)
    if seam_angle < min_angle:
        return False

    b_side = before if is_start_a and not is_start_b else after
    return _angle_between(b_side, -dir_b) <= 180.0 - min_angle


def _splice(
    vertices_a: List[List[float]],
    types_a: str,
    is_start_a: bool,
    vertices_b: List[List[float]],
    types_b: str,
    is_start_b: bool,
) -> Tuple[List[List[float]], str]:
    """Join two vertex runs at the touching endpoints.

    The four cases mirror ``Polygon2D.mergeWith``. In each, B's duplicate
    junction vertex is dropped so the seam carries a single vertex, and the
    per-vertex ``types`` string is spliced identically so curve flags follow
    their vertices.
    """
    list_a, list_b = list(vertices_a), list(vertices_b)
    seq_a, seq_b = list(types_a), list(types_b)

    if not is_start_a and is_start_b:
        # A.end -> B.start
        return list_a + list_b[1:], "".join(seq_a + seq_b[1:])
    if is_start_a and not is_start_b:
        # A.start -> B.end
        return list_b[:-1] + list_a, "".join(seq_b[:-1] + seq_a)
    if is_start_a and is_start_b:
        # A.start -> B.start: reverse A so its tail meets B's head
        return list_a[::-1] + list_b[1:], "".join(seq_a[::-1] + seq_b[1:])
    # A.end -> B.end: reverse B so its head meets A's tail
    return list_a + list_b[::-1][1:], "".join(seq_a + seq_b[::-1][1:])


@dataclass(frozen=True)
class _BridgePair:
    """A guarded curve endpoint paired with an external straight endpoint."""

    gap: float
    curve_index: int
    curve_start: bool
    external_index: int
    external_start: bool
    angle: Optional[float]


def _endpoint_key(index: int, is_start: bool) -> Tuple[int, bool]:
    """Return a stable identity for an original endpoint."""
    return index, is_start


def _bridge_side(pair: _BridgePair, index: int) -> bool:
    """Return the endpoint side used by ``pair`` at ``index``."""
    return pair.curve_start if pair.curve_index == index else pair.external_start


def _bridge_other(pair: _BridgePair, index: int) -> int:
    """Return the label index at the other end of ``pair``."""
    return pair.external_index if pair.curve_index == index else pair.curve_index


def _bridge_preference(pair: _BridgePair, own_index: int) -> Tuple[float, int, int]:
    """Sort candidates by gap, other label index, then other endpoint side."""
    other = _bridge_other(pair, own_index)
    # The TypeScript reference prefers a start endpoint before an end endpoint
    # for an otherwise exact tie.
    return pair.gap, other, 0 if _bridge_side(pair, other) else 1


def _bridge_approach_allowed(
    curve_point: Tuple[float, float],
    external_poly: dict,
    external_start: bool,
    gap: float,
    min_angle: float,
) -> bool:
    """Return whether a straight endpoint approaches a curve endpoint safely."""
    if gap <= CURVE_SAMPLING_GAP:
        return True
    external_direction = _direction(external_poly, external_start)
    if external_direction is None:
        return False
    external_point = _endpoint(external_poly, external_start)
    connector = np.array(
        [curve_point[0] - external_point[0], curve_point[1] - external_point[1]],
        dtype=float,
    ) / gap
    return _angle_between(external_direction, connector) <= 180.0 - min_angle


def _active_bridge_edges(
    labels: List[dict],
    tolerance: float,
    min_angle: float,
    mergeable_categories: Sequence[frozenset],
) -> List[_BridgePair]:
    """Build reciprocal curve/straight pairs and retain complete bridges."""
    endpoints: List[Tuple[int, bool, dict, Tuple[float, float]]] = []
    endpoint_by_key: Dict[Tuple[int, bool], Tuple[int, bool, dict, Tuple[float, float]]] = {}
    curve_indices: List[int] = []
    for index, label in enumerate(labels):
        poly = _open_polyline(label)
        if poly is None:
            continue
        if _endpoint_touches_curve(poly, True) and _endpoint_touches_curve(poly, False):
            curve_indices.append(index)
        for is_start in (True, False):
            entry = (index, is_start, poly, _endpoint(poly, is_start))
            endpoints.append(entry)
            endpoint_by_key[_endpoint_key(index, is_start)] = entry

    candidates: List[_BridgePair] = []
    for curve_index in curve_indices:
        for curve_start in (True, False):
            _, _, curve_poly, curve_point = endpoint_by_key[_endpoint_key(curve_index, curve_start)]
            for external_index, external_start, external_poly, external_point in endpoints:
                if (
                    external_index == curve_index
                    or _endpoint_touches_curve(external_poly, external_start)
                    or not _categories_compatible(
                        str(labels[curve_index].get("category", "")),
                        str(labels[external_index].get("category", "")),
                        mergeable_categories,
                    )
                ):
                    continue
                gap = float(np.hypot(curve_point[0] - external_point[0], curve_point[1] - external_point[1]))
                if gap > tolerance or not _bridge_approach_allowed(
                    curve_point, external_poly, external_start, gap, min_angle
                ):
                    continue
                candidates.append(
                    _BridgePair(
                        gap=gap,
                        curve_index=curve_index,
                        curve_start=curve_start,
                        external_index=external_index,
                        external_start=external_start,
                        angle=_junction_angle(curve_poly, curve_start, external_poly, external_start),
                    )
                )

    by_endpoint: Dict[Tuple[int, bool], List[_BridgePair]] = {}
    for pair in candidates:
        by_endpoint.setdefault(_endpoint_key(pair.curve_index, pair.curve_start), []).append(pair)
        by_endpoint.setdefault(_endpoint_key(pair.external_index, pair.external_start), []).append(pair)
    nearest = {
        key: min(pairs, key=lambda pair: _bridge_preference(pair, key[0]))
        for key, pairs in by_endpoint.items()
    }
    reciprocal: Dict[Tuple[int, bool], _BridgePair] = {}
    for pair in candidates:
        curve_key = _endpoint_key(pair.curve_index, pair.curve_start)
        external_key = _endpoint_key(pair.external_index, pair.external_start)
        if nearest[curve_key] == pair and nearest[external_key] == pair:
            reciprocal[curve_key] = pair

    active: List[_BridgePair] = []
    for curve_index in curve_indices:
        start = reciprocal.get(_endpoint_key(curve_index, True))
        end = reciprocal.get(_endpoint_key(curve_index, False))
        if start is not None and end is not None and _endpoint_key(
            start.external_index, start.external_start
        ) != _endpoint_key(end.external_index, end.external_start):
            active.extend((start, end))
    return active


def _splice_preserving_first(
    vertices_a: List[List[float]],
    types_a: str,
    is_start_a: bool,
    vertices_b: List[List[float]],
    types_b: str,
    is_start_b: bool,
) -> Tuple[List[List[float]], str]:
    """Splice onto a survivor without reversing its original vertex run."""
    if not is_start_a or not is_start_b:
        return _splice(vertices_a, types_a, is_start_a, vertices_b, types_b, is_start_b)
    return list(vertices_b)[::-1][:-1] + list(vertices_a), str(types_b)[::-1][:-1] + str(types_a)


def _splice_bridge_components(
    labels: List[dict],
    components: List[Tuple[List[_BridgePair], List[int]]],
    absorbed: set,
    connections: List[Connection],
) -> None:
    """Atomically splice accepted path components into their lowest-index labels."""
    for edges, nodes in sorted(components, key=lambda component: min(component[1])):
        survivor_index = min(nodes)
        survivor = labels[survivor_index]
        adjacency: Dict[int, List[_BridgePair]] = {}
        for edge in edges:
            adjacency.setdefault(edge.curve_index, []).append(edge)
            adjacency.setdefault(edge.external_index, []).append(edge)
        first_edges = sorted(
            adjacency[survivor_index],
            key=lambda edge: (int(_bridge_side(edge, survivor_index)), _bridge_other(edge, survivor_index)),
        )
        used_edges: set = set()

        for first_edge in first_edges:
            if first_edge in used_edges:
                continue
            survivor_start = _bridge_side(first_edge, survivor_index)
            current_index = survivor_index
            edge: Optional[_BridgePair] = first_edge
            while edge is not None and edge not in used_edges:
                used_edges.add(edge)
                absorbed_index = _bridge_other(edge, current_index)
                absorbed_label = labels[absorbed_index]
                survivor_poly = _open_polyline(survivor)
                absorbed_poly = _open_polyline(absorbed_label)
                if survivor_poly is None or absorbed_poly is None:
                    break
                junction = _endpoint(survivor_poly, survivor_start)
                vertices, types = _splice_preserving_first(
                    survivor_poly["vertices"],
                    str(survivor_poly.get("types", "")),
                    survivor_start,
                    absorbed_poly["vertices"],
                    str(absorbed_poly.get("types", "")),
                    _bridge_side(edge, absorbed_index),
                )
                survivor_poly["vertices"] = vertices
                survivor_poly["types"] = types
                absorbed.add(id(absorbed_label))
                connections.append(
                    Connection(
                        kept_id=str(survivor.get("id", "")),
                        absorbed_id=str(absorbed_label.get("id", "")),
                        category=str(survivor.get("category", "")),
                        junction=junction,
                        gap=edge.gap,
                        angle=edge.angle,
                    )
                )
                edge = next((candidate for candidate in adjacency[absorbed_index] if candidate != edge), None)
                current_index = absorbed_index


def _connect_atomic_curve_bridges(
    labels: List[dict],
    tolerance: float,
    min_angle: float,
    mergeable_categories: Sequence[frozenset],
) -> Tuple[set, List[Connection]]:
    """Connect complete non-branching curve-bridge paths before pairwise fallback."""
    if min_angle <= 0.0:
        return set(), []
    edges = _active_bridge_edges(labels, tolerance, min_angle, mergeable_categories)
    adjacency: Dict[int, List[_BridgePair]] = {}
    for edge in edges:
        adjacency.setdefault(edge.curve_index, []).append(edge)
        adjacency.setdefault(edge.external_index, []).append(edge)

    visited_nodes: set = set()
    accepted: List[Tuple[List[_BridgePair], List[int]]] = []
    for seed in sorted(adjacency):
        if seed in visited_nodes:
            continue
        nodes: List[int] = []
        component_edges: set = set()
        pending = [seed]
        while pending:
            index = pending.pop()
            if index in visited_nodes:
                continue
            visited_nodes.add(index)
            nodes.append(index)
            for edge in adjacency[index]:
                component_edges.add(edge)
                pending.append(_bridge_other(edge, index))

        valid = len(nodes) >= 3 and len(component_edges) == len(nodes) - 1
        ends = 0
        for index in nodes:
            degree = len(adjacency[index])
            if degree == 1:
                ends += 1
            elif degree != 2:
                valid = False
        endpoint_use: set = set()
        for edge in component_edges:
            for key in (
                _endpoint_key(edge.curve_index, edge.curve_start),
                _endpoint_key(edge.external_index, edge.external_start),
            ):
                if key in endpoint_use:
                    valid = False
                endpoint_use.add(key)
        if valid and ends == 2:
            accepted.append((list(component_edges), nodes))

    absorbed: set = set()
    connections: List[Connection] = []
    _splice_bridge_components(labels, accepted, absorbed, connections)
    return absorbed, connections


def connect_labels(
    labels: List[dict],
    tolerance: float = DEFAULT_TOLERANCE,
    min_angle: float = DEFAULT_MIN_ANGLE,
    mergeable_categories: Sequence[frozenset] = DEFAULT_MERGEABLE_CATEGORIES,
) -> Tuple[List[dict], ConnectResult]:
    """Merge same-category polylines whose endpoints nearly touch.

    Pairs are considered shortest-gap first and each endpoint is consumed at
    most once, so three lines converging on one point cannot all claim it. After
    each merge the surviving line's endpoints have moved, so candidates are
    re-derived from scratch; chains (A-B-C) therefore resolve across passes.
    """
    result = ConnectResult(labels_before=len(labels))
    working: List[dict] = list(labels)
    absorbed, atomic_connections = _connect_atomic_curve_bridges(
        working, tolerance, min_angle, mergeable_categories
    )
    result.connections.extend(atomic_connections)

    while True:
        # (label index, is_start) for every free endpoint of an eligible line.
        endpoints: List[Tuple[int, bool, dict, Tuple[float, float]]] = []
        for index, label in enumerate(working):
            if id(label) in absorbed:
                continue
            poly = _open_polyline(label)
            if poly is None:
                continue
            for is_start in (True, False):
                endpoints.append((index, is_start, poly, _endpoint(poly, is_start)))

        best: Optional[Tuple[float, int, bool, int, bool, Optional[float]]] = None

        for i in range(len(endpoints)):
            idx_a, start_a, poly_a, point_a = endpoints[i]
            label_a = working[idx_a]
            for j in range(i + 1, len(endpoints)):
                idx_b, start_b, poly_b, point_b = endpoints[j]
                if idx_a == idx_b:
                    continue  # Self-closing is an editor gesture, not a batch one.
                label_b = working[idx_b]
                if not _categories_compatible(
                    str(label_a.get("category", "")),
                    str(label_b.get("category", "")),
                    mergeable_categories,
                ):
                    continue

                gap = float(np.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1]))
                if gap > tolerance:
                    continue

                angle = None
                if min_angle > 0.0:
                    dir_a = _direction(poly_a, start_a)
                    dir_b = _direction(poly_b, start_b)
                    if dir_a is None or dir_b is None:
                        continue
                    angle = _junction_angle(poly_a, start_a, poly_b, start_b)
                    curve_adjacent = _endpoint_touches_curve(
                        poly_a, start_a
                    ) or _endpoint_touches_curve(poly_b, start_b)
                    curve_sampling_gap = curve_adjacent and gap <= CURVE_SAMPLING_GAP
                    follows_tangents = (
                        curve_sampling_gap
                        or (
                            _spliced_seam_follows_tangents(
                                poly_a, start_a, poly_b, start_b, min_angle
                            )
                            if curve_adjacent
                            else _gap_follows_tangents(
                                point_a, dir_a, point_b, dir_b, min_angle
                            )
                        )
                    )
                    if (
                        angle is None
                        or (angle < min_angle and not curve_sampling_gap)
                        or not follows_tangents
                    ):
                        continue

                if best is None or gap < best[0]:
                    best = (gap, idx_a, start_a, idx_b, start_b, angle)

        if best is None:
            break

        gap, idx_a, start_a, idx_b, start_b, angle = best
        label_a, label_b = working[idx_a], working[idx_b]
        poly_a, poly_b = _open_polyline(label_a), _open_polyline(label_b)
        if poly_a is None or poly_b is None:
            break

        junction = _endpoint(poly_a, start_a)
        vertices, types = _splice(
            poly_a["vertices"],
            str(poly_a.get("types", "")),
            start_a,
            poly_b["vertices"],
            str(poly_b.get("types", "")),
            start_b,
        )
        poly_a["vertices"] = vertices
        poly_a["types"] = types

        absorbed.add(id(label_b))
        result.connections.append(
            Connection(
                kept_id=str(label_a.get("id", "")),
                absorbed_id=str(label_b.get("id", "")),
                category=str(label_a.get("category", "")),
                junction=junction,
                gap=gap,
                angle=angle,
            )
        )

    survivors = [label for label in working if id(label) not in absorbed]
    result.labels_after = len(survivors)
    return survivors, result
