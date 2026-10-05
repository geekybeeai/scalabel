import { StyleRules, Theme } from "@material-ui/core/styles"
import createStyles from "@material-ui/core/styles/createStyles"

const fullWidth = 700

// Styles for the create page
export const createStyle = (): StyleRules<"listRoot" | "listHeader", {}> =>
  createStyles({
    listRoot: {
      width: "90%",
      marginLeft: "5%"
    },
    listHeader: {
      textAlign: "center",
      fontWeight: "bold"
    }
  })

// Styles for sidebar project list
export const projectListStyle = (
  theme: Theme
): StyleRules<
  "coloredListItem" | "projectName" | "projectLink" | "deleteIcon",
  {}
> =>
  createStyles({
    coloredListItem: {
      backgroundColor: theme.palette.action.hover
    },
    // The name column must be allowed to shrink below its content width,
    // otherwise a long project name pushes the delete icon off the row.
    projectName: {
      flex: 1,
      minWidth: 0,
      display: "flex",
      justifyContent: "center"
    },
    projectLink: {
      display: "block",
      maxWidth: "100%",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
      textAlign: "center"
    },
    deleteIcon: {
      flexShrink: 0,
      marginLeft: theme.spacing(1)
    }
  })

// Styles for the create form
export const formStyle = (
  theme: Theme
): StyleRules<
  | "hidden"
  | "root"
  | "fullWidthText"
  | "halfWidthText"
  | "formGroup"
  | "selectEmpty"
  | "submitButton",
  {}
> =>
  createStyles({
    root: {
      paddingLeft: theme.spacing(3),
      paddingRight: theme.spacing(3)
    },
    fullWidthText: {
      width: fullWidth
    },

    halfWidthText: {
      width: fullWidth / 2
    },

    formGroup: {
      marginTop: theme.spacing(1)
    },

    selectEmpty: {
      width: (fullWidth - theme.spacing(1)) / 2,
      marginRight: theme.spacing(1)
    },

    submitButton: {
      marginRight: theme.spacing(1)
    },

    hidden: {
      visibility: "hidden"
    }
  })

// Styles for the upload buttons
export const uploadStyle = createStyles({
  root: {
    width: fullWidth / 5
  },

  button: {
    // padding: 5,
    // marginRight: 10,
    textTransform: "initial"
  },

  textField: {
    width: 130
  },

  filenameText: {
    fontSize: 14,
    overflow: "hidden",
    textOverflow: "ellipsis"
  },

  grid: {
    marginTop: 5
  }
})

// Attribute upload override styling
export const attributeStyle = createStyles({
  root: {
    position: "absolute",
    marginLeft: (fullWidth * 4) / 5
  }
})

export const checkboxStyle = (
  theme: Theme
): StyleRules<"root" | "checked", {}> =>
  createStyles({
    root: {
      "&$checked": {
        color: theme.palette.secondary.dark
      }
    },

    checked: {}
  })
