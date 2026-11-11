"""Live Game business failover and Base discovery scaling; restores replica counts.

Existing WebSocket connections are transient and retain their owner. Authoritative
Game business state and action claims live in PostgreSQL and can use a sibling.
"""

import subprocess
import time
import uuid
from pathlib import Path

from lab4_client import Lab4Client, http

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "deploy/docker-compose.lab4.yml"]


def compose(*args):
    subprocess.run(COMPOSE + list(args), cwd=ROOT, check=True, capture_output=True)


def eventually(check):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(1)
    raise AssertionError("Timed out waiting for eligible replicas")


def main():
    client = Lab4Client()

    def active(service):
        return [
            item
            for item in client.registry()
            if item["service"] == service
            and item["healthy"]
            and item["lease_valid"]
            and not item["removed"]
        ]

    try:
        eventually(lambda: len(active("game-service")) == 2)
        suffix = uuid.uuid4().hex[:10]
        account = {
            "username": "ha_" + suffix,
            "email": suffix + "@lab4.test",
            "password": "Lab4-" + uuid.uuid4().hex,
        }
        http(
            client.gateway,
            "/api/v1/players/register",
            method="POST",
            body=account,
            expected=201,
        )
        tokens, _ = http(
            client.gateway,
            "/api/v1/players/login",
            method="POST",
            body={key: account[key] for key in ("email", "password")},
        )
        headers = {
            "Authorization": "Bearer " + tokens["access_token"],
            "Idempotency-Key": str(uuid.uuid4()),
        }
        lobby, _ = http(
            client.gateway,
            "/api/v1/lobbies",
            method="POST",
            body={"name": "failover-" + suffix, "university": "FAF", "max_players": 8},
            headers=headers,
            expected=201,
        )
        owner, _ = http(
            client.discovery,
            "/v1/select/game-service?affinity=" + lobby["lobby_id"],
            headers={"Authorization": "Bearer " + client.env["DISCOVERY_TOKEN"]},
        )
        assert owner["instance_id"] in ("game-1", "game-2"), (
            "This check targets the local Lab 4 stack"
        )
        service = (
            "game-service" if owner["instance_id"] == "game-1" else "game-service-2"
        )
        try:
            compose("stop", "-t", "10", service)
            result, response = http(
                client.gateway,
                "/api/v1/lobbies/" + lobby["lobby_id"],
                headers={"Authorization": headers["Authorization"]},
            )
            assert result["lobby_id"] == lobby["lobby_id"]
            assert response["X-Service-Instance"] != owner["instance_id"]
        finally:
            compose("up", "-d", "--no-deps", "--pull", "never", service)
        print(
            "PASS: Game owner stopped; persisted lobby request succeeds on healthy sibling",
            flush=True,
        )

        eventually(lambda: len(active("base-service")) == 2)
        try:
            compose(
                "up",
                "-d",
                "--no-deps",
                "--pull",
                "never",
                "--scale",
                "base-service=3",
                "base-service",
            )
            eventually(lambda: len(active("base-service")) == 3)
            selected = set()

            def selected_all_workers():
                selected.add(
                    client.call("base-service", "/api/v1/status")[1][
                        "X-Service-Instance"
                    ]
                )
                return len(selected) == 3

            # Discovery probes and Broker snapshots refresh asynchronously.
            eventually(selected_all_workers)

        finally:
            compose(
                "up",
                "-d",
                "--no-deps",
                "--pull",
                "never",
                "--scale",
                "base-service=2",
                "base-service",
            )
        eventually(lambda: len(active("base-service")) == 2)
        print(
            "PASS: Base scales 2 -> 3 -> 2 without static upstream edits or Broker/Gateway restart",
            flush=True,
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
