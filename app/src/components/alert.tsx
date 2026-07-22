import React from "react"
import Alert, { Color } from "@material-ui/lab/Alert"
import IconButton from "@material-ui/core/IconButton"
import CloseIcon from "@material-ui/icons/Close"
import Session from "../common/session"
import { removeAlert } from "../action/common"
import { onAlertRepeat } from "../common/alert"
import { Severity } from "../types/common"

// Keyframes for the attention shake replayed when the same alert fires
// again while still visible. Injected once per document.
const SHAKE_STYLE_ID = "scalabel-alert-shake-style"
if (
  typeof document !== "undefined" &&
  document.getElementById(SHAKE_STYLE_ID) === null
) {
  const style = document.createElement("style")
  style.id = SHAKE_STYLE_ID
  style.textContent =
    "@keyframes scalabel-alert-shake {" +
    "0%, 100% { transform: translateX(0); }" +
    "20% { transform: translateX(-8px); }" +
    "40% { transform: translateX(8px); }" +
    "60% { transform: translateX(-5px); }" +
    "80% { transform: translateX(5px); }" +
    "}"
  document.head.appendChild(style)
}

interface Props {
  id: string
  severity: Severity
  msg: string
  timeout: number
}

interface AlertState {
  /** bumped on every repeat-fire; keys the wrapper so the shake replays */
  shakeNonce: number
}

/** Custom window alert class */
export class CustomAlert extends React.Component<Props, AlertState> {
  /** pending self-dismiss timer */
  private _timer: number | null = null
  /** unsubscribe from repeat-fire notifications */
  private _offRepeat: (() => void) | null = null

  /**
   * Constructor
   *
   * @param props react props
   */
  constructor(props: Props) {
    super(props)
    this.state = { shakeNonce: 0 }
  }

  /** overrides default mounting */
  public componentDidMount(): void {
    this.startDismissTimer()
    // A repeat of this alert (same severity+message fired while visible)
    // shakes the toast and gives it a fresh dismiss window.
    this._offRepeat = onAlertRepeat((id) => {
      if (id === this.props.id) {
        this.startDismissTimer()
        this.setState((s) => ({ shakeNonce: s.shakeNonce + 1 }))
      }
    })
  }

  /** cleanup timers and subscriptions */
  public componentWillUnmount(): void {
    if (this._timer !== null) {
      window.clearTimeout(this._timer)
      this._timer = null
    }
    if (this._offRepeat !== null) {
      this._offRepeat()
      this._offRepeat = null
    }
  }

  /** (re)start the self-dismiss timer */
  private startDismissTimer(): void {
    if (this._timer !== null) {
      window.clearTimeout(this._timer)
    }
    this._timer = window.setTimeout(() => {
      Session.dispatch(removeAlert(this.props.id))
    }, this.props.timeout)
  }

  /** overrides default render */
  public render(): React.ReactNode {
    // Remounting the wrapper via the nonce key restarts the CSS animation.
    return (
      <div
        key={this.state.shakeNonce}
        style={
          this.state.shakeNonce > 0
            ? { animation: "scalabel-alert-shake 0.5s" }
            : undefined
        }
      >
        <Alert
          severity={this.props.severity as Color}
          action={
            <IconButton
              aria-label="close"
              color="inherit"
              size="small"
              onClick={() => {
                Session.dispatch(removeAlert(this.props.id))
              }}
            >
              <CloseIcon fontSize="inherit" />
            </IconButton>
          }
        >
          {this.props.msg}
        </Alert>
      </div>
    )
  }
}
