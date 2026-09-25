"""Launch four local storage node processes.

Usage: python run_nodes.py
Stop with Ctrl+C; all child node processes are terminated together.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

NODES = [
    ("node-1", 9001, "./data/node-1"),
    ("node-2", 9002, "./data/node-2"),
    ("node-3", 9003, "./data/node-3"),
    ("node-4", 9004, "./data/node-4"),
]


def main() -> None:
    procs = []
    for node_id, port, data_dir in NODES:
        env = os.environ.copy()
        env["NODE_ID"] = node_id
        env["NODE_PORT"] = str(port)
        env["NODE_DATA_DIR"] = data_dir
        proc = subprocess.Popen([sys.executable, "-m", "backend.node_app"], env=env)
        procs.append(proc)
        print(f"started {node_id} on port {port} (pid {proc.pid}), data dir {data_dir}")

    def shutdown(*_args):
        print("\nstopping storage nodes...")
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
