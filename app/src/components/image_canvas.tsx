import { withStyles } from "@material-ui/core/styles"
import * as React from "react"
import { connect } from "react-redux"

import Session from "../common/session"
import { onIdle } from "../common/interaction_state"
import { getCurrentViewerConfig, isFrameLoaded } from "../functional/state_util"
import { imageViewStyle } from "../styles/label"
import { ImageViewerConfigType, State } from "../types/state"
import {
  clearCanvas,
  drawImageOnCanvas,
  MAX_SCALE,
  MIN_SCALE,
  updateCanvasScale
} from "../view_config/image"
import {
  DrawableCanvas,
  DrawableProps,
  mapStateToDrawableProps
} from "./viewer"

interface ClassType {
  /** image canvas */
  image_canvas: string
}

export interface Props extends DrawableProps {
  /** styles */
  classes: ClassType
  /** display */
  display: HTMLDivElement | null
  /** viewer id */
  id: number
}

/**
 * Canvas Viewer
 */
export class ImageCanvas extends DrawableCanvas<Props> {
  /** The image context */
  protected imageContext: CanvasRenderingContext2D | null

  /** The image canvas */
  protected imageCanvas: HTMLCanvasElement | null
  /** The mask to hold the display */
  protected display: HTMLDivElement | null

  // Display variables
  /** The current scale */
  private scale: number

  /** unsubscribe from interaction-idle notifications */
  private _offIdle: (() => void) | null = null

  /**
   * Constructor, handles subscription to store
   *
   * @param {Object} props: react props
   * @param props
   */
  constructor(props: Readonly<Props>) {
    super(props)

    // Constants

    // Initialization
    this.scale = 1
    this.imageContext = null
    this.imageCanvas = null
    this.display = null
  }

  public componentDidMount(): void {
    super.componentDidMount()
    // After a gesture settles, re-render at full resolution (crisp pass).
    this._offIdle = onIdle(() => this.forceUpdate())
  }

  public componentWillUnmount(): void {
    super.componentWillUnmount()
    if (this._offIdle !== null) {
      this._offIdle()
      this._offIdle = null
    }
  }

  /**
   * Render function
   *
   * @return {React.Fragment} React fragment
   */
  public render(): JSX.Element {
    const { classes } = this.props
    let imageCanvas = (
      <canvas
        key="image-canvas"
        className={classes.image_canvas}
        ref={(canvas) => {
          if (canvas !== null && this.display !== null) {
            this.imageCanvas = canvas
            this.imageContext = canvas.getContext("2d")
            const displayRect = this.display.getBoundingClientRect()
            const item = this.state.user.select.item
            const sensor = this.state.user.viewerConfigs[this.props.id].sensor
            if (
              displayRect.width !== 0 &&
              !isNaN(displayRect.width) &&
              displayRect.height !== 0 &&
              !isNaN(displayRect.height) &&
              isFrameLoaded(this.state, item, sensor) &&
              this.imageContext !== null
            ) {
              this.updateScale(this.imageCanvas, this.imageContext, true)
              // Draw synchronously in the same commit so the freshly-resized
              // (and therefore cleared) canvas is never shown blank. The
              // deferred RAF redraw in componentDidUpdate would otherwise leave
              // a blank gap that is visible while a slow blit is pending.
              this.redraw()
            }
          }
        }}
      />
    )

    if (this.display !== null) {
      const displayRect = this.display.getBoundingClientRect()
      imageCanvas = React.cloneElement(imageCanvas, {
        height: displayRect.height,
        width: displayRect.width
      })
    }

    return imageCanvas
  }

  /**
   * Function to redraw all canvases
   * Includes dirty checking to skip redundant redraws.
   *
   * @return {boolean}
   */
  public redraw(): boolean {
    if (this.imageCanvas !== null && this.imageContext !== null) {
      const item = this.state.user.select.item
      const sensor = this.state.user.viewerConfigs[this.props.id].sensor
      if (
        isFrameLoaded(this.state, item, sensor) &&
        item < Session.images.length &&
        sensor in Session.images[item]
      ) {
        // Always redraw: the inline ref callback recreates canvas on every render
        // (HTML canvas is cleared whenever canvas.width is assigned, even with the
        // same value). ImageBitmap cache keeps this fast.
        const image = Session.images[item][sensor]
        drawImageOnCanvas(this.imageCanvas, this.imageContext, image, item, sensor)
      } else {
        clearCanvas(this.imageCanvas, this.imageContext)
      }
    }
    return true
  }

  /**
   * notify state is updated
   *
   * @param _state
   */
  protected updateState(_state: State): void {
    if (this.display !== this.props.display) {
      this.display = this.props.display
      this.forceUpdate()
    }
  }

  /**
   * Set the scale of the image in the display
   *
   * @param {object} canvas
   * @param context
   * @param {boolean} upRes
   */
  private updateScale(
    canvas: HTMLCanvasElement,
    context: CanvasRenderingContext2D,
    upRes: boolean
  ): void {
    if (this.display === null) {
      return
    }

    const imgConfig = getCurrentViewerConfig(
      this.state,
      this.props.id
    ) as ImageViewerConfigType
    if (imgConfig.viewScale >= MIN_SCALE && imgConfig.viewScale < MAX_SCALE) {
      const newParams = updateCanvasScale(
        this.state,
        this.display,
        canvas,
        context,
        imgConfig,
        imgConfig.viewScale / this.scale,
        upRes,
        // Image-only: render at reduced resolution during a gesture (cheap-but-
        // blurry while moving, crisp on idle). Labels stay full-res so tag
        // sizes don't change while zooming.
        true
      )
      this.scale = newParams[3]
    }
  }
}

const styledCanvas = withStyles(imageViewStyle, { withTheme: true })(
  ImageCanvas
)
export default connect(mapStateToDrawableProps)(styledCanvas)
