import { createTheme } from "@mui/material/styles";

/**
 * Colour system
 *
 * Hex values are sRGB approximations of the referenced Pantone colours.
 *
 * The palette is split by role, and the split is what keeps the interface
 * readable at the density of a findings table:
 *
 *   Purple  - brand and interaction. Buttons, active states, focus, selection.
 *   Navy    - structure and text. The header, headings, body copy.
 *   Red / amber / green - finding status, and nothing else.
 *
 * Because the status colours are never used decoratively, a red mark anywhere
 * in the interface always means a rule failed. Losing that would make the table
 * much slower to scan.
 */

// Pantone Violet C -- brand primary.
const VIOLET = "#440099";
// Pantone 2685 C -- deeper violet for hover and the header gradient.
const VIOLET_DARK = "#330072";
const VIOLET_LIGHT = "#6B33AD";
// A faint violet wash for selected rows, hover fills and chip backgrounds.
const VIOLET_TINT = "#F3EEFA";
const VIOLET_MUTED = "#B9AEDD";

// Pantone 282 C -- structural navy. Used for text in place of pure black,
// which reads as harsh against a white background.
const NAVY = "#041E42";
// Pantone 2935 C -- secondary interactive blue.
const BLUE = "#0057B8";

// Pantone 187 C / 124 C / 348 C -- status.
const STATUS_RED = "#A6192E";
const STATUS_AMBER = "#EAAA00";
const STATUS_GREEN = "#00843D";
const STATUS_GREY = "#6B7280";

const BORDER = "#E3DEEE";
const SURFACE_ALT = "#F8F6FB";

/**
 * Per-status tokens, exported so every component renders a status the same way.
 *
 * Each carries:
 *   main  - the saturated colour, for fills, accent strips and progress bars.
 *   text  - a darkened variant used wherever the colour carries *text* on a
 *           tint. The Pantone greens and greys sit at 4.2-4.4:1 on their own
 *           tints, just under the 4.5:1 needed for body-size text, so chip
 *           labels take this instead. Every pair below is verified at >= 4.5:1.
 *   tint  - the pale fill.
 *   border, contrastText, label.
 *
 * Tinted chips read more cleanly than solid ones at table density, where a
 * column of saturated blocks becomes noise rather than information.
 */
export const STATUS_TOKENS = {
  COMPLIANT: {
    main: STATUS_GREEN,
    text: "#006B31",
    tint: "#E6F3EC",
    border: "rgba(0, 132, 61, 0.40)",
    contrastText: "#FFFFFF",
    label: "Compliant",
  },
  "NON-COMPLIANT": {
    main: STATUS_RED,
    text: STATUS_RED,
    tint: "#F9E9EC",
    border: "rgba(166, 25, 46, 0.40)",
    contrastText: "#FFFFFF",
    label: "Non-Compliant",
  },
  NEEDS_REVIEW: {
    // Amber is too light to carry text at any size, so the text variant is a
    // deep ochre; the vivid amber survives in the tint and the border.
    main: "#8A6400",
    text: "#8A6400",
    tint: "#FCF4DE",
    border: "rgba(234, 170, 0, 0.55)",
    contrastText: NAVY,
    label: "Needs Review",
  },
  NOT_EVALUATED: {
    main: STATUS_GREY,
    text: "#565E6B",
    tint: "#F3F4F6",
    border: "rgba(107, 114, 128, 0.35)",
    contrastText: "#FFFFFF",
    label: "Not Evaluated",
  },
  ERROR: {
    main: STATUS_RED,
    text: STATUS_RED,
    tint: "#F9E9EC",
    border: "rgba(166, 25, 46, 0.40)",
    contrastText: "#FFFFFF",
    label: "Error",
  },
};

export const statusToken = (status) =>
  STATUS_TOKENS[status] || STATUS_TOKENS.NOT_EVALUATED;

export const SEVERITY_TOKENS = {
  critical: {
    main: STATUS_RED,
    text: STATUS_RED,
    tint: "#F9E9EC",
    border: "rgba(166,25,46,0.40)",
  },
  major: {
    main: "#8A6400",
    text: "#8A6400",
    tint: "#FCF4DE",
    border: "rgba(234,170,0,0.55)",
  },
  minor: {
    main: STATUS_GREY,
    text: "#565E6B",
    tint: "#F3F4F6",
    border: "rgba(107,114,128,0.35)",
  },
};

// The named Pantone values, for the few places that need a brand colour rather
// than a status colour.
export const PANTONE = { violet: VIOLET, navy: NAVY, blue: BLUE };

export const HEADER_GRADIENT = `linear-gradient(90deg, ${NAVY} 0%, ${VIOLET_DARK} 60%, ${VIOLET} 100%)`;

