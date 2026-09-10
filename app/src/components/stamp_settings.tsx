/**
 * Stamp settings panel, docked to the right of the canvas.
 *
 * Opened from its toolbar button and closed from its own X, independent of
 * whether a stamp is in progress: the shape, spacing and angle are worth
 * setting up (and saved shapes worth managing) before picking a line, not only
 * in the middle of a preview.
 *
 * While a preview IS active the panel also carries its Apply/Update/Done
 * controls, and every change repaints the preview immediately — spacing and
 * angle are far easier to judge against the image than to enter blind.
 */

import Button from "@material-ui/core/Button"
import MenuItem from "@material-ui/core/MenuItem"
import Select from "@material-ui/core/Select"
import Slider from "@material-ui/core/Slider"
import React from "react"

import { getState } from "../common/session"
import { getColorByCategory, toCssColor } from "../drawable/util"

import {
  endPreview,
  getStampOptions,
  isPanelOpen,
  requestCommit,
  requestFinish,
  setPanelOpen,
  getTemplates,
  hasApplied,
  isPreviewing,
  onStampChange,
  removeTemplate,
  renameTemplate,
  setPositions,
  resetStampOptions,
  setStampOptions
} from "../common/stamp_state"
import {
  CustomTemplate,
  StampTemplate
} from "../drawable/2d/polyline_stamp_geometry"

interface Props {
  /** called after the settings change, so the canvas can repaint */
  onChange: () => void
}

/**
 * Settings bar for the pending stamp.
 */
export class StampSettings extends React.Component<Props> {
  /** unsubscribe from stamp state changes */
  private offChange: (() => void) | null = null

  /** Subscribe so the bar appears and updates with the preview. */
  public componentDidMount(): void {
    this.offChange = onStampChange(() => this.forceUpdate())
  }

  /** Stop listening when the bar goes away. */
  public componentWillUnmount(): void {
    if (this.offChange !== null) {
      this.offChange()
      this.offChange = null
    }
  }

