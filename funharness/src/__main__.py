"""
FunHarness - Entry Point

Run with: fh (after pip install -e .)
Or: python -m funharness.src
"""
import os
from pathlib import Path

# Name of the isolated workspace directory (relative to where fh is launched)
WORKSPACE_DIR = "defaultspace"


def main():
    """Main entry point for the fh command."""
    # Resolve workspace relative to the original launch directory
    launch_dir = Path.cwd()
    workspace = launch_dir / WORKSPACE_DIR
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)

    from funharness.src.tui.app import FunHarnessApp
    app = FunHarnessApp()
    app.run()


if __name__ == "__main__":
    main()
