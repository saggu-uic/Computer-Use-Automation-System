"""Run FakeBank Core: `python -m fakebank --port 8700 --control-port 8701`."""

from __future__ import annotations

import argparse
import asyncio

import uvicorn

from fakebank.app import Bank, create_apps, credentials_from_env


async def serve(port: int, control_port: int, host: str) -> None:
    operator_id, password = credentials_from_env()
    app, control = create_apps(Bank(operator_id, password))
    servers = [
        uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning")),
        uvicorn.Server(uvicorn.Config(control, host=host, port=control_port, log_level="warning")),
    ]
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    parser = argparse.ArgumentParser(description="FakeBank Core (fake legacy core banking app)")
    parser.add_argument("--port", type=int, default=8700)
    parser.add_argument("--control-port", type=int, default=8701)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    asyncio.run(serve(args.port, args.control_port, args.host))


if __name__ == "__main__":
    main()
