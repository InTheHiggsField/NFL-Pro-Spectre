"""Start the NFL Pro Spectre website.

    python run_web.py            # http://127.0.0.1:8000, reloads when code changes
    python run_web.py --port 8080 --host 0.0.0.0   # reachable from other devices on your network
"""
import argparse

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-reload", action="store_true")
    args = ap.parse_args()
    print(f"NFL Pro Spectre running at http://{args.host}:{args.port}")
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=not args.no_reload)