const theme = createTheme({
  palette: {
    mode: "light",
    primary: {
      main: VIOLET,
      light: VIOLET_LIGHT,
      dark: VIOLET_DARK,
      contrastText: "#FFFFFF",
    },
    secondary: {
      main: BLUE,
      light: "#3379C6",
      dark: "#003E83",
      contrastText: "#FFFFFF",
    },
    error: { main: STATUS_RED, light: "#C24759", dark: "#8A1526" },
    warning: {
      main: STATUS_AMBER,
      light: "#EEBB33",
      dark: "#C79100",
      contrastText: NAVY,
    },
    success: { main: STATUS_GREEN, light: "#339D63", dark: "#00602C" },
    info: { main: BLUE },
    background: { default: "#FFFFFF", paper: "#FFFFFF" },
    text: { primary: NAVY, secondary: "#5A6478" },
    divider: BORDER,

    // Custom tokens consumed directly by components.
    brand: {
      violet: VIOLET,
      violetDark: VIOLET_DARK,
      violetTint: VIOLET_TINT,
      violetMuted: VIOLET_MUTED,
      navy: NAVY,
      surfaceAlt: SURFACE_ALT,
      border: BORDER,
    },
    status: {
      compliant: STATUS_GREEN,
      nonCompliant: STATUS_RED,
      review: STATUS_AMBER,
      notEvaluated: STATUS_GREY,
    },
  },

  shape: { borderRadius: 8 },

  typography: {
    fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    // Weight only -- no colour. Headings inherit `text.primary` (navy) on light
    // surfaces and the AppBar's white on the dark gradient. Pinning a colour
    // here would make the header title navy-on-navy and invisible.
    h4: { fontWeight: 700 },
    h5: { fontWeight: 600 },
    h6: { fontWeight: 600 },
    subtitle1: { fontWeight: 500 },
    button: { fontWeight: 600 },
  },

  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: "#FFFFFF" },
      },
    },
    MuiPaper: {
      defaultProps: { variant: "outlined" },
      styleOverrides: {
        root: { borderRadius: 12, borderColor: BORDER },
      },
    },
    MuiCard: {
      defaultProps: { variant: "outlined" },
      styleOverrides: {
        root: { borderRadius: 12, borderColor: BORDER },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 600 },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: { textTransform: "none", fontWeight: 600, borderRadius: 8 },
        containedPrimary: {
          boxShadow: "none",
          "&:hover": { backgroundColor: VIOLET_DARK, boxShadow: "none" },
        },
      },
    },
    MuiToggleButton: {
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 600,
          "&.Mui-selected": {
            backgroundColor: VIOLET,
            color: "#FFFFFF",
            "&:hover": { backgroundColor: VIOLET_DARK },
          },
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        head: {
          fontWeight: 700,
          color: NAVY,
          backgroundColor: SURFACE_ALT,
          borderBottom: `1px solid ${BORDER}`,
        },
        root: { borderColor: BORDER },
      },
    },
    MuiTableRow: {
      styleOverrides: {
        root: {
          "&:hover": { backgroundColor: VIOLET_TINT },
        },
      },
    },
    MuiTableSortLabel: {
      styleOverrides: {
        root: {
          "&.Mui-active": { color: VIOLET },
          "&.Mui-active .MuiTableSortLabel-icon": { color: `${VIOLET} !important` },
        },
      },
    },
    MuiTabs: {
      styleOverrides: {
        indicator: { backgroundColor: VIOLET, height: 3 },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 600,
          "&.Mui-selected": { color: VIOLET },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
            borderColor: VIOLET,
            borderWidth: 2,
          },
        },
        notchedOutline: { borderColor: BORDER },
      },
    },
    MuiInputLabel: {
      styleOverrides: {
        root: { "&.Mui-focused": { color: VIOLET } },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        root: { borderRadius: 999, backgroundColor: "#EDE9F5" },
        bar: { borderRadius: 999 },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: { borderRadius: 10 },
        standardError: {
          backgroundColor: STATUS_TOKENS["NON-COMPLIANT"].tint,
          color: STATUS_RED,
        },
        standardWarning: {
          backgroundColor: STATUS_TOKENS.NEEDS_REVIEW.tint,
          color: STATUS_TOKENS.NEEDS_REVIEW.main,
        },
        standardSuccess: {
          backgroundColor: STATUS_TOKENS.COMPLIANT.tint,
          color: STATUS_GREEN,
        },
        standardInfo: { backgroundColor: VIOLET_TINT, color: VIOLET_DARK },
      },
    },
    MuiStepIcon: {
      styleOverrides: {
        root: {
          color: VIOLET_MUTED,
          "&.Mui-active": { color: BLUE },
          "&.Mui-completed": { color: VIOLET },
        },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: { backgroundColor: NAVY, fontSize: "0.75rem" },
        arrow: { color: NAVY },
      },
    },
  },
});

export default theme;
