"""Live durable delivery check: stop Exam, accept a real registration, restart Broker.

Only the pad-lab4 deployment is changed. Volumes are retained. Exam replicas are
restarted in finally even when a check fails. No credentials or token bodies print.
"""

import json
import subprocess
import time
import urllib.error
import uuid
from pathlib import Path

from lab4_client import Lab4Client, http

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "deploy/docker-compose.lab4.yml"]


def compose(*args):
    return subprocess.run(
        COMPOSE + list(args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def eventually(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (OSError, urllib.error.URLError):
            pass  # Docker restart temporarily closes the management listener.
        except AssertionError as error:
            if "received 503" not in str(error) and "received 504" not in str(error):
                raise  # Retry only transient availability while reconnecting; no domain errors.
        time.sleep(1)
    raise AssertionError("Timed out waiting for durable delivery recovery")


def main():
    client = Lab4Client()
    old_dead = {
        item["id"] for item in client.ops("/v1/dead-letters")[0]["dead_letters"]
    }
    exam_stopped = False
    try:
        compose("stop", "-t", "10", "exam-service")
        exam_stopped = True
        suffix = uuid.uuid4().hex[:12]
        account = {
            "username": "durable_" + suffix,
            "email": suffix + "@lab4.test",
            "password": "Lab4-" + uuid.uuid4().hex,
        }
        player, _ = http(
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
        player_id = str(uuid.UUID(player["player_id"]))
        sql = (
            "SELECT event_id FROM broker_outbox WHERE status='delivered' "
            "AND payload->>'type'='PlayerRegistered' AND payload->'payload'->>'player_id'='"
            + player_id
            + "'"
        )

        def accepted():
            return compose(
                "exec",
                "-T",
                "player-db",
                "psql",
                "-U",
                "player",
                "-d",
                "player_db",
                "-At",
                "-c",
                sql,
            ).strip()

        event_id = str(uuid.UUID(eventually(accepted)))
        print(
            "PASS: actual Player outbox received durable Broker acceptance while Exam was stopped",
            flush=True,
        )
        compose("restart", "-t", "10", "message-broker")
        eventually(lambda: http(client.operations, "/health")[0]["status"] == "ok")
        restored_dead = {
            item["id"] for item in client.ops("/v1/dead-letters")[0]["dead_letters"]
        }
        assert old_dead <= restored_dead, "Dead letters disappeared across restart"
        compose("up", "-d", "--no-deps", "--pull", "never", "exam-service")
        exam_stopped = False

        def enrolled():
            payload, _ = http(
                client.gateway,
                "/api/v1/players/" + player_id + "/courses",
                headers={"Authorization": "Bearer " + tokens["access_token"]},
            )
            return payload.get("items") or payload.get("courses")

        eventually(enrolled)
        query = (
            "SELECT count(*) FROM processed_events WHERE event_id='" + event_id + "'"
        )
        eventually(
            lambda: (
                compose(
                    "exec",
                    "-T",
                    "exam-db",
                    "psql",
                    "-U",
                    "exam",
                    "-d",
                    "exam_db",
                    "-At",
                    "-c",
                    query,
                ).strip()
                == "1"
            )
        )
        print(
            "PASS: queued registration survives Broker restart, enrols on Exam recovery exactly once; dead letters persist",
            flush=True,
        )
    finally:
        if exam_stopped:
            compose("up", "-d", "--no-deps", "--pull", "never", "exam-service")
        client.close()


if __name__ == "__main__":
    main()
