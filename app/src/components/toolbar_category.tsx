import { Checkbox, ListItemText, ListItem } from "@material-ui/core"
import FormControl from "@material-ui/core/FormControl"
import { withStyles } from "@material-ui/core/styles"
import TreeView from "@material-ui/lab/TreeView"
import TreeItem from "@material-ui/lab/TreeItem"
import ExpandMoreIcon from "@material-ui/icons/ExpandMore"
import ChevronRightIcon from "@material-ui/icons/ChevronRight"
import * as React from "react"

import { changeSelect } from "../action/common"
import { changeSelectedLabelsCategories } from "../action/select"
import { dispatch, getState } from "../common/session"
import { getColorByCategory } from "../drawable/util"
import { categoryStyle } from "../styles/label"
import { Component } from "./component"
import { Category } from "../types/state"

/**
 * Display-name overrides for category keys. The data keeps the canonical
 * names (e.g. "continuous_white_line") so the annotation JSON / round-trip
 * is unchanged; only the sidebar label is shortened. Falls back to the raw
 * name when a category isn't listed here.
 */
const CATEGORY_DISPLAY_NAME: { [name: string]: string } = {
  continuous_white_line: "continuous_line",
  dashed_white_line: "dashed_line"
}

/**
 * Map a canonical category name to its sidebar display label.
 *
 * @param name canonical category name from the config
 */
function categoryDisplayName(name: string): string {
  return CATEGORY_DISPLAY_NAME[name] ?? name
}

/**
 * This is the handleChange function of MultipleSelect
 * that change the set the state of MultipleSelect.
 *
 * @param _event
 * @param categoryIndex
 */
function handleChange(
  _event: React.MouseEvent<HTMLElement>,
  categoryIndex: number | null
): void {
  if (categoryIndex !== null) {
    dispatch(changeSelect({ category: categoryIndex }))

    // If any labels are currently selected, update their category too
    const state = getState()
    const selectedLabels = state.user.select.labels
    const hasSelected = Object.values(selectedLabels).some(
      (ids) => ids.length > 0
    )
    if (hasSelected) {
      dispatch(changeSelectedLabelsCategories(state, [categoryIndex]))
    }
  }
}

/**
 * This is the nodeSelect function of TreeView
 * that change the set the state of TreeView.
 *
 * @param _event
 * @param categoryIndex
 */
function handleTreeSelect(
  _event: React.ChangeEvent<{}>,
  categoryIndex: string[]
): void {
  if (categoryIndex.includes("NotLeaf")) {
    return
  }
  const catIdx = Number(categoryIndex)
  dispatch(changeSelect({ category: catIdx }))

  // If any labels are currently selected, update their category too
  const state = getState()
  const selectedLabels = state.user.select.labels
  const hasSelected = Object.values(selectedLabels).some(
    (ids) => ids.length > 0
  )
  if (hasSelected) {
    dispatch(changeSelectedLabelsCategories(state, [catIdx]))
  }
}

/**
 * Create a map for quick lookup of category data
 *
 * @param categories the categories from config file
 * returns a map from category value to its index
 */
function getCategoryMap(categories: string[]): { [key: string]: number } {
  const categoryNameMap: { [key: string]: number } = {}
  for (let catInd = 0; catInd < categories.length; catInd++) {
    // Map category names to their indices
    const category = categories[catInd]
    categoryNameMap[category] = catInd
  }
  return categoryNameMap
}

interface ClassType {
  /** root of the category selector */
  root: string
  /** form control tag */
  formControl: string
  /** primary for ListItemText */
  primary: string
  /** button style */
  button: string
  /** button group style */
  buttonGroup: string
  /** tree view style */
  treeView: string
  /** tree item root style */
  treeItemRoot: string
  /** tree item iconcontainer style */
  treeItemIconContainer: string
  /** tree item content style */
  treeItemContent: string
  /** tree item group style */
  treeItemGroup: string
  /** tree item label style */
  treeItemLabel: string
  /** tree item label text style */
  treeItemLabelText: string
  /** tree item first-level outline */
  treeItemOutline: string
}

