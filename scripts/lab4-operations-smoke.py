"""Live Lab 4 operations acceptance against the local Compose deployment.

Uses a disposable, isolated two-worker service group. Sends genuine failure and
load observations; may send Discovery alerts through the configured SMTP channel.
The broker restart check preserves its volume and all accepted queued work.
"""

import io
import json
import os
import subprocess
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lab4_client import Lab4Client, http

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "deploy/docker-compose.lab4.yml"]


def eventually(check, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.5)
    raise AssertionError("Timed out waiting for expected operational state")


def main():
    client = Lab4Client()
    suffix = uuid.uuid4().hex[:10]
    container = "pad-lab4-fixture-" + suffix
    identity = "lab4-fixture-" + suffix
    fixture_env = {
        **os.environ,
        **{
            key: client.env[key]
            for key in ("DISCOVERY_TOKEN", "BROKER_WORKER_TOKEN", "BROKER_ADMIN_TOKEN")
        },
        "FIXTURE_ID": identity,
    }
    args = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--no-healthcheck",
        "--name",
        container,
        "--network",
        "pad-lab4_default",
        "--read-only",
        "--tmpfs",
        "/tmp",
        "-e",
        "PYTHONPATH=/app",
        "-p",
        "127.0.0.1:28602:8001",
        "-p",
        "127.0.0.1:28603:8002",
        "--mount",
        "type=bind,source=" + str(ROOT / "scripts") + ",target=/fixtures,readonly",
    ]
    for name in (
        "DISCOVERY_TOKEN",
        "BROKER_WORKER_TOKEN",
        "BROKER_ADMIN_TOKEN",
        "FIXTURE_ID",
    ):
        args += ["-e", name]
    args += [
        "unstoppabledestroyer/message-broker:4.0.0",
        "python",
        "/fixtures/lab4-worker-fixture.py",
    ]
    subprocess.run(args, cwd=ROOT, env=fixture_env, check=True, capture_output=True)

    def replicas():
        return [
            item
            for item in client.registry()
            if item["instance_id"].startswith(identity)
        ]

    def stats(port):
        return http("http://localhost:" + str(port), "/api/v1/status")[0]

    try:
        eventually(lambda: len([i for i in replicas() if i["healthy"]]) == 2)
        # Real work keeps one replica occupied. No forged load gauge is used.
        with ThreadPoolExecutor(max_workers=1) as pool:
            slow = pool.submit(
                client.call, "lab4-fixture-service", "/api/v1/work?delay=7"
            )
            busy = eventually(
                lambda: next(
                    (
                        stats(p)["instance_id"]
                        for p in (28602, 28603)
                        if stats(p)["in_flight"]
                    ),
                    None,
                )
            )
            selected = [
                client.call("lab4-fixture-service", "/api/v1/work")[0]["instance_id"]
                for _ in range(4)
            ]
            assert all(item != busy for item in selected), "Busy worker was selected"
            slow.result()
        print("PASS: real concurrent work routes to the free sibling", flush=True)

        http(
            "http://localhost:28602",
            "/control",
            method="POST",
            body={"fail": True},
            headers={"Authorization": "Bearer " + client.env["BROKER_ADMIN_TOKEN"]},
        )
        # Each failed physical attempt is reported once by Broker. Successful
        # retries on B remain visible to the caller; probes on A remain healthy.
        for _ in range(12):
            client.call("lab4-fixture-service", "/api/v1/work")
            failures = stats(28602)["failures"]
            a = next(i for i in replicas() if i["instance_id"] == identity + "-a")
            if failures < 3:
                assert not a["removed"], (
                    "Instance removed before three physical failures"
                )
            if a["removed"]:
                assert failures == 3, (
                    "Failure threshold did not isolate exactly three attempts"
                )
                break
        else:
            raise AssertionError("Third physical failure did not remove the replica")
        before = stats(28602)["calls"]
        for _ in range(4):
            assert (
                client.call("lab4-fixture-service", "/api/v1/work")[0]["instance_id"]
                == identity + "-b"
            )
        assert stats(28602)["calls"] == before
        http(
            "http://localhost:28602",
            "/control",
            method="POST",
            body={"fail": False},
            headers={"Authorization": "Bearer " + client.env["BROKER_ADMIN_TOKEN"]},
        )
        time.sleep(
            4
        )  # At least one real registration refresh; it cannot restore removal.
        assert next(i for i in replicas() if i["instance_id"] == identity + "-a")[
            "removed"
        ]
        client.ops("/v1/instances/" + identity + "-a/restore", method="POST")
        eventually(
            lambda: (
                not next(i for i in replicas() if i["instance_id"] == identity + "-a")[
                    "removed"
                ]
            )
        )
        print(
            "PASS: third distinct failure removes A; B serves; heartbeat cannot restore; operator restore works",
            flush=True,
        )

        event_id = str(uuid.uuid4())
        # A valid producer/envelope with invalid domain payload exercises a real
        # consumer rejection. No failure switch is added to an application API.
        receipt = client.publish(
            {
                "event_id": event_id,
                "type": "PlayerRegistered",
                "version": 1,
                "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "producer": "player-service",
                "payload": {},
            }
        )
        dead = eventually(
            lambda: next(
                (
                    d
                    for d in client.ops("/v1/dead-letters")[0]["dead_letters"]
                    if d["kind"] == "event"
                    and d["message_id"] == receipt.message_id
                    and d["reason"] == "consumer_rejected"
                ),
                None,
            )
        )
        client.ops("/v1/dead-letters/" + dead["id"] + "/replay", method="POST")
        eventually(
            lambda: next(
                (
                    d
                    for d in client.ops("/v1/dead-letters")[0]["dead_letters"]
                    if d["id"] == dead["id"]
                ),
                None,
            )
        )
        print(
            "PASS: poison event reaches a durable dead letter; authenticated replay is available",
            flush=True,
        )

        topics = client.ops("/v1/topics")[0]["topics"]
        assert len([t for t in topics if t["name"].startswith("commands.")]) >= 8
        archive, _ = client.ops("/v1/logs/download")
        with zipfile.ZipFile(io.BytesIO(archive)) as logs:
            names = logs.namelist()
            assert len(names) >= 19, ("Missing system log files", names)
            assert all(
                name.endswith(".log") and ".env" not in name and ".." not in name
                for name in names
            )
        http(client.operations, "/v1/logs/download", expected=401)
        for path in ("/api/v1/pools", "/api/v1/worlds", "/world-service/api/v1/status"):
            http(
                client.gateway,
                path,
                method="POST" if path != "/world-service/api/v1/status" else "GET",
                body={} if path != "/world-service/api/v1/status" else None,
                expected=404,
            )
        prom, _ = http(
            "http://localhost:29090",
            "/api/v1/query?query=up%7Bjob%3D%22message-broker%22%7D",
        )
        assert prom["data"]["result"] and prom["data"]["result"][0]["value"][1] == "1"
        http("http://localhost:23000", "/api/health")
        print(
            "PASS: topic listing, authenticated system log ZIP, removed internal Gateway routes, Prometheus and Grafana",
            flush=True,
        )
    finally:
        subprocess.run(
            ["docker", "stop", "-t", "5", container], capture_output=True, check=False
        )
        client.close()


if __name__ == "__main__":
    main()
