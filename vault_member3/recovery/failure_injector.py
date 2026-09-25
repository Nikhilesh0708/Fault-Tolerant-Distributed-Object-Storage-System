"""Faults affect only the explicit demo API, never OS processes or real storage."""
import argparse
import os
from urllib.parse import urlsplit
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--action", required=True, choices=["stop", "restart", "corrupt", "delete-replica", "delay", "timeout", "reject", "outdated", "full", "invalid"])
    parser.add_argument("--object", dest="object_id")
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--url", default="http://127.0.0.1:8003")
    args = parser.parse_args()
    if urlsplit(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Demo injection is restricted to loopback hosts")
    headers = {}
    if token := os.getenv("VAULT_ADMIN_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    try:
        with httpx.Client(timeout=10, trust_env=False, headers=headers) as client:
            response = client.get(args.url.rstrip("/") + "/healthz")
            response.raise_for_status()
            if response.json().get("mode") != "demo":
                parser.error("Server is not in demo mode")
            response = client.post(args.url.rstrip("/") + "/demo/faults", json={
                "node_id": args.node, "action": args.action, "object_id": args.object_id, "seconds": args.seconds})
            response.raise_for_status()
            print(response.text)
    except (httpx.HTTPError, ValueError) as exc:
        parser.exit(1, f"Demo command failed: {exc}\n")


if __name__ == "__main__":
    main()
