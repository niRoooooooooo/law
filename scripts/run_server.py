"""
Start Tor Lawyer for local development and Cloudflare Tunnel team testing.

Development command (from C:\\Projects\\law):

    .venv\\Scripts\\python scripts\\run_server.py --reload

Then open http://127.0.0.1:8000. Options: --port 8080, no --reload.

Why this launcher instead of plain `uvicorn app.main:app`?
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
- --reload watches the project's app/ folder wherever you start it.
- Refuses to start if the port is already taken (often an old server
  left running), instead of letting the old server keep answering.

Equivalent uvicorn command (run from the project folder):
    uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
"""

import argparse
import socket
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((HOST, port)) == 0


def main() -> None:

    parser = argparse.ArgumentParser(
        description="Start Tor Lawyer on 127.0.0.1."
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if port_in_use(args.port):
        sys.exit(
            f"Port {args.port} is already in use, probably by an older "
            "Tor Lawyer server that is still running.\n"
            "Find it:  netstat -ano | findstr :{0}\n"
            "Stop it:  taskkill /F /T /PID <pid>   "
            "(or use --port to pick another port)".format(args.port)
        )

    sys.path.insert(0, str(PROJECT_ROOT))

    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=args.port,
        workers=1,
        reload=args.reload,
        reload_dirs=[str(PROJECT_ROOT / "app")] if args.reload else None,
        proxy_headers=False,
        app_dir=str(PROJECT_ROOT),
    )


if __name__ == "__main__":
    main()
