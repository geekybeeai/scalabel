import React from "react"

import Session from "../common/session"

interface State {
  /** True while a Save & Close request is in flight */
  saving: boolean
}

/**
 * "Save & Close" for embedded mode: fetch the project export, hand it to the
 * parent window, then close the ephemeral edit session.
 *
 * Rendered at the right end of the viewer's toolbar row (see
 * `drawable_viewer.tsx`) so it sits on the same line as the drawing tools.
 */
export class SaveCloseButton extends React.Component<{}, State> {
  /**
   * Constructor
   *
   * @param props
   */
  constructor(props: {}) {
    super(props)
    this.state = { saving: false }
    this.handleSaveAndClose = this.handleSaveAndClose.bind(this)
  }

  /**
   * Save the current edits via /getExport, postMessage the JSON to the
   * parent window, then fire-and-forget /closeEditSession to delete the
   * ephemeral project.
   */
  private async handleSaveAndClose(): Promise<void> {
    if (this.state.saving) {
      return
    }
    this.setState({ saving: true })

    const reduxState = Session.store.getState().present
    const projectName = reduxState.task.config.projectName
    const sessionId = projectName.replace(/^embed_/, "")

    let annotations: unknown = null
    try {
      const resp = await fetch(
        `./getExport?project_name=${encodeURIComponent(projectName)}`
      )
      if (!resp.ok) {
        throw new Error(`getExport returned ${resp.status}`)
      }
      annotations = await resp.json()
    } catch (err) {
      // eslint-disable-next-line no-alert
      window.alert(
        `Save failed: ${(err as Error).message}. Your edits remain in this ` +
          `session — please try again.`
      )
      this.setState({ saving: false })
      return
    }

    window.parent.postMessage(
      { type: "scalabel:saved", sessionId, annotations },
      window.location.origin
    )

    void fetch(
      `./closeEditSession?sessionId=${encodeURIComponent(sessionId)}`,
      { method: "POST" }
    ).catch(() => {
      /* swallow — the cleanup task will catch it */
    })

    // Leave saving=true so the button stays disabled until the parent
    // closes the modal, which destroys this iframe.
  }

  /**
   * Render the button
   */
  public render(): React.ReactNode {
    const saving = this.state.saving
    return (
      <button
        type="button"
        disabled={saving}
        onClick={() => {
          void this.handleSaveAndClose()
        }}
        style={{
          padding: "6px 16px",
          background: saving ? "#5a5a5a" : "#0a84ff",
          color: "#fff",
          border: "none",
          borderRadius: 4,
          cursor: saving ? "not-allowed" : "pointer",
          fontWeight: 600,
          fontSize: 13,
          lineHeight: "20px",
          whiteSpace: "nowrap"
        }}
      >
        {saving ? "Saving…" : "Save & Continue"}
      </button>
    )
  }
}
