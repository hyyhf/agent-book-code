"""
FunHarness - TUI Theme

Chinese ochre-yellow inspired dark theme with icons and border config.
"""
from textual.theme import Theme

# ----------------------------------------------------------------
#  Color Palette
# ----------------------------------------------------------------

OCHRE_BRIGHT = "#E8C847"
OCHRE_PRIMARY = "#C68E17"
OCHRE_DEEP = "#9E6F0B"
OCHRE_DIM = "#7A5C18"
OCHRE_MUTED = "#514627"

SURFACE_DARK = "#12100D"
SURFACE_MID = "#1B1813"
SURFACE_LIGHT = "#252016"
SURFACE_RAISED = "#211D17"
PANEL_BG = "#181510"

TEXT_PRIMARY = "#E8DCC8"
TEXT_SECONDARY = "#B5A98F"
TEXT_DIM = "#7F735C"

SUCCESS_COLOR = "#7BA05B"
ERROR_COLOR = "#C75050"
WARNING_COLOR = "#D4A017"
INFO_COLOR = "#5B9BD5"
ACCENT_COOL = "#6EA6A1"

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
