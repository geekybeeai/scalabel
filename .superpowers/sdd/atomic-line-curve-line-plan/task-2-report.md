# Task 2 report: Python parity and documentation

## Scope

Implemented the Python reference equivalent of the finalized TypeScript atomic
line–curve–line bridge pass. The Node runtime was not changed.

## TDD evidence

### RED

Command:

```powershell
$env:PYTHONPATH='tools'; python -m unittest tools.annotation_fix.test_autoconnect
```

Output before implementation:

```text
FFFF..........
Ran 14 tests in 0.010s
FAILED (failures=4)
```

The failures were the new complete-bridge, longer-chain, reciprocal/index-tie,
and survivor/report parity tests. Each failed because the pre-existing pairwise
junction guard left the curve bridge labels unmerged.

### GREEN

Focused command after implementation and added topology/tie coverage:

```powershell
$env:PYTHONPATH='tools'; python -m unittest tools.annotation_fix.test_autoconnect
```

Output:

```text
................
Ran 16 tests in 0.011s
OK
```

Final reference-suite command:

```powershell
$env:PYTHONPATH='tools'; python -m unittest discover -s tools/annotation_fix -p 'test_*.py'
```

Output immediately before commit:

```text
................
Ran 16 tests in 0.027s
OK
```

`git diff --check` also exited successfully; it reported only the repository's
existing LF-to-CRLF checkout warnings.

## Files changed

- `tools/annotation_fix/autoconnect.py`
  - Adds the guarded atomic bridge graph, reciprocal-nearest matching,
    deterministic label-index/endpoint-side ties, complete path validation,
    lowest-index survivor splicing, and atomic report generation.
  - Leaves the existing pairwise fallback in place and bypasses the graph pass
    when `min_angle` is zero.
- `tools/annotation_fix/test_autoconnect.py`
  - Covers bridge recovery, longer components, reciprocal and both tie modes,
    survivor metadata/orientation/types/report behavior, false parallel/fork
    protection, self-cycle rejection, and the zero-angle legacy path.
- `tools/annotation_fix/README.md`
  - Documents guarded curve bridge topology, straight-side connector alignment,
    unchanged straight-to-straight guards, no new setting, and zero-angle
    direction-check bypass.

## Self-review

- The graph pass runs only for positive `min_angle`; the distance-only legacy
  path is unchanged for zero.
- Only eligible open polylines are considered. A bridge requires both curve
  endpoints, two distinct external endpoints, category compatibility,
  tolerance, reciprocal nearest matches, and connector alignment outside the
  5 px jitter allowance.
- Components reject cycles, branches, self-connections, incomplete bridges,
  and reused endpoints before any mutation.
- Atomic splicing preserves the lowest original index's label identity,
  metadata, vertex orientation, types, output ordering, and report shape.
- The original pairwise pass still runs after atomic absorption.

## Concerns

None. Python remains an optional behavioral reference; no Node runtime
integration was introduced.
