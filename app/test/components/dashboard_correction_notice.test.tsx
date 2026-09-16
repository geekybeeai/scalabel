/** @jest-environment node */

import React from "react"
import ShallowRenderer from "react-test-renderer/shallow"

import { Dashboard, DashboardContents } from "../../src/components/dashboard"

const WAIT_MESSAGE =
  "Auto-correction is still in progress. Please wait before opening this task."

const dashboardContents: DashboardContents = {
  projectMetaData: {
    name: "ANGLE3",
    itemType: "image",
    labelTypes: ["polyline2d"],
    taskSize: 50,
    numItems: 50,
    numLeafCategories: 4,
    numAttributes: 0
  },
  taskMetaDatas: [
    {
      numLabeledItems: "0",
      numLabels: "0",
      submissions: [],
      handlerUrl: "label"
    }
  ],
  taskKeys: ["000004"],
  numUsers: 0,
  correctionStatuses: {
    "000004": { state: "running" }
  }
}

const classes = {
  root: "root",
  row: "row",
  linkButton: "link-button",
  headerCell: "header-cell",
  bodyCell: "body-cell"
}

/**
 * Find an element by test id in a rendered React subtree.
 *
 * @param node subtree to search
 * @param testId requested test id
 * @returns the matching element
 */
function findByTestId(
  node: React.ReactNode,
  testId: string
): React.ReactElement {
  if (!React.isValidElement(node)) {
    throw new Error(`Could not find ${testId}`)
  }
  if (node.props["data-testid"] === testId) {
    return node
  }
  for (const child of React.Children.toArray(node.props.children)) {
    try {
      return findByTestId(child, testId)
    } catch {
      // Keep searching the remaining children.
    }
  }
  throw new Error(`Could not find ${testId}`)
}

test("clicking a task being corrected shows the wait message", () => {
  const renderer = ShallowRenderer.createRenderer()
  renderer.render(
    <Dashboard classes={classes} dashboardContents={dashboardContents} />
  )

  let output = renderer.getRenderOutput()
  let children = React.Children.toArray(output.props.children)
  const taskLink = findByTestId(
    (children[0] as React.ReactElement).props.main,
    "task-link-0"
  )
  expect(typeof taskLink.props.onClick).toBe("function")
  expect(taskLink.props.href).toBeUndefined()

  taskLink.props.onClick()

  output = renderer.getRenderOutput()
  children = React.Children.toArray(output.props.children)
  const notice = children[1] as React.ReactElement
  expect(notice.props.open).toBe(true)
  expect(notice.props.children.props.children).toBe(WAIT_MESSAGE)
})

test("a correction redirect opens the dashboard with the wait message", () => {
  const previousWindow = (global as { window?: Window }).window
  Object.defineProperty(global, "window", {
    configurable: true,
    value: {
      location: {
        search: "?project_name=ANGLE3&correction_pending=1"
      }
    }
  })

  try {
    const renderer = ShallowRenderer.createRenderer()
    renderer.render(
      <Dashboard classes={classes} dashboardContents={dashboardContents} />
    )

    const output = renderer.getRenderOutput()
    const children = React.Children.toArray(output.props.children)
    const notice = children[1] as React.ReactElement
    expect(notice.props.open).toBe(true)
    expect(notice.props.children.props.children).toBe(WAIT_MESSAGE)
  } finally {
    Object.defineProperty(global, "window", {
      configurable: true,
      value: previousWindow
    })
  }
})
