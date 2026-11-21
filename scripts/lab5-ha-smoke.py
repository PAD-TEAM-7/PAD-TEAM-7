"""Verify assigned service flows against a running Lab 5 deployment."""
import argparse
import json
import runpy
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from lab4_client import ROOT, Lab4Client, http


class Demo:
    def __init__(self, client, values):
        self.client = client
        self.values = values
        self.player = values["playerId"]
        self.lobby = values["lobbyId"]
        self.base = values["baseId"]

    def request(self, path, **options):
        headers = {"Authorization": "Bearer " + self.values["playerToken"],
                   "Idempotency-Key": str(uuid4()), **options.pop("headers", {})}
        return http(self.client.gateway, path, headers=headers, **options)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", choices=("islam", "artiom", "maxim", "nichita"), action="append")
    parser.add_argument("--env-file", type=Path, default=ROOT / "deploy/.env")
    parser.add_argument("--gateway", default="http://127.0.0.1:28080")
    parser.add_argument("--broker-grpc", default="127.0.0.1:28600")
    parser.add_argument("--broker-http", default="http://127.0.0.1:28601")
    parser.add_argument("--discovery", default="http://127.0.0.1:28500")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    def connect():
        return Lab4Client(env_file=args.env_file, target=args.broker_grpc, gateway=args.gateway,
                          operations=args.broker_http, discovery=args.discovery)

    module = runpy.run_path(str(ROOT / "scripts/lab4-prepare-postman.py"))
    prepare = module["prepare"]
    prepare.__globals__["Lab4Client"] = connect
    with tempfile.TemporaryDirectory(prefix="lab5-fixture-") as directory:
        path = Path(directory) / "environment.json"
        prepare(path)
        values = {entry["key"]: entry["value"] for entry in json.loads(path.read_text())["values"]}
    client = connect()
    try:
        demo = Demo(client, values)
        for owner in args.owner or ("islam", "artiom", "maxim", "nichita"):
            check = runpy.run_path(str(ROOT / "scripts/lab5_acceptance" / f"{owner}.py"))["verify"]
            check(demo)
    finally:
        client.close()


if __name__ == "__main__":
    main()
