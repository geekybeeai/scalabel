# Item 4 line editor handoff

Scalabel supports three manual line categories in standalone lane projects and in the editor embedded by `tmi-tcp-ui`. The saved annotation JSON uses the same category IDs in both cases.

| Saved category ID | Sidebar label | Editor RGB | Backend preview BGR | DWG linetype | Dash / gap |
|---|---|---|---|---|---|
| `bike_lane_line` | `bike_lane` | `[0, 230, 230]` (`#00E6E6`) | `(230, 230, 0)` | `BIKELANE` | 4 ft / 8 ft |
| `cat_track_line` | `cat_track` | `[255, 170, 210]` (`#FFAAD2`) | `(210, 170, 255)` | `CATTRACK` | 1 ft / 6 ft |
| `elephant_track_line` | `elephant_track` | `[150, 100, 255]` (`#9664FF`) | `(255, 100, 150)` | `ELEPHANT` | 3 ft / 12 ft |

## Scalabel implementation

- `CATEGORY_NAME_COLOR_MAP` supplies the sidebar swatches and canvas stroke colours. [app/src/drawable/util.ts](../app/src/drawable/util.ts)
- `CATEGORY_DISPLAY_NAME` supplies the sidebar labels while the annotation JSON uses the saved category IDs in the table. [app/src/components/toolbar_category.tsx](../app/src/components/toolbar_category.tsx)
- `addBasemapLineCategories` recognizes `polyline2d` projects containing the six Basemap categories. It appends missing IDs to both the flat category list and the category tree, preserving existing category indices and skipping duplicates. [app/src/server/util.ts](../app/src/server/util.ts)
- Project creation applies the helper before annotations are imported. `ProjectStore.loadState` applies it to existing projects after loading the task or saved state. Both paths serve standalone and embedded lane editors. [app/src/server/create_project.ts](../app/src/server/create_project.ts) · [app/src/server/project_store.ts](../app/src/server/project_store.ts)
- Run `npm run build` in the Scalabel repo. Use the full `app/dist` output for TMI so the server and editor bundles ship together.

## Basemap AI follow-up

1. In `src/stages/02_line_detection/stage.py`, append the three IDs as `{"name": "…"}` entries in the review artifact’s `config.categories`. Keep the six trained model channels in their existing order.
2. In `src/utils/taxonomy.py`, mirror the table’s BGR values in `SCALABEL_CATEGORY_COLORS_BGR` for preview rendering.
3. In `src/stages/06_dwg_generation/_internal/line_emitter.py`, map each saved ID to its NORCAL linetype. Emit one yellow polyline per drawn path on `-STRP`, using the layer colour and the template dash pattern.

After the backend changes land, draw and save one line in each category. Check that the saved JSON carries the three IDs and that the DWG contains one `-STRP` polyline for each of `BIKELANE`, `CATTRACK`, and `ELEPHANT`, with the lengths above.