interface Props {
  /** categories of MultipleSelect */
  categories: string[] | null
  /** tree categories of MultipleSelect */
  treeCategories: Category[] | null
  /** styles of MultipleSelect */
  classes: ClassType
  /** header text of MultipleSelect */
  headerText: string
  /** indices of categories currently hidden on the canvas */
  hiddenCategories?: number[]
  /** toggle visibility of a single category by index */
  onToggleCategoryVisibility?: (index: number) => void
  /** toggle visibility of all categories at once */
  onToggleAllCategoryVisibility?: () => void
}

/**
 * Function to create a tree-level category select menu
 *
 * @param treeCategory
 * @param categoryNameMap
 * @param treeLevel
 * @param classes
 */
function renderTreeCategory(
  treeCategory: Category,
  categoryNameMap: { [key: string]: number },
  treeLevel: number,
  classes: ClassType,
  hiddenCategories: number[],
  onToggleVisibility?: (index: number) => void
): JSX.Element {
  const isLeaf: boolean = !Array.isArray(treeCategory.subcategories)
  const nodeId = isLeaf
    ? categoryNameMap[treeCategory.name].toString()
    : treeCategory.name + "-" + treeLevel.toString() + "-NotLeaf"
  const catIdx = isLeaf ? categoryNameMap[treeCategory.name] : -1
  const rgb = getColorByCategory(catIdx, treeCategory.name)
  const swatchColor = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`
  return (
    <TreeItem
      key={treeCategory.name}
      nodeId={nodeId}
      label={
        <div
          className={classes.treeItemLabelText}
          style={{ display: "flex", alignItems: "center" }}
        >
          {isLeaf && onToggleVisibility !== undefined && (
            <Checkbox
              size="small"
              checked={!hiddenCategories.includes(catIdx)}
              // Stop the click from selecting this tree node as the active
              // draw category — the checkbox only toggles canvas visibility.
              onClick={(e) => {
                e.stopPropagation()
                onToggleVisibility(catIdx)
              }}
              onMouseDown={(e) => e.stopPropagation()}
              title={`Toggle visibility of ${treeCategory.name}`}
              style={{ padding: 2, color: "inherit" }}
            />
          )}
          {isLeaf && (
            <span
              // Solid swatch matching the colour this category's lines use.
              title={`Line colour for ${treeCategory.name}`}
              style={{
                display: "inline-block",
                width: 14,
                height: 14,
                flexShrink: 0,
                marginRight: 6,
                borderRadius: 2,
                border: "1px solid rgba(255, 255, 255, 0.5)",
                background: swatchColor
              }}
            />
          )}
          {categoryDisplayName(treeCategory.name)}
        </div>
      }
      classes={{
        root: classes.treeItemRoot,
        iconContainer: classes.treeItemIconContainer,
        content: classes.treeItemContent,
        group: classes.treeItemGroup,
        label: classes.treeItemLabel
      }}
      className={treeLevel === 0 ? classes.treeItemOutline : undefined}
    >
      {Array.isArray(treeCategory.subcategories)
        ? treeCategory.subcategories.map((category) =>
            renderTreeCategory(
              category,
              categoryNameMap,
              treeLevel + 1,
              classes,
              hiddenCategories,
              onToggleVisibility
            )
          )
        : null}
    </TreeItem>
  )
}

/**
 * This is a multipleSelect component that displays
 * all the categories as a list.
 */
class MultipleSelect extends Component<Props> {
  /**
   * Render the category in a list
   *
   * @param categories
   * @param treeCategories
   * @param classes
   * @param headerText
   */
  public renderCategory(
    categories: string[],
    treeCategories: Category[] | null,
    classes: ClassType,
    headerText: string
  ): JSX.Element {
    const categoryNameMap = getCategoryMap(categories)
    return (
      <>
        <FormControl className={classes.formControl}>
          <ListItem dense={true} className={classes.primary}>
            <ListItemText
              classes={{ primary: classes.primary }}
              primary={headerText}
            />
          </ListItem>
          {this.props.onToggleAllCategoryVisibility !== undefined && (
            <ListItem dense disableGutters style={{ padding: "0 0 2px 8px" }}>
              <Checkbox
                size="small"
                checked={(this.props.hiddenCategories ?? []).length === 0}
                indeterminate={
                  (this.props.hiddenCategories ?? []).length > 0 &&
                  (this.props.hiddenCategories ?? []).length < categories.length
                }
                onChange={() => this.props.onToggleAllCategoryVisibility?.()}
                title="Toggle visibility of all categories"
                style={{ padding: 2, color: "inherit" }}
              />
              <span style={{ fontSize: 12, opacity: 0.75 }}>Show all</span>
            </ListItem>
          )}
          {treeCategories !== null ? (
            <TreeView
              onNodeSelect={handleTreeSelect}
              defaultCollapseIcon={<ExpandMoreIcon />}
              defaultExpandIcon={<ChevronRightIcon />}
              className={classes.treeView}
            >
              {treeCategories.map((category) =>
                renderTreeCategory(
                  category,
                  categoryNameMap,
                  0,
                  classes,
                  this.props.hiddenCategories ?? [],
                  this.props.onToggleCategoryVisibility
                )
              )}
            </TreeView>
          ) : (
            <div className={classes.buttonGroup}>
              {categories.map((name: string, index: number) => {
                const hidden = (this.props.hiddenCategories ?? []).includes(
                  index
                )
                const selected = getState().user.select.category === index
                const rgb = getColorByCategory(index, name)
                const swatchColor = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`
                return (
                  <div
                    key={`category-${name}`}
                    // Clicking the row selects this category as the active draw
                    // category (mirrors the old ToggleButton behaviour).
                    onClick={(e) => handleChange(e, index)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      minHeight: 24,
                      padding: "1px 4px",
                      border: "1px solid rgba(255, 255, 255, 0.23)",
                      marginTop: index === 0 ? 0 : -1,
                      cursor: "pointer",
                      fontSize: 12,
                      lineHeight: 1.15,
                      background: selected
                        ? "rgba(25, 118, 210, 0.4)"
                        : "transparent"
                    }}
                  >
                    {this.props.onToggleCategoryVisibility !== undefined && (
                      <Checkbox
                        size="small"
                        checked={!hidden}
                        // Stop the click from also selecting this category as
                        // the active draw category — the checkbox only toggles
                        // canvas visibility.
                        onClick={(e) => {
                          e.stopPropagation()
                          this.props.onToggleCategoryVisibility?.(index)
                        }}
                        onMouseDown={(e) => e.stopPropagation()}
                        title={`Toggle visibility of ${name}`}
                        style={{ padding: 2, color: "inherit" }}
                      />
                    )}
                    <span
                      // Solid swatch showing the colour this category's lines
                      // are drawn in on the canvas.
                      title={`Line colour for ${name}`}
                      style={{
                        display: "inline-block",
                        width: 14,
                        height: 14,
                        flexShrink: 0,
                        marginRight: 6,
                        borderRadius: 2,
                        border: "1px solid rgba(255, 255, 255, 0.5)",
                        background: swatchColor
                      }}
                    />
                    <span style={{ flex: 1, wordBreak: "break-word" }}>
                      {categoryDisplayName(name)}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </FormControl>
      </>
    )
  }

  /**
   * MultipleSelect render function
   */
  public render(): React.ReactNode {
    const { categories } = this.props
    const { treeCategories } = this.props
    const { classes } = this.props
    const { headerText } = this.props
    if (categories === null) {
      return null
    } else {
      return this.renderCategory(
        categories,
        treeCategories,
        classes,
        headerText
      )
    }
  }
}

export const ToolbarCategory = withStyles(categoryStyle, { withTheme: true })(
  MultipleSelect
)
export default ToolbarCategory
