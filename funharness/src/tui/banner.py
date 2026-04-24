"""
FunHarness - ASCII Art Banner

Gradient ochre-yellow FUN HARNESS banner using Rich markup.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Ensure .env is loaded before reading env vars
def _find_env():
    d = Path(__file__).resolve().parent
    for _ in range(5):
        env = d / ".env"
        if env.exists():
            return env
        d = d.parent
    return None

_env = _find_env()
if _env:
    load_dotenv(_env)

# Ochre gradient from bright to deep
_GRADIENT = [
    "#E8C847",  # Lightest golden
    "#D4A017",  # Bright ochre
    "#C68E17",  # Primary ochre
    "#B8860B",  # Dark goldenrod
    "#A87A0A",  # Deeper ochre
    "#8B6914",  # Dim ochre
    "#7A5C12",  # Deep ochre
]

_BANNER_LINES = [
    r"███████╗██╗   ██╗███╗   ██╗    ██╗  ██╗ █████╗ ██████╗ ███╗   ██╗███████╗███████╗███████╗",
    r"██╔════╝██║   ██║████╗  ██║    ██║  ██║██╔══██╗██╔══██╗████╗  ██║██╔════╝██╔════╝██╔════╝",
    r"█████╗  ██║   ██║██╔██╗ ██║    ███████║███████║██████╔╝██╔██╗ ██║█████╗  ███████╗███████╗",
    r"██╔══╝  ██║   ██║██║╚██╗██║    ██╔══██║██╔══██║██╔══██╗██║╚██╗██║██╔══╝  ╚════██║╚════██║",
    r"██║     ╚██████╔╝██║ ╚████║    ██║  ██║██║  ██║██║  ██║██║ ╚████║███████╗███████║███████║",
    r"╚═╝      ╚═════╝ ╚═╝  ╚═══╝    ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚══════╝",
]

_LINE_CHAR = "\u2500"  # horizontal box-drawing line


def get_banner_rich() -> str:
    """Return the banner with Rich-compatible color markup."""
    lines = []
    for i, line in enumerate(_BANNER_LINES):
        color = _GRADIENT[i % len(_GRADIENT)]
        lines.append(f"[{color}]{line}[/]")
    return "\n".join(lines)


def get_banner_plain() -> str:
    """Return the raw ASCII banner without color."""
    return "\n".join(_BANNER_LINES)


def get_full_banner() -> str:
    """Return banner + subtitle + hint for TUI display."""
    model = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")
    sep_thin = f"[#5C4A1C]{_LINE_CHAR * 68}[/]"

    # 2x2 grid layout: left column ~34 chars, right column fills the rest
    col_left_w = 34
    row1_left = f"Model : [bold #E8C847]{model}[/]"
    row1_right = f"Cmds  : [bold #E8C847]/help[/] for all commands"
    row2_left = f"Exit  : [bold #E8C847]quit[/] or [bold #E8C847]Ctrl+C[/]"
    row2_right = f"Clear : [bold #E8C847]Ctrl+L[/]"

    # Pad the left column for alignment (using visible char count estimate)
    pad1 = " " * max(0, col_left_w - len(f"Model : {model}"))
    pad2 = " " * max(0, col_left_w - len("Exit  : quit or Ctrl+C"))

    parts = [
        "",  # top margin
        get_banner_rich(),
        f"[#A89878]v1.0[/]  [#786848]「-- An Minimalist AI Agent --」[/]",
        sep_thin,
        f"[#786848]  {row1_left}{pad1}{row1_right}[/]",
        f"[#786848]  {row2_left}{pad2}{row2_right}[/]",
        sep_thin,
    ]
    return "\n".join(parts)

