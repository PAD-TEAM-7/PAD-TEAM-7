"""Exercise a durable craft across an actual Broker restart in a Lab 5 project."""

import argparse
import json
import runpy
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from lab4_client import ROOT, Lab4Client, http
from lab4_rpc import broker_pb2 as pb


def wait_for(probe, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if value := probe():
                return value
        except (AssertionError, OSError):
            pass
        time.sleep(0.7)
    raise AssertionError("Timed out waiting for the expected Saga state.")


def verify_refund(client, fixture):
    """Use Resource fixture stock to exercise a definite Player rejection."""
    absent_player = str(uuid4())
    inputs = [{"item_id": "wood-01", "count": 4}, {"item_id": "metal-01", "count": 2}]
    client.call(
        "resource-service", "/api/v1/transactions/gather", "POST",
        {"lobby_id": fixture["lobbyId"], "player_id": absent_player,
         "source_pool_id": fixture["sourcePoolId"], "action_id": str(uuid4()),
         "reason": "lab5-refund-fixture", "items": inputs},
        source="resource-service", key=str(uuid4()),
    )
    # Fund one payment. A second compensated attempt proves the first was refunded.
    for _ in range(2):
        job = str(uuid4())
        command = pb.Command(
            method="POST", path="/api/v1/internal/craft-settlement", topic="commands.crafting",
            idempotency_key=job, request_id=job,
            body=json.dumps({"job_id": job, "player_id": absent_player,
                             "lobby_id": fixture["lobbyId"], "inputs": inputs,
                             "output": {"item_id": "barricade-kit-01", "count": 1}}).encode(),
        )

        def settled():
            result = client.stub.Call(command, metadata=client.metadata("crafting-service"), timeout=5)
            return result if result.status != 202 else None

        reply = wait_for(settled, timeout=30)
        assert reply.status == 200 and json.loads(reply.body)["status"] == "compensated"
    print("PASS: Player rejected both grants and Resource restored the materials after each rejection.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / "deploy/.env")
    parser.add_argument("--gateway", default="http://127.0.0.1:28080")
    parser.add_argument("--broker-grpc", default="127.0.0.1:28600")
    parser.add_argument("--broker-http", default="http://127.0.0.1:28601")
    parser.add_argument("--discovery", default="http://127.0.0.1:28500")
    parser.add_argument("--verify-refund", action="store_true")
    args = parser.parse_args()
    if not args.project.startswith("pad-lab5"):
        parser.error("Run against an isolated pad-lab5 test project.")

    def connect():
        return Lab4Client(
            env_file=args.env_file, target=args.broker_grpc, gateway=args.gateway,
            discovery=args.discovery, operations=args.broker_http,
        )

    def containers(service):
        answer = subprocess.check_output([
            "docker", "ps", "--filter", f"label=com.docker.compose.project={args.project}",
            "--filter", f"label=com.docker.compose.service={service}", "--format", "{{.Names}}",
        ], text=True, timeout=20)
        return answer.splitlines()

    def docker(action, *names):
        subprocess.run(["docker", action, *names], check=True, timeout=90, capture_output=True)

    players, brokers = containers("player-service"), containers("message-broker")
    if len(players) != 2 or len(brokers) != 1:
        raise RuntimeError("Expected two Player workers and one Broker before the demo.")

    prepare = runpy.run_path(str(ROOT / "scripts/lab4-prepare-postman.py"))["prepare"]
    prepare.__globals__["Lab4Client"] = connect
    with tempfile.TemporaryDirectory(prefix="pad-lab5-saga-") as directory:
        path = Path(directory) / "fixtures.json"
        prepare(path)
        values = {item["key"]: item["value"] for item in json.loads(path.read_text())["values"]}

    client = connect()
    headers = {"Authorization": "Bearer " + values["playerToken"]}
    player, lobby = values["playerId"], values["lobbyId"]
    trace = "lab5-saga-" + uuid4().hex

    def api(path, **kwargs):
        return http(client.gateway, path, headers=headers, **kwargs)[0]

    def resources():
        return {row["item_id"]: row["amount"] for row in api(
            f"/api/v1/players/{player}/resources?lobby_id={lobby}"
        )["resources"]}

    try:
        client.ops("/v1/sagas")
        before = resources()
        # Fill the verified signing-key cache before taking Player offline.
        api(f"/api/v1/players/{player}")
        try:
            docker("stop", *players)
            submit_headers = {**headers, "Idempotency-Key": trace, "X-Request-ID": trace}
            craft, _ = http(client.gateway, "/api/v1/crafts", method="POST", expected=202,
                            headers=submit_headers, body={
                "player_id": player, "lobby_id": lobby, "recipe_id": "recipe-barricade-kit",
            })
            assert craft["status"] == "pending"
            job = craft["job_id"]

            def grant_pending():
                rows = client.ops("/v1/sagas")[0]["sagas"]
                return next((item for item in rows if item["job_id"] == job
                             and item["state"] == "grant" and item["consume_tx_id"]), None)

            settlement = wait_for(grant_pending, timeout=25)
            assert settlement["request_id"] == trace
            docker("restart", brokers[0])
            persisted = wait_for(grant_pending, timeout=40)
            assert persisted["consume_tx_id"] == settlement["consume_tx_id"]
        finally:
            docker("start", *players)

        def completed():
            current = api(f"/api/v1/crafts/{job}")
            return current if current["status"] == "completed" else None

        result = wait_for(completed)
        inventory = api(f"/api/v1/players/{player}/inventory")["items"]
        after = resources()
        assert result["consume_tx_id"] == settlement["consume_tx_id"]
        assert after["wood-01"] == before["wood-01"] - 4
        assert after["metal-01"] == before["metal-01"] - 2
        assert [row["count"] for row in inventory if row["item_id"] == "barricade-kit-01"] == [1]
        print(f"PASS: {job} resumed after Broker restart, charged once, and granted exactly one kit.")
        if args.verify_refund:
            verify_refund(client, values)
    finally:
        client.close()


if __name__ == "__main__":
    main()
