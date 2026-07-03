"""Command line entrypoint for the KVT-C MQTT bridge."""
import argparse
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mqtt_bridge.service import MqttBridgeService


def parse_args():
    parser = argparse.ArgumentParser(description="KVT-C MQTT bridge")
    parser.add_argument("--broker-host", default=None, help="Override mqtt_config.json broker.host")
    parser.add_argument("--broker-port", type=int, default=None, help="Override mqtt_config.json broker.port")
    parser.add_argument("--client-id", default=None, help="Override mqtt_config.json broker.client_id")
    return parser.parse_args()


def main():
    args = parse_args()
    overrides = {
        "host": args.broker_host,
        "port": args.broker_port,
        "client_id": args.client_id,
    }
    service = MqttBridgeService(overrides={k: v for k, v in overrides.items() if v is not None})
    for sig in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, sig, None)
        if signum is not None:
            try:
                signal.signal(signum, service.stop)
            except (ValueError, RuntimeError):
                pass
    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
