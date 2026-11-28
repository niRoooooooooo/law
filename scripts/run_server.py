"""
Start Tor Lawyer for local use and Cloudflare Tunnel team testing.

    python scripts/run_server.py            # http://127.0.0.1:8000
    python scripts/run_server.py --port 8080 --reload

Why not plain `uvicorn app.main:app`?
- Uvicorn's default --proxy-headers rewrites the client address
  from X-Forwarded-For for connections from 127.0.0.1. Cloudflare
  Tunnel always sends that header, so the app could no longer see
  that the request came from local cloudflared, and a local caller
  could pick their own rate-limit identity. This launcher turns
  that rewriting off; the app reads CF-Connecting-IP itself, and
  only from the loopback peer.
- Binds to 127.0.0.1 only: reach it remotely through the tunnel,
  never by exposing the port (or Ollama's 11434) to the network.
- One worker: AI rate/concurrency limits are kept in memory.

Equivalent uvicorn command:
    uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
"""

import argparse
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=args.port,
        workers=1,
        reload=args.reload,
        proxy_headers=False,
        app_dir=str(PROJECT_ROOT),
    )


if __name__ == "__main__":
    main()
