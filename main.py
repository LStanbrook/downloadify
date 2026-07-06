"""
Downloadify entrypoint.

Run the desktop GUI (default):
    python main.py
    python main.py --mode gui

Run the web app instead:
    python main.py --mode web
    python main.py --mode web --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="downloadify",
        description="Download every track of a public Spotify playlist as MP3s.",
    )
    parser.add_argument(
        "--mode",
        choices=["gui", "web"],
        default="gui",
        help="Run the PyQt6 desktop app ('gui', default) or the FastAPI web app ('web').",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Web mode only: bind host.")
    parser.add_argument("--port", type=int, default=8000, help="Web mode only: bind port.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.mode == "gui":
        from downloadify.gui.main_window import run_gui

        run_gui()
    else:
        import uvicorn

        print(f"Downloadify web app running at http://{args.host}:{args.port}")
        uvicorn.run("downloadify.web.server:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
