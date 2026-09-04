import { Grid } from "@material-ui/core"
import React from "react"
import ReactDOM from "react-dom"

import Session from "../common/session"
import { ViewerConfigType } from "../types/state"
import { Component } from "./component"
import { SaveCloseButton } from "./save_close_button"
import { NAVBAR_TOOLS_SLOT_ID } from "./title_bar"

/**
 * Generate string to use for react component key
 *
 * @param id
 */
export function viewerReactKey(id: number): string {
  return `viewer${id}`
}

export interface ViewerClassTypes {
  /** container */
  viewer_container: string
}

export interface ViewerProps {
  /** classes */
  classes: ViewerClassTypes
  /** id of the viewer, for referencing viewer config in state */
  id: number
}

/**
 * Canvas Viewer
 */
export abstract class DrawableViewer<
  T extends ViewerProps
> extends Component<T> {
  /** Moveable container */
  protected _container: HTMLDivElement | null
  /** viewer config */
  protected _viewerConfig?: ViewerConfigType
  /** viewer id */
  protected _viewerId: number

  /** UI handler */
  protected _keyDownHandler: (e: KeyboardEvent) => void
  /** UI handler */
  protected _keyUpHandler: (e: KeyboardEvent) => void
  /** UI Handler */
  protected _wheelHandler: (e: WheelEvent) => void

  /** The hashed list of keys currently down */
  protected _keyDownMap: { [key: string]: boolean }
  /** Mouse x-coord */
  protected _mX: number
  /** Mouse y-coord */
  protected _mY: number
  /** Whether mouse is down */
  protected _mouseDown: boolean
  /** which button is pressed on mouse down */
  protected _mouseButton: number
  /** item number */
  protected _item: number

  /**
   * Constructor
   *
   * @param {Object} props: react props
   * @param props
   */
  constructor(props: T) {
    super(props)
    this._container = null
    this._viewerId = -1

    const state = Session.getState()
    if (this.props.id in state.user.viewerConfigs) {
      this._viewerConfig = state.user.viewerConfigs[this.props.id]
    }

    this._keyDownHandler = this.onKeyDown.bind(this)
    this._keyUpHandler = this.onKeyUp.bind(this)
    this._wheelHandler = this.onWheel.bind(this)

    this._keyDownMap = {}
    this._mX = 0
    this._mY = 0
    this._mouseDown = false
    this._mouseButton = -1
    this._item = -1
  }

  /**
   * Run when component mounts
   */
  public componentDidMount(): void {
    super.componentDidMount()
    document.addEventListener("keydown", this._keyDownHandler)
    document.addEventListener("keyup", this._keyUpHandler)
    // Claim active-viewer status on mount so wheel events (e.g. ctrl+scroll
    // zoom) work without first having to mouseenter the canvas. Only claim
    // if no other viewer has taken it yet — otherwise hover decides.
    if (Session.activeViewerId === -1) {
      Session.activeViewerId = this.props.id
    }
    // The title-bar tools slot did not exist yet during the initial render
    // (sibling tree, committed in the same pass); re-render so the toolbar
    // portal finds it.
    this.forceUpdate()
  }

  /**
   * Run when component unmounts
   */
  public componentWillUnmount(): void {
    super.componentWillUnmount()
    document.removeEventListener("keydown", this._keyDownHandler)
    document.removeEventListener("keyup", this._keyUpHandler)
  }

  /**
   * Render function
   */
  public render(): React.ReactNode {
    this._viewerId = this.props.id
    this._viewerConfig = this.state.user.viewerConfigs[this._viewerId]
    this._item = this.state.user.select.item

    const bannerMessage = this.bannerMessage()
    // Render the toolbar buttons into the title-bar slot (the navbar) via a
    // portal, so the canvas gets the full pane height. Only the active viewer
    // portals (guards against duplicate button sets with split panes); until
    // the slot exists (first render) the buttons stay inline as a fallback.
    const menuComponents = this.getMenuComponents()
    const toolsSlot = document.getElementById(NAVBAR_TOOLS_SLOT_ID)
    const portalTools =
      toolsSlot !== null && Session.activeViewerId === this.props.id
    return (
      <div
        className={this.props.classes.viewer_container}
        style={{ display: "flex", flexDirection: "column" }}
      >
        {portalTools && toolsSlot !== null
          ? ReactDOM.createPortal(<>{menuComponents}</>, toolsSlot)
          : null}
        <Grid justifyContent={"flex-start"} container direction="row">
          {...portalTools ? [] : menuComponents}
          {bannerMessage !== undefined && (
            <div style={{ flexGrow: 1, height: "48px" }}>
              <div
                style={{
                  background: "#007aff",
                  lineHeight: "32px",
                  padding: "0 16px",
                  margin: "8px",
                  float: "left"
                }}
              >
                {bannerMessage}
              </div>
            </div>
          )}
          {/* Embedded mode has no title bar, so the tool buttons render in
              this row; Save & Close sits at its right end on the same line. */}
          {Session.embedded && Session.activeViewerId === this.props.id && (
            <div
              style={{
                marginLeft: "auto",
                display: "flex",
                alignItems: "center",
                paddingRight: 12
              }}
            >
              <SaveCloseButton />
            </div>
          )}
        </Grid>
        <div
          ref={(element) => {
            if (element !== null && this._container !== element) {
              if (this._container !== null) {
                this._container.removeEventListener("wheel", this._wheelHandler)
              }
              this._container = element
              this._container.addEventListener("wheel", this._wheelHandler)
              this.forceUpdate()
            }
          }}
          // overflow hidden: zoom/pan are CSS offsets on the absolutely
          // positioned canvases, so without a clip a zoomed canvas paints
          // over the toolbar row above (Save & Close vanished behind it).
          style={{ flexGrow: 1, position: "relative", overflow: "hidden" }}
          onMouseDown={(e) => this.onMouseDown(e)}
          onMouseUp={(e) => this.onMouseUp(e)}
          onMouseMove={(e) => this.onMouseMove(e)}
          onMouseEnter={(e) => this.onMouseEnter(e)}
          onMouseLeave={(e) => this.onMouseLeave(e)}
          onDoubleClick={(e) => this.onDoubleClick(e)}
        >
          <div style={{ width: "100%", height: "100%", position: "absolute" }}>
            {this.getDrawableComponents()}
          </div>
          {/*
            Sibling of the panning div, not a child: overlays anchored here
            stay put while the image is panned and zoomed. pointerEvents none
            so the empty area never swallows canvas drags; each overlay turns
            it back on for itself.
          */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              pointerEvents: "none",
              zIndex: 10
            }}
          >
            {this.getOverlayComponents()}
          </div>
        </div>
      </div>
    )
  }

  /**
   * Normalize coordinates to container
   *
   * @param x
   * @param y
   */
  protected normalizeCoordinates(x: number, y: number): [number, number] {
    if (this._container !== null) {
      const rect = this._container.getBoundingClientRect()
      return [x - rect.left, y - rect.top]
    }
    return [x, y]
  }

  /**
   * Whether a specific key is pressed down
   *
   * @param {string} key - the key to check
   * @return {boolean}
   */
  protected isKeyDown(key: string): boolean {
    return this._keyDownMap[key]
  }

  /** Get child components for rendering */
  protected abstract getDrawableComponents(): React.ReactElement[]

  /**
   * Components pinned to the viewport, outside the pan/zoom transform.
   *
   * Default is none; viewers that need a docked panel override it.
   */
  protected getOverlayComponents(): React.ReactElement[] {
    return []
  }

  /** Get components for viewer menu */
  protected abstract getMenuComponents(): React.ReactElement[]

  /**
   * Handle mouse down
   *
   * @param e
   */
  protected onMouseDown(e: React.MouseEvent): void {
    if (this._container === null) {
      return
    }
    this._mouseDown = true
    this._mouseButton = e.button
    const normalized = this.normalizeCoordinates(e.clientX, e.clientY)
    this._mX = normalized[0]
    this._mY = normalized[1]
  }

  /**
   * Handle mouse up
   *
   * @param _e
   */
  protected onMouseUp(_e: React.MouseEvent): void {
    this._mouseDown = false
  }

  /**
   * Handle mouse move
   *
   * @param e
   */
  protected onMouseMove(e: React.MouseEvent): void {
    const normalized = this.normalizeCoordinates(e.clientX, e.clientY)
    this._mX = normalized[0]
    this._mY = normalized[1]
  }

  /**
   * Handle double click
   *
   * @param e
   */
  protected abstract onDoubleClick(e: React.MouseEvent): void

  /**
   * Handle mouse leave
   *
   * @param _e
   */
  protected onMouseEnter(_e: React.MouseEvent): void {
    Session.activeViewerId = this.props.id
  }

  /**
   * Handle mouse leave
   *
   * @param e
   */
  protected abstract onMouseLeave(_e: React.MouseEvent): void

  /**
   * Handle mouse wheel
   *
   * @param e
   */
  protected abstract onWheel(e: WheelEvent): void

  /**
   * Handle key down
   *
   * @param e
   */
  protected onKeyUp(e: KeyboardEvent): void {
    // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
    delete this._keyDownMap[e.key]
  }

  /**
   * Handle key down
   *
   * @param e
   */
  protected onKeyDown(e: KeyboardEvent): void {
    this._keyDownMap[e.key] = true
  }

  /**
   * Get possible banner messages based on current state.
   */
  private bannerMessage(): string | undefined {
    const {
      session: { polygon2DBoundaryClone: status }
    } = this.state
    if (status == null) {
      return
    }
    const { labelId, handler1Idx, handler2Idx } = status
    if (labelId === undefined || handler1Idx === undefined) {
      return "Click on one handler of the boundary segment you want to share. Press [esc] to quit."
    }
    if (handler2Idx === undefined) {
      return "Click on the handler of the other end. Press [esc] to quit."
    }
    return "Press [alt] to swith direction, [enter] to commit, or [esc] to quit."
  }
}
