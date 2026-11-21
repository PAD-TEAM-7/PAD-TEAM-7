"""Exercise 2PC through Crafting and verify database-backed recovery on Lab 5."""

import argparse
import json
import runpy
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from lab4_client import ROOT, Lab4Client, http


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="pad-lab5-2pc")
    parser.add_argument("--env-file", type=Path, default=ROOT / "deploy/.env")
    parser.add_argument("--gateway", default="http://127.0.0.1:28080")
    parser.add_argument("--broker-grpc", default="127.0.0.1:28600")
    parser.add_argument("--broker-http", default="http://127.0.0.1:28601")
    parser.add_argument("--discovery", default="http://127.0.0.1:28500")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    def connect():
        return Lab4Client(
            env_file=args.env_file, gateway=args.gateway, target=args.broker_grpc,
            operations=args.broker_http, discovery=args.discovery,
        )

    prepare = runpy.run_path(str(ROOT / "scripts/lab4-prepare-postman.py"))["prepare"]
    prepare.__globals__["Lab4Client"] = connect
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "private.json"
        prepare(path)
        fixture = {entry["key"]: entry["value"] for entry in json.loads(path.read_text())["values"]}
    player, lobby = fixture["playerId"], fixture["lobbyId"]
    auth = {"Authorization": "Bearer " + fixture["playerToken"]}

    def read_balances():
        response, _ = http(
            args.gateway, f"/api/v1/players/{player}/resources?lobby_id={lobby}", headers=auth
        )
        return {row["item_id"]: row["amount"] for row in response["resources"]}

    def kits():
        response, _ = http(args.gateway, f"/api/v1/players/{player}/inventory", headers=auth)
        return next((item["count"] for item in response["items"]
                     if item["item_id"] == "barricade-kit-01"), 0)

    def recovery(mode, actor):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/lab5-2pc-verify.py"), "--project", args.project,
             "--mode", mode, "--player", actor, "--lobby", lobby],
            check=True, timeout=80,
        )

    before, reward = read_balances(), kits()
    operation_headers = {**auth, "Idempotency-Key": str(uuid4())}
    operation = {"player_id": player, "lobby_id": lobby, "recipe_id": "recipe-barricade-kit"}
    craft, _ = http(args.gateway, "/api/v1/crafts", method="POST", body=operation,
                    headers=operation_headers, expected=201)
    replay, _ = http(args.gateway, "/api/v1/crafts", method="POST", body=operation,
                     headers=operation_headers, expected=201)
    assert craft == replay and craft["consume_tx_id"] == craft["job_id"]
    after = read_balances()
    assert after["wood-01"] == before["wood-01"] - 4
    assert after["metal-01"] == before["metal-01"] - 2 and kits() == reward + 1
    print("PASS: real two-phase Crafting request spent once, credited once, and replayed.")
    recovery("commit", player)
    after_restart = read_balances()
    assert after_restart["wood-01"] == after["wood-01"] - 4
    assert after_restart["metal-01"] == after["metal-01"] - 2 and kits() == reward + 2

    absent = str(uuid4())
    inputs = [{"item_id": "wood-01", "count": 4}, {"item_id": "metal-01", "count": 2}]
    client = connect()
    try:
        client.call("resource-service", "/api/v1/transactions/gather", "POST",
                    {"lobby_id": lobby, "player_id": absent, "source_pool_id": fixture["sourcePoolId"],
                     "action_id": str(uuid4()), "reason": "lab5-2pc-fixture", "items": inputs},
                    source="resource-service", key=str(uuid4()))
    finally:
        client.close()
    # Both attempts can stage the same stock only if the prior abort released it.
    recovery("abort", absent)
    recovery("abort", absent)


if __name__ == "__main__":
    main()
