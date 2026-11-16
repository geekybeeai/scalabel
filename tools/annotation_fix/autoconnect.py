"""Batch endpoint connection for imported polylines.

This is the offline equivalent of the editor's drag-an-endpoint-onto-another
gesture. In the app, ``Label2DList.findNearestEndpoint`` finds a target within
15 screen px and ``Polygon2D.mergeWith`` splices the two lines into one label.
Here the same rules run over the export JSON before import, so a batch of
predictions arrives already joined.

Two deliberate differences from the interactive path:

* Tolerance is in IMAGE pixels, not screen pixels. There is no zoom level
  offline, and results must not depend on one.
* Same-``category`` pairs merge into one label. Different-category pairs
  snap: the moved endpoint takes the anchor endpoint's exact coordinates and
  both labels keep their ids and categories. Because nobody is watching at
  import time, snaps always require a near-straight continuation
  (``SNAP_MIN_ANGLE``), never pull apart endpoints that already touch, and
  never move a Bezier anchor away from its control points.

The four splice orientations mirror ``Polygon2D.mergeWith`` exactly, including
dropping the duplicate junction vertex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

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

# Upper bound on the stub allowance: when one endpoint's direction comes from a
# segment shorter than the gap (a stub whose heading is noise), a gap of at most
# this many image pixels may run sideways to the tangents, as long as the
# spliced seam stays straight. Every other straight gap must run along both
# tangents, which keeps side-by-side parallel lines apart. 12 px admits the
# verified 10.7 px overshoot, whose earlier label is a 1.4 px stub.
STRAIGHT_JITTER_GAP = 12.0

# Cross-category snaps move vertices across a class boundary with nobody
# watching, so they always require a near-straight continuation, even when the
# merge angle guard is disabled (min_angle 0).
SNAP_MIN_ANGLE = 150.0


def _category_of(label: dict) -> str:
    """A label's category as compared by the connector (exact string match)."""
    return str(label.get("category", ""))


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
class Snap:
    """One cross-category endpoint snap that preserves both labels.

    ``anchor_id`` is the label whose endpoint stayed fixed: the earlier label,
    unless the later label's endpoint is a Bezier anchor.
    """

    anchor_id: str
    moved_id: str
    anchor_category: str
    moved_category: str
    junction: Tuple[float, float]
    gap: float
    angle: Optional[float] = None

    def to_dict(self) -> dict:
        """Serialise for the run report."""
        payload = {
            "anchorId": self.anchor_id,
            "movedId": self.moved_id,
            "anchorCategory": self.anchor_category,
            "movedCategory": self.moved_category,
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
    snaps: List[Snap] = field(default_factory=list)
    labels_before: int = 0
    labels_after: int = 0

    @property
    def merged_count(self) -> int:
        """How many merges were applied."""
        return len(self.connections)

    @property
    def snapped_count(self) -> int:
        """How many cross-category snaps were applied."""
        return len(self.snaps)


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


# (label index, is_start, poly, endpoint coordinates)
_Endpoint = Tuple[int, bool, dict, Tuple[float, float]]


def _collect_endpoints(labels: List[dict], skip: Optional[Set[int]] = None) -> List[_Endpoint]:
    """Both endpoints of every eligible open polyline, in label order.

    ``skip`` holds ``id()`` values of labels to leave out (e.g. absorbed ones).
    """
    endpoints: List[_Endpoint] = []
    for index, label in enumerate(labels):
        if skip is not None and id(label) in skip:
            continue
        poly = _open_polyline(label)
        if poly is None:
            continue
        for is_start in (True, False):
            endpoints.append((index, is_start, poly, _endpoint(poly, is_start)))
    return endpoints


def _reach_direction(poly: dict, is_start: bool, reach: float) -> Optional[np.ndarray]:
    """Outward direction of an endpoint measured over at least ``reach`` pixels.

    The unit vector from the first vertex that far from the tip, or None when
    the whole line stays closer than that (a stub). Skipping shorter segments
    keeps a double-click or near-duplicate final vertex from passing for a stub.
    """
    vertices = poly["vertices"]
    tip = vertices[0] if is_start else vertices[-1]
    indices = range(1, len(vertices)) if is_start else range(len(vertices) - 2, -1, -1)
    for index in indices:
        neighbour = vertices[index]
        dx = float(tip[0]) - float(neighbour[0])
        dy = float(tip[1]) - float(neighbour[1])
        length = float(np.hypot(dx, dy))
        if length >= max(reach, 1e-9):
            return np.array([dx / length, dy / length])
    return None


def _candidate_geometry(
    a: _Endpoint,
    b: _Endpoint,
    tolerance: float,
    min_angle: float,
    allow_curve_sampling: bool,
) -> Optional[Tuple[float, Optional[float]]]:
    """Apply the shared distance, angle and seam guards to two endpoints.

    Returns ``(gap, angle)`` or None when the pair is not eligible.

    Curve-adjacent endpoints keep the spliced-seam check. Merges may also use
    the ``CURVE_SAMPLING_GAP`` allowance, which skips the angle check for curve
    fragments a few pixels apart; cross-category snaps never do. Straight gaps
    must run along both tangents, which rejects side-by-side parallel lines;
    each tangent is measured over at least the gap's length so a short final
    segment cannot fake a direction. The one exception is a stub: when either
    whole line stays within the gap's length of its endpoint (and the gap is
    within ``STRAIGHT_JITTER_GAP``), its direction is noise, so a sideways gap
    is allowed as long as the spliced seam stays straight.
    """
    _, start_a, poly_a, point_a = a
    _, start_b, poly_b, point_b = b
    gap = float(np.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1]))
    if gap > tolerance:
        return None
    if min_angle <= 0.0:
        return gap, None
    dir_a = _direction(poly_a, start_a)
    dir_b = _direction(poly_b, start_b)
    if dir_a is None or dir_b is None:
        return None
    angle = _junction_angle(poly_a, start_a, poly_b, start_b)
    curve_adjacent = _endpoint_touches_curve(poly_a, start_a) or _endpoint_touches_curve(
        poly_b, start_b
    )
    curve_sampling_gap = allow_curve_sampling and curve_adjacent and gap <= CURVE_SAMPLING_GAP
    if angle is None or (angle < min_angle and not curve_sampling_gap):
        return None
    reach_a = _reach_direction(poly_a, start_a, gap)
    reach_b = _reach_direction(poly_b, start_b, gap)
    stub = reach_a is None or reach_b is None
    if curve_adjacent:
        follows = curve_sampling_gap or _spliced_seam_follows_tangents(
            poly_a, start_a, poly_b, start_b, min_angle
        )
    elif stub and gap <= STRAIGHT_JITTER_GAP:
        follows = _spliced_seam_follows_tangents(poly_a, start_a, poly_b, start_b, min_angle)
    else:
        follows = _gap_follows_tangents(
            point_a,
            dir_a if reach_a is None else reach_a,
            point_b,
            dir_b if reach_b is None else reach_b,
            min_angle,
        )
    return (gap, angle) if follows else None


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
) -> List[_BridgePair]:
    """Build reciprocal curve/straight pairs and retain complete bridges."""
    endpoints = _collect_endpoints(labels)
    endpoint_by_key: Dict[Tuple[int, bool], _Endpoint] = {}
    curve_indices: List[int] = []
    for entry in endpoints:
        index, is_start, poly, _ = entry
        endpoint_by_key[_endpoint_key(index, is_start)] = entry
        if is_start and _endpoint_touches_curve(poly, True) and _endpoint_touches_curve(poly, False):
            curve_indices.append(index)

    candidates: List[_BridgePair] = []
    for curve_index in curve_indices:
        for curve_start in (True, False):
            _, _, curve_poly, curve_point = endpoint_by_key[_endpoint_key(curve_index, curve_start)]
            for external_index, external_start, external_poly, external_point in endpoints:
                if (
                    external_index == curve_index
                    or _endpoint_touches_curve(external_poly, external_start)
                    or _category_of(labels[curve_index]) != _category_of(labels[external_index])
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
) -> Tuple[set, List[Connection]]:
    """Connect complete non-branching curve-bridge paths before pairwise fallback."""
    if min_angle <= 0.0:
        return set(), []
    edges = _active_bridge_edges(labels, tolerance, min_angle)
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


