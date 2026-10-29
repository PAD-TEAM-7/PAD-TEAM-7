"""Live Lab 3 checks for Maxim's Zombie and Resource services; no third-party dependencies."""

import argparse
import base64
import hashlib
import hmac
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from importlib.util import module_from_spec, spec_from_file_location

spec = spec_from_file_location("lab3_common", Path(__file__).with_name("lab3-smoke.py"))
common = module_from_spec(spec)
spec.loader.exec_module(common)
request = common.request


def service_token(secret, audience):
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=")
    header = encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    claims = encode(
        json.dumps(
            {
                "sub": "maxim-lab3-demo",
                "roles": ["service"],
                "aud": audience,
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
            }
        ).encode()
    )
    body = header + b"." + claims
    return (
        body + b"." + encode(hmac.new(secret.encode(), body, hashlib.sha256).digest())
    ).decode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="http://localhost:18080")
    parser.add_argument("--discovery", default="http://localhost:18500")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "deploy/.env",
    )
    parser.add_argument("--fault-demo", action="store_true")
    args = parser.parse_args()
    env = dict(
        line.split("=", 1)
        for line in args.env_file.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    admin = {"Authorization": "Bearer " + env["DISCOVERY_TOKEN"].strip()}
    api = args.gateway
    for service in ("zombie-service", "resource-service"):
        state, _ = request(args.discovery, "/v1/instances", headers=admin)
        active = [
            i
            for i in state["instances"]
            if i["service"] == service
            and i["healthy"]
            and i["lease_valid"]
            and not i["removed"]
        ]
        assert len(active) == 2, f"Need two healthy {service} replicas"
        sequence = [
            request(api, f"/{service}/api/v1/status")[1]["X-Service-Instance"]
            for _ in range(4)
        ]
        assert (
            sequence[0] == sequence[2]
            and sequence[1] == sequence[3]
            and sequence[0] != sequence[1]
        )
        print("PASS:", service, "registered and round robin", " -> ".join(sequence))

    def auth(service, key=None):
        headers = {
            "Authorization": "Bearer "
            + service_token(env["SERVICE_JWT_SECRET"], service)
        }
        if key:
            headers["Idempotency-Key"] = key
        return headers

    suffix = uuid.uuid4().hex[:12]
    account = {
        "username": "maxim_" + suffix,
        "email": suffix + "@maxim.test",
        "password": "maxim-lab3-password",
    }
    player, _ = request(api, "/api/v1/players/register", "POST", account, expected=201)
    tokens, _ = request(
        api,
        "/api/v1/players/login",
        "POST",
        {k: account[k] for k in ("email", "password")},
    )
    player_auth = {"Authorization": "Bearer " + tokens["access_token"]}
    lobby = str(uuid.uuid4())
    pool_key = str(uuid.uuid4())
    pool_input = {
        "lobby_id": lobby,
        "room_id": "lab",
        "node_id": str(uuid.uuid4()),
        "kind": "node",
        "initial_stock": [{"item_id": "wood-01", "count": 10}],
    }
    pool, first = request(
        api,
        "/api/v1/pools",
        "POST",
        pool_input,
        auth("resource-service", pool_key),
        201,
    )
    replay, second = request(
        api,
        "/api/v1/pools",
        "POST",
        pool_input,
        auth("resource-service", pool_key),
        200,
    )
    assert (
        pool["pool_id"] == replay["pool_id"]
        and first["X-Service-Instance"] != second["X-Service-Instance"]
    )
    gather_input = {
        "lobby_id": lobby,
        "player_id": player["player_id"],
        "source_pool_id": pool["pool_id"],
        "items": [{"item_id": "wood-01", "count": 3}],
        "reason": "lab3",
        "action_id": str(uuid.uuid4()),
    }
    gather_key = str(uuid.uuid4())
    gathered, first = request(
        api,
        "/api/v1/transactions/gather",
        "POST",
        gather_input,
        auth("resource-service", gather_key),
    )
    replayed, second = request(
        api,
        "/api/v1/transactions/gather",
        "POST",
        gather_input,
        auth("resource-service", gather_key),
    )
    assert (
        gathered["transaction_id"] == replayed["transaction_id"]
        and replayed["replayed"]
    )
    assert first["X-Service-Instance"] != second["X-Service-Instance"]
    stock, _ = request(
        api, "/api/v1/pools/" + pool["pool_id"], headers=auth("resource-service")
    )
    assert stock["stock"][0]["count"] == 7
    print(
        "PASS: Resource pool/gather replay on different replicas changes stock only once"
    )

    spawn_key = str(uuid.uuid4())
    zombie, _ = request(
        api,
        "/api/v1/zombies",
        "POST",
        {"type_id": "tourist_zombie", "lobby_id": lobby, "room_id": "lab"},
        auth("zombie-service", spawn_key),
        201,
    )
    path = "/api/v1/zombies/" + zombie["zombie_id"]
    steal_key = str(uuid.uuid4())
    steal = {
        "victim_type": "node",
        "victim_id": pool["pool_id"],
        "items": [{"item_id": "wood-01", "count": 2}],
        "xp": 0,
    }
    stolen, first = request(
        api, path + "/steal", "POST", steal, auth("zombie-service", steal_key)
    )
    replay, second = request(
        api, path + "/steal", "POST", steal, auth("zombie-service", steal_key)
    )
    assert stolen["inventory"] == replay["inventory"] and replay["replayed"]
    assert first["X-Service-Instance"] != second["X-Service-Instance"]
    stock, _ = request(
        api, "/api/v1/pools/" + pool["pool_id"], headers=auth("resource-service")
    )
    assert stock["stock"][0]["count"] == 5
    request(api, path, "DELETE", headers=auth("zombie-service"))
    stock, _ = request(
        api, "/api/v1/pools/" + pool["pool_id"], headers=auth("resource-service")
    )
    assert stock["stock"][0]["count"] == 7
    print("PASS: Zombie theft replay and despawn return use the real Resource replicas")

    zombie, _ = request(
        api,
        "/api/v1/zombies",
        "POST",
        {"type_id": "tourist_zombie", "lobby_id": lobby, "room_id": "lab"},
        auth("zombie-service", str(uuid.uuid4())),
        201,
    )
    path = "/api/v1/zombies/" + zombie["zombie_id"]
    request(
        api,
        path + "/damage",
        "POST",
        {
            "amount": 70,
            "source_player_id": player["player_id"],
            "weapon_item_id": "axe-01",
        },
        auth("zombie-service", str(uuid.uuid4())),
    )

    def kill(_):
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            api + path + "/kill",
            method="POST",
            data=json.dumps(
                {"killer_player_id": player["player_id"], "lobby_id": lobby}
            ).encode(),
            headers={
                "Content-Type": "application/json",
                **auth("zombie-service", str(uuid.uuid4())),
            },
        )
        try:
            res = urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as error:
            res = error
        res.read()
        return res.status, res.headers["X-Service-Instance"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(kill, range(2)))
    assert sorted(code for code, _ in outcomes) == [200, 422], outcomes
    assert len({instance for _, instance in outcomes}) == 2, outcomes
    profile, _ = request(
        api, "/api/v1/players/" + player["player_id"], headers=player_auth
    )
    inventory, _ = request(
        api,
        "/api/v1/players/" + player["player_id"] + "/inventory",
        headers=player_auth,
    )
    assert profile["xp"] == 40, profile
    assert (
        sum(
            item["count"]
            for item in inventory["items"]
            if item["item_id"] == "energy-01"
        )
        == 1
    )
    print(
        "PASS: concurrent kills on different Zombie replicas award real Player loot and XP once"
    )

    for service in ("zombie-service", "resource-service"):
        state, _ = request(args.discovery, "/v1/instances", headers=admin)
        victim = next(
            i["instance_id"]
            for i in state["instances"]
            if i["service"] == service
            and i["healthy"]
            and i["lease_valid"]
            and not i["removed"]
        )
        try:
            for index in range(3):
                removed, _ = request(
                    args.discovery,
                    f"/v1/instances/{victim}/failures",
                    "POST",
                    {
                        "event_id": str(uuid.uuid4()),
                        "reason": "Maxim smoke controlled failure",
                    },
                    admin,
                )
                assert removed["removed"] == (index == 2)
            for _ in range(4):
                assert (
                    request(api, f"/{service}/api/v1/status")[1]["X-Service-Instance"]
                    != victim
                )
            time.sleep(6)
            state, _ = request(args.discovery, "/v1/instances", headers=admin)
            assert next(i for i in state["instances"] if i["instance_id"] == victim)[
                "removed"
            ]
        finally:
            request(
                args.discovery, f"/v1/instances/{victim}/restore", "POST", headers=admin
            )
        print(
            "PASS:",
            service,
            "removed after three errors, excluded across heartbeat, explicitly restored",
        )
        if args.fault_demo:
            common.fault_demo(args, admin, service)
    print("All Maxim Lab 3 checks passed.")


if __name__ == "__main__":
    main()
