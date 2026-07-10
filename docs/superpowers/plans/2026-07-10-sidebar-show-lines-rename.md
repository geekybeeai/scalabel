# Sidebar "Show all" → "Show lines" Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the sidebar's "Show all" checkbox label to "Show lines" (label + tooltip only; behavior unchanged).

**Architecture:** A single text change in the category sidebar component. No prop, state, or binding changes.

**Tech Stack:** TypeScript, React 16, `@material-ui/core` v4.

## Global Constraints

- **Label-only change.** The checkbox still toggles all-category visibility via `onToggleAllCategoryVisibility`; do not touch its `checked` / `indeterminate` / `onChange` logic.
- **No new `console.log`.**
- Commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## Testing Strategy (read before starting)

This is a static string change with no local unit harness for the sidebar. Gate on `npx tsc --noEmit`, `npx eslint -c .eslintrc.json app/src/components/toolbar_category.tsx` (the real gate is **zero non-`prettier/prettier`** violations vs HEAD — the pre-existing CRLF `prettier/prettier` noise is ignored), and a runtime check via the **`verify`** skill. All commands run from the repo root `d:/Nikhil/Projects/GitHub/scalabel`.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/src/components/toolbar_category.tsx` | **Modify.** Rename the "Show all" label text and its checkbox tooltip. |

---

## Task 1: Rename the label

**Files:**
- Modify: `app/src/components/toolbar_category.tsx`

**Interfaces:**
- No interface changes. Purely the visible label text and the adjacent checkbox `title`.

- [ ] **Step 1: Rename the checkbox tooltip**

In `app/src/components/toolbar_category.tsx`, in the all-categories `Checkbox` (currently line 308), change:

```tsx
                title="Toggle visibility of all categories"
```

to:

```tsx
                title="Toggle visibility of all lines"
```

- [ ] **Step 2: Rename the label text**

Immediately below, change the label span text (currently lines 311–315):

```tsx
              <span
                style={{ fontSize: 12, opacity: 0.75, marginRight: 16 }}
              >
                Show all
              </span>
```

to:

```tsx
              <span
                style={{ fontSize: 12, opacity: 0.75, marginRight: 16 }}
              >
                Show lines
              </span>
```

- [ ] **Step 3: Type-check and lint**

Run: `npx tsc --noEmit`
Expected: no new errors referencing `toolbar_category.tsx`.

Run: `npx eslint -c .eslintrc.json app/src/components/toolbar_category.tsx`
Expected: zero non-`prettier/prettier` violations vs HEAD.

- [ ] **Step 4: Runtime verification**

Rebuild first (`npm run build`; the server serves the bundle from disk), then use the `verify` skill to drive the label page in headless Chrome. Confirm:
1. The sidebar display row reads **"Show lines"** (no "Show all" text remains).
2. Toggling that checkbox still shows/hides all lines on the canvas (unchanged behavior).

- [ ] **Step 5: Commit**

```bash
git add app/src/components/toolbar_category.tsx
git commit -m "feat: rename sidebar 'Show all' to 'Show lines'"
```

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-07-10-sidebar-show-lines-rename-design.md`):

| Spec item | Task |
|---|---|
| Rename label "Show all" → "Show lines" | Task 1 Step 2 |
| Update tooltip to match | Task 1 Step 1 |
| No behavior change (checked/indeterminate/onChange untouched) | Task 1 (only text edited) |

**2. Placeholder scan:** No placeholders; both edits show the exact before/after text.

**3. Type consistency:** No types or signatures involved.
