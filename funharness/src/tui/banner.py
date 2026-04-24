"""
FunHarness - ASCII Art Banner

Gradient ochre-yellow FUN HARNESS banner using Rich markup.
"""
import os

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

    parts = [
        "",  # top margin
        get_banner_rich(),
        f"[#A89878]v1.0[/]  [#786848]An Minimalist AI Agent[/]",
        sep_thin,
        f"[#786848]  Model : [bold #E8C847]{model}[/][/]",
        f"[#786848]  Cmds  : [bold #E8C847]/help[/] for all commands[/]",
        f"[#786848]  Exit  : [bold #E8C847]quit[/] or [bold #E8C847]Ctrl+C[/]",
        f"[#786848]  Clear : [bold #E8C847]Ctrl+L[/][/]",
        sep_thin,
    ]
    return "\n".join(parts)

