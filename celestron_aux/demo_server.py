"""Explicit PC-only composition for observing AUX client traffic safely."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from celestron_aux.dispatcher import AUXDispatcher
from celestron_aux.tcp_server import AUXTCPServer
from fake_mount_backend import FakeMountBackend
from mount_api import MountController


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="No-reply AUX TCP skeleton using FakeMountBackend.")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--capture-root", type=Path, default=Path("captures"))
    parser.add_argument("--client", default="SkyPortal")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    controller = MountController(FakeMountBackend())
    server = AUXTCPServer(
        AUXDispatcher(controller), bind=args.bind, port=args.port,
        capture_root=args.capture_root, client_label=args.client,
        allow_synthetic_replies=False,
    )
    try:
        server.serve_forever()
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