  /**
   * Render the bar, or nothing when no preview is active.
   */
  public render(): React.ReactNode {
    if (!isPanelOpen()) {
      return null
    }
    const previewing = isPreviewing()
    const options = getStampOptions()
    const isChevron = options.template === StampTemplate.CHEVRON
    const isCustom = options.template === StampTemplate.CUSTOM
    // After the first Apply the marks are real labels, so the buttons change
    // from "place them" to "adjust them / I am finished".
    const applied = hasApplied()

    return (
      <div
        style={{
          position: "absolute",
          right: 12,
          top: 12,
          width: 232,
          maxHeight: "calc(100% - 24px)",
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          alignItems: "stretch",
          gap: 10,
          padding: "10px 12px",
          borderRadius: 6,
          background: "rgba(32,32,32,0.94)",
          border: "1px solid rgba(255,255,255,0.18)",
          color: "#fff",
          fontSize: 12,
          zIndex: 20,
          // The panel sits over the canvas; without this a drag that starts on
          // it would fall through and pan the image.
          pointerEvents: "auto"
        }}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
      >
        {this.renderHeader()}

        <Select
          value={
            options.template === StampTemplate.CUSTOM &&
            options.custom !== undefined
              ? `custom:${options.custom.name}`
              : options.template
          }
          onChange={(e) => {
            const value = e.target.value as string
            if (value.startsWith("custom:")) {
              const name = value.slice("custom:".length)
              const found = getTemplates().find(
                (t: CustomTemplate) => t.name === name
              )
              if (found !== undefined) {
                setStampOptions({
                  ...options,
                  template: StampTemplate.CUSTOM,
                  custom: found
                })
              }
            } else {
              const template = value as StampTemplate
              // The dash slider only spans -90..90. Switching to it from a
              // chevron turned past that would strand the angle off-scale, so
              // fold it back onto the equivalent dash orientation — which is
              // the same drawn line, because a dash is symmetric.
              let angle = options.angle
              if (template === StampTemplate.DASH) {
                while (angle > 90) {
                  angle -= 180
                }
                while (angle < -90) {
                  angle += 180
                }
              }
              setStampOptions({ ...options, template, angle })
            }
            this.props.onChange()
          }}
          style={{ color: "#fff", fontSize: 12, minWidth: 96 }}
        >
          <MenuItem value={StampTemplate.DASH}>Dash</MenuItem>
          <MenuItem value={StampTemplate.CHEVRON}>Chevron</MenuItem>
          {getTemplates().map((t: CustomTemplate) => (
            <MenuItem key={t.name} value={`custom:${t.name}`}>
              {t.name}
            </MenuItem>
          ))}
        </Select>

        {this.renderCategoryPicker()}
        {/* A dash is symmetric about its centre, so -90..90 already reaches
            every orientation it has. A chevron and a captured shape both point
            somewhere — 0 and 180 face opposite ways — so they need the full
            turn, or half their orientations are unreachable. */}
        {this.renderSlider(
          "Angle",
          options.angle,
          isChevron || isCustom ? -180 : -90,
          isChevron || isCustom ? 180 : 90,
          "°",
          (v) => {
            setStampOptions({ ...options, angle: v })
          }
        )}
        {!isChevron &&
          !isCustom &&
          this.renderSlider("Length", options.length, 4, 200, "px", (v) => {
            setStampOptions({ ...options, length: v })
          })}
        {isChevron &&
          this.renderSlider("Arm A", options.armA, 4, 200, "px", (v) => {
            setStampOptions({ ...options, armA: v })
          })}
        {isChevron &&
          this.renderSlider("Arm B", options.armB, 4, 200, "px", (v) => {
            setStampOptions({ ...options, armB: v })
          })}
        {isChevron &&
          this.renderSlider("Apex", options.apex, 10, 170, "°", (v) => {
            setStampOptions({ ...options, apex: v })
          })}
        {isCustom &&
          this.renderSlider("Size", options.scale * 100, 20, 400, "%", (v) => {
            setStampOptions({ ...options, scale: v / 100 })
          })}
        {/* Negative sits left of the direction of travel, positive right. */}
        {this.renderSlider("Offset", options.offset, -60, 60, "px", (v) => {
          setStampOptions({ ...options, offset: v })
        })}
        {this.renderPlacement()}
        {isCustom && this.renderTemplateActions()}

        <Button
          size="small"
          style={{ color: "#bbb", fontSize: 11, alignSelf: "flex-start" }}
          onClick={() => {
            resetStampOptions()
            this.props.onChange()
          }}
        >
          Reset to defaults
        </Button>
        {!previewing && (
          <span style={{ opacity: 0.6, lineHeight: 1.4 }}>
            Arm the stamp tool, then click a line to preview marks along it.
          </span>
        )}
        {previewing && (
          <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
            {!applied && (
              <Button
                size="small"
                style={{ color: "#ff8a80", fontSize: 11 }}
                onClick={() => {
                  endPreview()
                  this.props.onChange()
                }}
              >
                Cancel
              </Button>
            )}
            <Button
              size="small"
              variant="contained"
              color="primary"
              style={{ fontSize: 11 }}
              onClick={() => requestCommit()}
            >
              {applied ? "Update" : "Apply"}
            </Button>
            {applied && (
              <Button
                size="small"
                style={{ color: "#fff", fontSize: 11 }}
                onClick={() => requestFinish()}
              >
                Done
              </Button>
            )}
          </div>
        )}
      </div>
    )
  }

  /**
   * Render the rename/forget buttons for the selected saved shape.
   */
  private renderTemplateActions(): React.ReactNode {
    const options = getStampOptions()
    const custom = options.custom
    if (custom === undefined) {
      return null
    }
    return (
      <div style={{ display: "flex", gap: 6 }}>
        <Button
          size="small"
          style={{ color: "#bbb", fontSize: 11 }}
          title="Give this shape a meaningful name"
          onClick={() => {
            const next = window.prompt("Name this shape", custom.name)
            if (next !== null && next.trim() !== "") {
              renameTemplate(custom.name, next)
              setStampOptions({
                ...options,
                custom: { ...custom, name: next.trim() }
              })
              this.props.onChange()
            }
          }}
        >
          Rename
        </Button>
        <Button
          size="small"
          style={{ color: "#bbb", fontSize: 11 }}
          title="Forget this saved shape"
          onClick={() => {
            removeTemplate(custom.name)
            setStampOptions({ ...options, template: StampTemplate.DASH })
            this.props.onChange()
          }}
        >
          Forget
        </Button>
      </div>
    )
  }

