import { shouldCanvasFreeze } from "../functional/selector"
import { ReduxState } from "../types/redux"
import { State } from "../types/state"
import { Component } from "./component"

export interface DrawableProps {
  /** Whether the canvas should freeze */
  shouldFreeze: boolean
  /** Whether tracking is enabled */
  tracking: boolean
}

export const mapStateToDrawableProps = (state: ReduxState): DrawableProps => {
  return {
    shouldFreeze: shouldCanvasFreeze(state),
    tracking: state.present.task.config.tracking
  }
}

/**
 * Abstract class for Canvas
 */
export abstract class DrawableCanvas<
  Props extends DrawableProps
> extends Component<Props> {
  /** Last viewScale used for drawing - for dirty checking */
  protected _lastViewScale: number = 1
  /** Last item index used for drawing */
  protected _lastItem: number = -1
  /** Flag indicating pending redraw */
  private _redrawScheduled: boolean = false

  /**
   * General constructor
   *
   * @param props: component props
   * @param props
   */
  protected constructor(props: Readonly<Props>) {
    super(props)
  }

  /**
   * Prevent unnecessary re-renders.
   * Only re-render if props actually changed.
   *
   * @param nextProps
   * @param nextState
   */
  public shouldComponentUpdate(
    nextProps: Readonly<Props>,
    nextState: Readonly<State>
  ): boolean {
    // Always update if shouldFreeze changed
    if (nextProps.shouldFreeze !== this.props.shouldFreeze) {
      return true
    }
    // Check if relevant state changed
    if (nextState.user.select.item !== this.state.user.select.item) {
      return true
    }
    // Check viewer config changes (includes hideLabels, hiddenLabelTypes, viewScale, etc.)
    const viewerId = (this.props as unknown as { id: number }).id
    if (viewerId !== undefined) {
      const oldConfig = this.state.user.viewerConfigs[viewerId]
      const newConfig = nextState.user.viewerConfigs[viewerId]
      if (oldConfig !== newConfig) {
        return true
      }
    }
    // Check if labels changed
    if (nextState.task.items !== this.state.task.items) {
      return true
    }
    // Check if session mode changed (affects label drawing style - e.g. fill in SELECTING mode)
    if (nextState.session.mode !== this.state.session.mode) {
      return true
    }
    // Check if track linking state changed (affects label drawing decorations)
    if (nextState.session.trackLinking !== this.state.session.trackLinking) {
      return true
    }
    // Check if label selection changed (affects highlight/selected drawing)
    if (nextState.user.select.labels !== this.state.user.select.labels) {
      return true
    }
    return false
  }

  /**
   * Execute when component state is updated
   */
  public componentDidUpdate(): void {
    this.updateState(this.state)
    // Use requestAnimationFrame to batch multiple rapid updates
    if (!this._redrawScheduled) {
      this._redrawScheduled = true
      requestAnimationFrame(() => {
        this._redrawScheduled = false
        this.redraw()
      })
    }
  }

  /**
   * Checks whether to freeze interface
   */
  public checkFreeze(): boolean {
    return this.props.shouldFreeze
  }

  /**
   * Redraw function for canvas
   * It should always fetch the current state from this.state
   * instead of Session
   */
  public abstract redraw(): boolean

  /**
   * notify state is updated
   */
  protected abstract updateState(state: State): void
}
