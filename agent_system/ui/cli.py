"""agent-ui entry point. Imports FastAPI lazily so a core-only install gets a clear hint."""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agent-ui", description="Local model/provider control UI")
    parser.add_argument("--host", default=DEFAULT_HOST, help="loopback address to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root", default=".", help="orchestrator root containing config/ (default: cwd)")
    args = parser.parse_args(argv)
    if not _is_loopback(args.host):
        raise SystemExit(f"agent-ui refuses to bind {args.host!r}: V1 has no authentication and is loopback-only")
    root = Path(args.root).resolve()
    if not (root / "config" / "models.yaml").exists():
        raise SystemExit(f"config/models.yaml not found under {root}")
    try:
        import uvicorn

        from .app import create_app
    except ImportError:
        raise SystemExit("agent-ui requires the UI extras: pip install -e '.[ui]'")
    print(f"Multi-Agent Coder model control UI: http://{args.host}:{args.port}", flush=True)
    uvicorn.run(create_app(root), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
