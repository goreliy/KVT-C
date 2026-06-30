"""Command line entrypoint for the KVT-C OPC UA server."""
import argparse
import asyncio
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opcua_server.service import OpcUaService


def parse_args():
    parser = argparse.ArgumentParser(description="KVT-C OPC UA server")
    parser.add_argument("--host", default=None, help="Override opcua_config.json server.host")
    parser.add_argument("--port", type=int, default=None, help="Override opcua_config.json server.port")
    parser.add_argument("--endpoint-path", default=None, help="Override opcua_config.json server.endpoint_path")
    return parser.parse_args()


async def main_async(args):
    overrides = {
        "host": args.host,
        "port": args.port,
        "endpoint_path": args.endpoint_path,
    }
    service = OpcUaService(overrides={k: v for k, v in overrides.items() if v is not None})
    loop = asyncio.get_running_loop()
    for sig in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, sig, None)
        if signum is None:
            continue
        try:
            loop.add_signal_handler(signum, service.stop)
        except (NotImplementedError, RuntimeError):
            pass
    await service.run_forever()


def main():
    args = parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
