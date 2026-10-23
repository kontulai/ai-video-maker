"""python -m server [--port 8000]: serve the API and the built browser app."""
import argparse

import uvicorn

from .app import app


def main() -> None:
    ap = argparse.ArgumentParser(description="AI video maker web app")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"Open http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
