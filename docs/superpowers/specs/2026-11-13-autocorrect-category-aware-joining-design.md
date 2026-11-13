# Auto-correct Category-aware Joining Design

## Intent

Make project-creation auto-correct connect nearby polyline endpoints without
losing category information:

- labels with the same category merge into one label;
- labels with different categories snap to one shared endpoint but remain two
  independent labels.

This behavior applies only to the batch corrector invoked by the
**Auto-correct annotations** checkbox. Manual editor behavior remains
unchanged.

## Current failure and root cause

In `auto-correct-class-check-6`, the only same-category pair within the
40-image-pixel tolerance has a 10.728-pixel gap and a 180-degree continuation
angle. The pair is rejected because the connector between the endpoints is
sideways relative to both tangents. The current offset-parallel guard therefore
prevents an otherwise aligned same-category merge.

The current different-category restriction avoids category loss by rejecting
the candidate completely. That preserves both labels but leaves a gap instead
of connecting them.

## Selected behavior

### Shared candidate rules

Both same- and different-category candidates use the same deterministic
selection rules:

- endpoints must be within the configured tolerance (40 image pixels by
  default);
- the existing minimum continuation angle remains 150 degrees by default;
- shortest eligible gap wins;
- each endpoint is consumed by at most one correction;
- ties are resolved by original label order and endpoint side.

For ordinary straight endpoints, the continuation angle is sufficient; remove
the additional requirement that the gap vector follow both tangents. This
allows the verified 10.728-pixel, 180-degree same-category pair to connect.
The 150-degree guard continues to reject perpendicular same-category lines.
Keep the existing curve seam/tangent safeguards for endpoints adjacent to
Bezier curves.

### Same category: merge

Use the earlier label's endpoint as the junction, splice the later label's
geometry into the earlier label, and absorb the later label. Preserve the
existing survivor metadata and reporting behavior.

### Different categories: snap without merging

Use the earlier label's endpoint as the junction and move only the later
label's eligible endpoint onto that exact coordinate. Preserve both labels,
IDs, categories, all non-endpoint geometry, and endpoint point types. Because
the labels remain independent, the user can select either label and manually
separate its endpoint later.

If the endpoints already share the same coordinate, leave the geometry
unchanged. This operation is idempotent.

Run same-category merges first, then calculate different-category snaps among
the surviving labels. This prevents a snap from influencing which labels are
merged and avoids repeatedly selecting an already-snapped pair.

## Reporting

Keep merges and snaps distinct:

- `connections` continues to describe absorbed-label merges;
- add a separate `snaps` collection describing the anchor label, moved label,
  categories, junction, original gap, and optional angle;
- report `totalMerged` and `totalSnapped` separately;
- project-creation logs mention both counts.

Snapping must never increment the merged count or reduce `labelsAfter`.

## Approaches considered

### Selected: earlier-label anchor with two-phase merge then snap

This changes only one endpoint for different-category joins, is deterministic,
matches the coordinate that the existing merge keeps, and preserves both
categories without changing the editor data model.

### Rejected: move both endpoints to their midpoint

This is visually symmetric but modifies two labels instead of one and discards
both original endpoint coordinates.

### Rejected: merge different categories with segment-level categories

Scalabel stores one category per label. Supporting categories per segment would
require an export-schema, editor, selection, rendering, and downstream-tooling
redesign, and the resulting line would not be manually separable as two labels.

### Rejected: retain the current gap-direction guard

This leaves the user's verified aligned same-category example unmerged and
does not meet the requested behavior.

## Test design

1. Reproduce the verified 10.728-pixel, 180-degree same-category pair and
   confirm it merges into the earlier label.
2. Confirm same-category perpendicular lines still remain separate under the
   150-degree guard.
3. Confirm different-category eligible endpoints become coordinate-identical
   while both IDs and categories remain.
4. Confirm only the later endpoint moves and all other vertices remain exact.
5. Confirm exact-coordinate cross-category endpoints are unchanged and do not
   loop or create duplicate snap reports.
6. Confirm shortest-gap and endpoint-consumption behavior is deterministic.
7. Confirm merge and snap counts are reported separately through the stdio and
   project-creation layers.
8. Confirm no files in the interactive drawable connection path change.

## Non-goals

- Do not change manual editor snapping or merging.
- Do not add segment-level categories.
- Do not change the configured 40-pixel tolerance or 150-degree angle guard.
- Do not merge perpendicular same-category lines.