  /**
   * Render the class picker for the marks.
   *
   * Defaults to whatever the sidebar has selected, since that is the previous
   * behaviour, but marks are often a different class from the line they run
   * along, and changing the sidebar selection before every stamp is friction.
   */
  private renderCategoryPicker(): React.ReactNode {
    const options = getStampOptions()
    const categories = getState().task.config.categories
    if (categories === undefined || categories.length === 0) {
      return null
    }
    const selected = options.category ?? getState().user.select.category
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ opacity: 0.75, whiteSpace: "nowrap", width: 54 }}>
          Class
        </span>
        <Select
          value={selected}
          onChange={(e) => {
            setStampOptions({
              ...options,
              category: e.target.value as number
            })
            this.props.onChange()
          }}
          style={{ color: "#fff", fontSize: 12, flex: 1 }}
        >
          {categories.map((name: string, index: number) => (
            <MenuItem key={name} value={index}>
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  marginRight: 6,
                  borderRadius: 2,
                  border: "1px solid rgba(0,0,0,0.5)",
                  background: toCssColor(getColorByCategory(index, name))
                }}
              />
              {name}
            </MenuItem>
          ))}
        </Select>
      </div>
    )
  }

  /**
   * Render the spacing mode: even period, or manual click placement.
   */
  private renderPlacement(): React.ReactNode {
    const options = getStampOptions()
    return (
      <>
        <label
          style={{ display: "flex", alignItems: "center", gap: 6 }}
          title="Off: click along the line to place each mark by hand"
        >
          <input
            type="checkbox"
            checked={options.evenSpacing}
            onChange={() => {
              setStampOptions({ ...options, evenSpacing: !options.evenSpacing })
              this.props.onChange()
            }}
          />
          <span>Even spacing</span>
        </label>
        {options.evenSpacing &&
          this.renderSlider("Spacing", options.period, 10, 400, "px", (v) => {
            setStampOptions({ ...options, period: v })
          })}
        {/* Start shifts only the first mark. Margin trims both ends together,
            so it cannot line a run up with existing paint without also cutting
            the far end short. */}
        {options.evenSpacing &&
          this.renderSlider("Start", options.start, 0, 400, "px", (v) => {
            setStampOptions({ ...options, start: v })
          })}
        {!options.evenSpacing && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              opacity: 0.75
            }}
          >
            <span>
              {options.positions.length} placed — click the line to add, a mark
              to remove, Ctrl+Z to undo
            </span>
          </div>
        )}
        {!options.evenSpacing && options.positions.length > 0 && (
          <Button
            size="small"
            style={{ color: "#bbb", fontSize: 11, alignSelf: "flex-start" }}
            onClick={() => {
              // Recorded, so a mis-clicked Clear is one Ctrl+Z away.
              setPositions([])
              this.props.onChange()
            }}
          >
            Clear placed marks
          </Button>
        )}
      </>
    )
  }

  /**
   * Render the panel title and its close button.
   */
  private renderHeader(): React.ReactNode {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between"
        }}
      >
        <span style={{ fontWeight: 600 }}>Stamp marks</span>
        <Button
          size="small"
          style={{ color: "#bbb", fontSize: 14, minWidth: 28, padding: 0 }}
          title="Close"
          onClick={() => {
            endPreview()
            setPanelOpen(false)
            this.props.onChange()
          }}
        >
          ×
        </Button>
      </div>
    )
  }

  /**
   * Render one labelled slider with its current value.
   *
   * @param label the caption
   * @param value the current value
   * @param min slider minimum
   * @param max slider maximum
   * @param unit suffix shown after the value
   * @param onChange called with the new value
   */
  private renderSlider(
    label: string,
    value: number,
    min: number,
    max: number,
    unit: string,
    onChange: (value: number) => void
  ): React.ReactNode {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ opacity: 0.75, whiteSpace: "nowrap", width: 54 }}>
          {label}
        </span>
        <Slider
          value={Math.round(value)}
          min={min}
          max={max}
          onChange={(_event, next) => {
            onChange(next as number)
            this.props.onChange()
          }}
          style={{ flex: 1 }}
        />
        <span
          style={{
            width: 42,
            textAlign: "right",
            fontVariantNumeric: "tabular-nums"
          }}
        >
          {Math.round(value)}
          {unit}
        </span>
      </div>
    )
  }
}

export default StampSettings
