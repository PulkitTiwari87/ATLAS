"""Entry point for a worker process talking to the control plane over gRPC.

Connection target is ATLAS_GRPC_TARGET (host:port), defaulting to
localhost:{settings.grpc_port} for local development.
"""

import os
import signal
import time

import grpc

from atlas.config import get_settings
from atlas.logging import setup_logging
from atlas.worker.grpc_client import GrpcWorkerClient


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    target = os.getenv("ATLAS_GRPC_TARGET", f"localhost:{settings.grpc_port}")
    channel = grpc.insecure_channel(target)
    client = GrpcWorkerClient(channel)
    client.register()

    stop = {"requested": False}

    def _handle_signal(signum, frame):
        stop["requested"] = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        while not stop["requested"]:
            time.sleep(1)
    finally:
        client.shutdown()
        channel.close()


if __name__ == "__main__":
    main()
