"""Run against the Lab 3 stack. Creates a fresh demo player/lobby; prints no credentials."""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def request(base, path, method="GET", data=None, headers=None, expected=200):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        base + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    raw = response.read()
    assert response.status == expected, (
        f"{method} {path}: expected {expected}, got {response.status}: {raw[:300]!r}"
    )
    return (json.loads(raw) if raw else None), response.headers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="http://localhost:18080")
    parser.add_argument("--discovery", default="http://localhost:18500")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "deploy/.env",
    )
    parser.add_argument(
        "--fault-demo",
        action="store_true",
        help="Also inject a temporary failing replica and critical-load alert",
    )
    args = parser.parse_args()
    env = dict(
        line.split("=", 1)
        for line in args.env_file.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    admin = {"Authorization": "Bearer " + env["DISCOVERY_TOKEN"].strip()}
    instances, _ = request(args.discovery, "/v1/instances", headers=admin)
    active = [
        i
        for i in instances["instances"]
        if i["healthy"] and i["lease_valid"] and not i["removed"]
    ]
    assert sum(i["service"] == "player-service" for i in active) >= 2, (
        "Need two healthy Player replicas"
    )
    assert sum(i["service"] == "game-service" for i in active) >= 2, (
        "Need two healthy Game replicas"
    )
    print("PASS: two healthy replicas of Player and Game registered")

    sequence = []
    for _ in range(4):
        _, headers = request(args.gateway, "/player-service/api/v1/status")
        sequence.append(headers["X-Service-Instance"])
    assert (
        sequence[0] == sequence[2]
        and sequence[1] == sequence[3]
        and sequence[0] != sequence[1]
    ), sequence
    print("PASS: round robin", " -> ".join(sequence))

    suffix = uuid.uuid4().hex[:12]
    account = {
        "username": "demo_" + suffix,
        "email": suffix + "@lab.test",
        "password": "lab3-demo-password-123",
    }
    player, register_headers = request(
        args.gateway, "/api/v1/players/register", "POST", account, expected=201
    )
    credentials = {"email": account["email"], "password": account["password"]}
    tokens, login_headers = request(
        args.gateway, "/api/v1/players/login", "POST", credentials
    )
    assert register_headers["X-Service-Instance"] != login_headers["X-Service-Instance"]
    auth = {"Authorization": "Bearer " + tokens["access_token"]}
    request(args.gateway, "/api/v1/players/" + player["player_id"], headers=auth)
    rotated, _ = request(
        args.gateway,
        "/api/v1/players/refresh",
        "POST",
        {"refresh_token": tokens["refresh_token"]},
    )
    request(
        args.gateway,
        "/api/v1/players/refresh",
        "POST",
        {"refresh_token": tokens["refresh_token"]},
        expected=401,
    )
    print("PASS: cross-replica login, shared signing key and one-time refresh rotation")

    lobby, _ = request(
        args.gateway,
        "/api/v1/lobbies",
        "POST",
        {"name": "Lab 3 demo", "university": "FAF", "max_players": 8},
        {**auth, "Idempotency-Key": str(uuid.uuid4())},
        expected=201,
    )
    lobby_path = "/api/v1/lobbies/" + lobby["lobby_id"]
    owners = []
    for _ in range(4):
        _, headers = request(args.gateway, lobby_path, headers=auth)
        owners.append(headers["X-Service-Instance"])
    assert len(set(owners)) == 1, owners
    ws, _ = request(args.gateway, "/ws/v1/lobbies/" + lobby["lobby_id"], headers=auth)
    expected = next(i["ws_url"] for i in active if i["instance_id"] == owners[0])
    assert ws["url"].startswith(expected + "/")
    print("PASS: lobby affinity and direct WebSocket URL agree:", owners[0])

    # Exercise real registry removal and restore without destroying data or stopping containers.
    victim = sequence[0]
    try:
        for i in range(3):
            result, _ = request(
                args.discovery,
                f"/v1/instances/{victim}/failures",
                "POST",
                {
                    "event_id": str(uuid.uuid4()),
                    "reason": "lab3 smoke controlled fault",
                },
                admin,
            )
            assert result["removed"] == (i == 2)
        for _ in range(4):
            _, headers = request(args.gateway, "/player-service/api/v1/health")
            assert headers["X-Service-Instance"] != victim
        time.sleep(6)  # heartbeat must not undo the circuit removal
        state, _ = request(args.discovery, "/v1/instances", headers=admin)
        assert next(i for i in state["instances"] if i["instance_id"] == victim)[
            "removed"
        ]
        print("PASS: three failures remove the instance; heartbeats cannot restore it")
    finally:
        request(
            args.discovery, f"/v1/instances/{victim}/restore", "POST", headers=admin
        )
    print("PASS: explicit health-checked restoration")
    if args.fault_demo:
        fault_demo(args, admin)
    print(
        "All Lab 3 smoke checks passed. SMTP provider delivery requires configured credentials."
    )


def fault_demo(args, admin):
    class FaultyReplica(BaseHTTPRequestHandler):
        def do_GET(self):
            status = self.path == "/api/v1/status"
            self.send_response(200 if status else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"status": "ok", "load": 0.95}
                    if status
                    else {"error": "controlled demo fault"}
                ).encode()
            )

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 19090), FaultyReplica)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    instance = "demo-fault-" + uuid.uuid4().hex[:8]
    registration = {
        "service": "player-service",
        "instance_id": instance,
        "url": "http://host.docker.internal:19090",
        "task_timeout": 15,
    }
    try:
        request(args.discovery, "/v1/instances", "POST", registration, admin)
        for _ in range(15):
            state, _ = request(args.discovery, "/v1/instances", headers=admin)
            if next(i for i in state["instances"] if i["instance_id"] == instance)[
                "healthy"
            ]:
                break
            time.sleep(1)
        else:
            raise AssertionError(
                "Docker cannot reach the local fault fixture on port 19090"
            )
        failures = 0
        paths = (
            "/player-service/api/v1/health",
            "/player-service/api/v1/ready",
            "/api/v1/auth/jwks",
        )
        for index in range(15):
            request(args.discovery, "/v1/instances", "POST", registration, admin)
            path = paths[failures % len(paths)]
            req = urllib.request.Request(args.gateway + path)
            try:
                response = urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as error:
                response = error
            response.read()
            if response.headers.get("X-Service-Instance") == instance:
                assert response.status == 500
                failures += 1
            state, _ = request(args.discovery, "/v1/instances", headers=admin)
            if next(i for i in state["instances"] if i["instance_id"] == instance)[
                "removed"
            ]:
                break
        assert failures == 3, (
            f"Expected three actual HTTP failures, observed {failures}"
        )
        for _ in range(6):
            _, headers = request(args.gateway, "/player-service/api/v1/health")
            assert headers["X-Service-Instance"] != instance
        print(
            "PASS: three real HTTP 500s across different endpoints removed the faulty replica"
        )
        print(
            "Critical-load alert generated; inspect Discovery logs and your configured SMTP inbox."
        )
    finally:
        request(args.discovery, f"/v1/instances/{instance}", "DELETE", headers=admin)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
