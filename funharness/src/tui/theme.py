"""
FunHarness - TUI Theme

Chinese ochre-yellow inspired dark theme with icons and border config.
"""
from textual.theme import Theme

# ----------------------------------------------------------------
#  Color Palette
# ----------------------------------------------------------------

OCHRE_BRIGHT = "#D4A017"
OCHRE_PRIMARY = "#C68E17"
OCHRE_DEEP = "#B8860B"
OCHRE_DIM = "#8B6914"
OCHRE_MUTED = "#5C4A1C"

SURFACE_DARK = "#1A1510"
SURFACE_MID = "#231E14"
SURFACE_LIGHT = "#2E2618"
PANEL_BG = "#1E1A12"

TEXT_PRIMARY = "#E8DCC8"
TEXT_SECONDARY = "#A89878"
TEXT_DIM = "#786848"

SUCCESS_COLOR = "#7BA05B"
ERROR_COLOR = "#C75050"
WARNING_COLOR = "#D4A017"
INFO_COLOR = "#5B9BD5"

# ----------------------------------------------------------------
#  Icons (ASCII-safe unicode symbols, NOT emoji)
# ----------------------------------------------------------------

ICONS = {
    "agent": ">",
    "tool": ">>>",
    "result": "-->",
    "success": "[+]",
    "error": "[!]",
    "warning": "[~]",
    "info": "[i]",
    "denied": "[x]",
}

# Spinner animation frames (bouncing dot pattern)
SPINNER_FRAMES = [
    "*     ",
    " *    ",
    "  *   ",
    "   *  ",
    "    * ",
    "     *",
    "    * ",
    "   *  ",
    "  *   ",
    " *    ",
]

# Risk level display config
RISK_CONFIG = {
    "read":    {"badge": "R", "color": SUCCESS_COLOR,  "label": "READ"},
    "write":   {"badge": "W", "color": WARNING_COLOR,  "label": "WRITE"},
    "execute": {"badge": "X", "color": ERROR_COLOR,    "label": "EXEC"},
    "web":     {"badge": "N", "color": INFO_COLOR,     "label": "NET"},
}

# ----------------------------------------------------------------
#  Textual Theme
# ----------------------------------------------------------------

funharness_theme = Theme(
    name="funharness",
    primary=OCHRE_PRIMARY,
    secondary=OCHRE_DIM,
    accent=OCHRE_BRIGHT,
    warning=WARNING_COLOR,
    error=ERROR_COLOR,
    success=SUCCESS_COLOR,
    surface=SURFACE_MID,
    panel=PANEL_BG,
    dark=True,
    variables={
        "block-cursor-foreground": SURFACE_DARK,
        "block-cursor-background": OCHRE_PRIMARY,
        "input-cursor-foreground": SURFACE_DARK,
        "input-cursor-background": OCHRE_PRIMARY,
        "scrollbar-color": OCHRE_DIM,
        "scrollbar-color-hover": OCHRE_PRIMARY,
        "scrollbar-color-active": OCHRE_BRIGHT,
        "scrollbar-background": SURFACE_DARK,
        "input-selection-background": f"{OCHRE_DIM}80",
        "border": OCHRE_MUTED,
        "border-blurred": OCHRE_MUTED,
        "footer-key-foreground": SURFACE_DARK,
        "footer-key-background": OCHRE_PRIMARY,
    },
)
