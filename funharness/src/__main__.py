"""
FunHarness - Entry Point

Run with: fh (after pip install -e .)
Or: python -m funharness.src
"""
import os
import sys
from pathlib import Path

# Name of the isolated workspace directory (relative to where fh is launched)
WORKSPACE_DIR = "defaultspace"


def main():
    """Main entry point for the fh command."""
    if len(sys.argv) > 1 and sys.argv[1] == "feishu":
        from funharness.src.channels.feishu import main as feishu_main
        feishu_main(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "qqbot":
        from funharness.src.channels.qqbot import main as qqbot_main
        qqbot_main(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "weixin":
        from funharness.src.channels.weixin import main as weixin_main
        weixin_main(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "weixin-login":
        from funharness.src.channels.weixin import login_main as weixin_login
        weixin_login(sys.argv[2:])
        return

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
