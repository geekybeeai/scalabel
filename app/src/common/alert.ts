import { uid } from "../common/uid"
import Session from "../common/session"
import { Severity } from "../types/common"
import { addAlert } from "../action/common"

/**
 * Listeners notified when an already-visible alert is fired again (same
 * severity + message). The toast component subscribes and replays a shake
 * animation / restarts its dismiss timer instead of stacking a duplicate.
 */
const repeatListeners = new Set<(id: string) => void>()

/**
 * Subscribe to repeat-fires of an existing alert.
 *
 * @param listener called with the repeated alert's id
 * @returns an unsubscribe function
 */
export function onAlertRepeat(listener: (id: string) => void): () => void {
  repeatListeners.add(listener)
  return () => {
    repeatListeners.delete(listener)
  }
}

/**
 * Custom window alert
 *
 * @param severity - severity of the alert
 * @param msg - message to display
 */
export function alert(severity: Severity, msg: string): void {
  // Dedupe: when an identical toast is already on screen, shake it (and
  // restart its dismiss timer) instead of stacking a duplicate.
  const existing = Session.getState().session.alerts.find(
    (a) => a.severity === severity && a.message === msg
  )
  if (existing !== undefined) {
    repeatListeners.forEach((listener) => listener(existing.id))
    return
  }
  const newAlert = {
    id: uid(),
    severity,
    message: msg,
    timeout: 12000
  }
  Session.dispatch(addAlert(newAlert))
}
