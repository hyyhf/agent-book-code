"""
FunHarness - Entry Point

Run with: fh (after pip install -e .)
Or: python -m funharness.src
"""

def main():
    """Main entry point for the fh command."""
    from funharness.src.tui.app import FunHarnessApp
    app = FunHarnessApp()
    app.run()


if __name__ == "__main__":
    main()
