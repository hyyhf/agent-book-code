from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FunHarness GUI backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    uvicorn.run("funGUI.backend.app:create_app", host=args.host, port=args.port, factory=True)


if __name__ == "__main__":
    main()