def _snap_different_categories(
    labels: List[dict], tolerance: float, min_angle: float
) -> List[Snap]:
    """Snap eligible cross-category endpoints without absorbing either label.

    Endpoints that already coincide are consumed up front, so a third label can
    never pull apart a junction the annotator drew touching. The continuation
    guard is at least ``SNAP_MIN_ANGLE``, even when the merge guard is
    disabled, and side-by-side parallel ends are never snapped. The earlier
    label's endpoint stays fixed and the later one moves, unless the later
    endpoint is a Bezier anchor; then the later endpoint stays and the earlier
    one moves. When both are Bezier anchors the pair is skipped, because moving
    an anchor while its control points stay put would reshape the curve.
    """
    snap_angle = max(min_angle, SNAP_MIN_ANGLE)
    endpoints = _collect_endpoints(labels)
    consumed: set = set()
    candidates: List[Tuple[float, int, bool, int, bool, Optional[float]]] = []
    for i in range(len(endpoints)):
        a = endpoints[i]
        for j in range(i + 1, len(endpoints)):
            b = endpoints[j]
            if a[0] == b[0]:
                continue
            gap = float(np.hypot(a[3][0] - b[3][0], a[3][1] - b[3][1]))
            if gap < 1e-9:
                consumed.add(_endpoint_key(a[0], a[1]))
                consumed.add(_endpoint_key(b[0], b[1]))
                continue
            if _category_of(labels[a[0]]) == _category_of(labels[b[0]]):
                continue
            geometry = _candidate_geometry(a, b, tolerance, snap_angle, False)
            if geometry is None:
                continue
            candidates.append((geometry[0], a[0], a[1], b[0], b[1], geometry[1]))
    candidates.sort(key=lambda c: (c[0], c[1], 0 if c[2] else 1, c[3], 0 if c[4] else 1))

    snaps: List[Snap] = []
    for _, idx_a, start_a, idx_b, start_b, _ in candidates:
        key_a = _endpoint_key(idx_a, start_a)
        key_b = _endpoint_key(idx_b, start_b)
        if key_a in consumed or key_b in consumed:
            continue
        poly_a = _open_polyline(labels[idx_a])
        poly_b = _open_polyline(labels[idx_b])
        if poly_a is None or poly_b is None:
            continue
        # An earlier snap may have moved the opposite endpoint of a two-vertex
        # line and turned this endpoint's tangent, so revalidate.
        end_a: _Endpoint = (idx_a, start_a, poly_a, _endpoint(poly_a, start_a))
        end_b: _Endpoint = (idx_b, start_b, poly_b, _endpoint(poly_b, start_b))
        geometry = _candidate_geometry(end_a, end_b, tolerance, snap_angle, False)
        if geometry is None or geometry[0] < 1e-9:
            continue
        curve_a = _endpoint_touches_curve(poly_a, start_a)
        curve_b = _endpoint_touches_curve(poly_b, start_b)
        if curve_a and curve_b:
            continue
        anchor, moved = (end_b, end_a) if curve_b else (end_a, end_b)
        junction = anchor[3]
        moved_poly = moved[2]
        moved_index = 0 if moved[1] else len(moved_poly["vertices"]) - 1
        moved_poly["vertices"][moved_index] = [junction[0], junction[1]]
        consumed.add(key_a)
        consumed.add(key_b)
        anchor_label = labels[anchor[0]]
        moved_label = labels[moved[0]]
        snaps.append(
            Snap(
                anchor_id=str(anchor_label.get("id", "")),
                moved_id=str(moved_label.get("id", "")),
                anchor_category=_category_of(anchor_label),
                moved_category=_category_of(moved_label),
                junction=junction,
                gap=geometry[0],
                angle=geometry[1],
            )
        )
    return snaps


