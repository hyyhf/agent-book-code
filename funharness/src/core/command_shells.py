"""Explicit shell selection without routing PowerShell through cmd.exe."""
from __future__ import annotations

import base64
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ShellName = Literal["default", "cmd", "powershell", "pwsh", "sh", "bash"]


@dataclass(frozen=True)
class CommandShell:
    name: str
    executable: str

    @property
    def is_powershell(self):
        return self.name in {"powershell", "pwsh"}


def resolve_shell(name: str = "default") -> CommandShell:
    if not isinstance(name, str):
        raise ValueError("shell must be default, cmd, powershell, pwsh, sh, or bash")
    name = name.strip().lower()
    if name == "default":
        if os.name != "nt":
            return CommandShell("sh", "/bin/sh")
        name = "cmd"
    if name not in {"cmd", "powershell", "pwsh", "sh", "bash"}:
        raise ValueError(f"Unsupported shell: {name!r}; use default, cmd, powershell, pwsh, sh, or bash")
    if name in {"cmd", "powershell"}:
        if os.name != "nt":
            raise ValueError(f"shell={name} requires Windows; use pwsh for PowerShell on other platforms")
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        executable = system32 / ("cmd.exe" if name == "cmd" else "WindowsPowerShell/v1.0/powershell.exe")
        if executable.is_file():
            return CommandShell(name, str(executable))
    executable = shutil.which(name)
    if not executable and name == "pwsh" and os.name == "nt":
        # Standard PowerShell 7 installation may not be present in a GUI's PATH.
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell/7/pwsh.exe"
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        hint = "; use shell=powershell for built-in Windows PowerShell 5.1" if name == "pwsh" and os.name == "nt" else ""
        raise ValueError(f"Shell {name!r} is not installed or not on PATH{hint}")
    # Preserve the invocation name: /bin/sh may be a symlink to bash, whose
    # behavior changes if invoked under its real basename instead of "sh".
    return CommandShell(name, str(Path(executable).absolute()))


def powershell_argv(shell: CommandShell, script_path: Path) -> list[str]:
    # Only this small bootstrap goes on the command line. User code is UTF-8 in
    # a private temporary file, avoiding Windows' command-line length limit and
    # cmd expansion of $, %, quotes, newlines, ampersands and pipe characters.
    # ScriptBlock.Create parses the unmodified source (including using/param),
    # independently of the bootstrap; no execution-policy changes are required.
    literal_path = str(script_path).replace("'", "''")
    bootstrap = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$global:LASTEXITCODE = 0
try {{
    . ([scriptblock]::Create([IO.File]::ReadAllText('{literal_path}', [Text.Encoding]::UTF8)))
    $fhCommandSucceeded = $?
    if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}
    if (-not $fhCommandSucceeded) {{ exit 1 }}
    exit 0
}} catch {{
    [Console]::Error.WriteLine(($_ | Out-String))
    exit 1
}}
"""
    encoded = base64.b64encode(bootstrap.encode("utf-16-le")).decode("ascii")
    return [shell.executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text",
            "-EncodedCommand", encoded]


def available_shell_names() -> list[str]:
    names = []
    for name in ("cmd", "powershell", "pwsh", "sh", "bash"):
        try:
            resolve_shell(name)
            names.append(name)
        except ValueError:
            pass
    return names