def connect_labels(
    labels: List[dict],
    tolerance: float = DEFAULT_TOLERANCE,
    min_angle: float = DEFAULT_MIN_ANGLE,
) -> Tuple[List[dict], ConnectResult]:
    """Merge same-category polylines and snap different-category ones.

    Pairs are considered shortest-gap first and each endpoint is consumed at
    most once, so three lines converging on one point cannot all claim it. After
    each merge the surviving line's endpoints have moved, so candidates are
    re-derived from scratch; chains (A-B-C) therefore resolve across passes.
    Cross-category snaps run afterwards over the survivors. ``min_angle`` 0
    disables the angle guard for merges only; snaps always use at least
    ``SNAP_MIN_ANGLE``.
    """
    result = ConnectResult(labels_before=len(labels))
    working: List[dict] = list(labels)
    absorbed, atomic_connections = _connect_atomic_curve_bridges(working, tolerance, min_angle)
    result.connections.extend(atomic_connections)

    while True:
        endpoints = _collect_endpoints(working, absorbed)
        best: Optional[Tuple[float, int, bool, int, bool, Optional[float]]] = None

        for i in range(len(endpoints)):
            a = endpoints[i]
            label_a = working[a[0]]
            for j in range(i + 1, len(endpoints)):
                b = endpoints[j]
                if a[0] == b[0]:
                    continue  # Self-closing is an editor gesture, not a batch one.
                if _category_of(label_a) != _category_of(working[b[0]]):
                    continue
                geometry = _candidate_geometry(a, b, tolerance, min_angle, True)
                if geometry is None:
                    continue
                if best is None or geometry[0] < best[0]:
                    best = (geometry[0], a[0], a[1], b[0], b[1], geometry[1])

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
    result.snaps.extend(_snap_different_categories(survivors, tolerance, min_angle))
    result.labels_after = len(survivors)
    return survivors, result
