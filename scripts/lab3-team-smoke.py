"""Real full-team Lab 3 checks. Creates isolated demo records; never prints credentials."""

import argparse
import json
import runpy
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
common = runpy.run_path(str(Path(__file__).with_name("lab3-maxim-smoke.py")))
request, service_token = common["request"], common["service_token"]
SERVICES = ("exam-service", "world-service", "base-service", "crafting-service")


def eventually(check, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(1)
    raise AssertionError("Timed out waiting for the expected integration state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://localhost:18080")
    parser.add_argument("--discovery", default="http://localhost:18500")
    parser.add_argument("--env-file", type=Path, default=ROOT / "deploy/.env")
    parser.add_argument("--scale-demo", action="store_true")
    parser.add_argument("--fault-demo", action="store_true", help="Inject test replicas; sends configured alert emails")
    parser.add_argument("--skip-game-demo", action="store_true", help="Validate an older partial deployment without the real Game scenario")
    args = parser.parse_args()
    env = dict(line.split("=", 1) for line in args.env_file.read_text().splitlines()
               if "=" in line and not line.lstrip().startswith("#"))
    admin = {"Authorization": "Bearer " + env["DISCOVERY_TOKEN"].strip()}
    api = args.gateway

    def auth(service, key=None):
        headers = {"Authorization": "Bearer " + service_token(env["SERVICE_JWT_SECRET"], service)}
        if key:
            headers["Idempotency-Key"] = key
        return headers

    def active(service):
        state, _ = request(args.discovery, "/v1/instances", headers=admin)
        return [i for i in state["instances"] if i["service"] == service
                and i["healthy"] and i["lease_valid"] and not i["removed"]]

    for service in SERVICES:
        eventually(lambda: len(active(service)) == 2)
        sequence = []
        for _ in range(4):
            status, headers = request(api, f"/{service}/api/v1/status")
            assert status["status"] == "ok" and isinstance(status["load"], (int, float))
            sequence.append(headers["X-Service-Instance"])
        assert sequence[0] == sequence[2] and sequence[1] == sequence[3] and sequence[0] != sequence[1]
        print("PASS:", service, "two healthy replicas and round robin", flush=True)

    suffix = uuid.uuid4().hex[:12]
    account = {"username": "team_" + suffix, "email": suffix + "@lab3.test", "password": "lab3-demo-password"}
    player, _ = request(api, "/api/v1/players/register", "POST", account, expected=201)
    tokens, _ = request(api, "/api/v1/players/login", "POST", {k: account[k] for k in ("email", "password")})
    player_headers = {"Authorization": "Bearer " + tokens["access_token"]}
    player_id = player["player_id"]
    lobby = str(uuid.uuid4())

    def player_auth(key=None):
        return {**player_headers, **({"Idempotency-Key": key} if key else {})}

    def event(kind, payload):
        return {"event_id": str(uuid.uuid4()), "type": kind, "version": 1,
                "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "producer": "lab3-team-verification", "payload": payload}

    registered = event("PlayerRegistered", {"player_id": player_id})
    request(api, "/exam-service/api/v1/events", "POST", registered, auth("exam-service", registered["event_id"]), expected=202)
    if not args.skip_game_demo:
        game_demo(api, player_id, player_auth, auth)
    world_key = str(uuid.uuid4())
    world_body = {"lobby_id": lobby, "university": "UTM", "seed": suffix}
    world, first = request(api, "/world-service/api/v1/worlds", "POST", world_body, auth("world-service", world_key), 201)
    replay, second = request(api, "/world-service/api/v1/worlds", "POST", world_body, auth("world-service", world_key), 201)
    assert world == replay and first["X-Service-Instance"] != second["X-Service-Instance"]
    eventually(lambda: request(api, f"/resource-service/api/v1/pools?lobby_id={lobby}", headers=auth("resource-service"))[0]["items"])
    print("PASS: World cross-replica replay and real Resource pool delivery", flush=True)

    base_key = str(uuid.uuid4())
    base_body = {"lobby_id": lobby, "home_room_id": world["home_room_id"]}
    base, first = request(api, "/base-service/api/v1/bases", "POST", base_body, auth("base-service", base_key), 201)
    replay, second = request(api, "/base-service/api/v1/bases", "POST", base_body, auth("base-service", base_key), 201)
    assert base["base_id"] == replay["base_id"] and first["X-Service-Instance"] != second["X-Service-Instance"]

    pool, _ = request(api, "/resource-service/api/v1/pools", "POST", {
        "lobby_id": lobby, "room_id": world["home_room_id"], "node_id": str(uuid.uuid4()),
        "kind": "node", "initial_stock": [{"item_id": "wood-01", "count": 50}, {"item_id": "metal-01", "count": 20}],
    }, auth("resource-service", str(uuid.uuid4())), 201)
    request(api, "/resource-service/api/v1/transactions/gather", "POST", {
        "lobby_id": lobby, "player_id": player_id, "source_pool_id": pool["pool_id"],
        "action_id": str(uuid.uuid4()), "reason": "lab3-team-fixture",
        "items": [{"item_id": "wood-01", "count": 30}, {"item_id": "metal-01", "count": 10}],
    }, auth("resource-service", str(uuid.uuid4())))
    barricade_body = {"player_id": player_id, "room_id": world["home_room_id"], "material": "wood"}
    barricade_key = str(uuid.uuid4())
    path = f"/base-service/api/v1/bases/{base['base_id']}/barricades"
    barricade, _ = request(api, path, "POST", barricade_body, player_auth(barricade_key), 201)
    replay, _ = request(api, path, "POST", barricade_body, player_auth(barricade_key), 201)
    assert barricade == replay
    print("PASS: Base replay, real World validation and Resource consumption", flush=True)

    preview, _ = request(api, "/crafting-service/api/v1/recipes/recipe-barricade-kit/preview", "POST",
                         {"player_id": player_id, "lobby_id": lobby}, player_auth(str(uuid.uuid4())))
    assert preview["materials_verified"] and preview["craftable"], preview
    craft_key = str(uuid.uuid4())
    craft_body = {"player_id": player_id, "lobby_id": lobby, "recipe_id": "recipe-barricade-kit"}
    crafted, _ = request(api, "/crafting-service/api/v1/crafts", "POST", craft_body, player_auth(craft_key), 201)
    replay, _ = request(api, "/crafting-service/api/v1/crafts", "POST", craft_body, player_auth(craft_key))
    assert replay.pop("replayed") and crafted == replay
    inventory, _ = request(api, f"/player-service/api/v1/players/{player_id}/inventory", headers=player_auth())
    counts = {item["item_id"]: item["count"] for item in inventory["items"]}
    assert counts["barricade-kit-01"] == 1
    balances, _ = request(api, f"/resource-service/api/v1/players/{player_id}/resources?lobby_id={lobby}", headers=player_auth())
    amounts = {item["item_id"]: item["amount"] for item in balances["resources"]}
    assert amounts["wood-01"] == 16 and amounts["metal-01"] == 8
    print("PASS: Crafting preview, real Resource debit, exactly one Player reward", flush=True)

    exam_key = str(uuid.uuid4())
    exam_body = {"player_id": player_id, "lobby_id": lobby, "encounter_id": str(uuid.uuid4())}
    exam, first = request(api, "/exam-service/api/v1/exams", "POST", exam_body, auth("exam-service", exam_key), 201)
    replay, second = request(api, "/exam-service/api/v1/exams", "POST", exam_body, auth("exam-service", exam_key), 201)
    assert exam == replay and first["X-Service-Instance"] != second["X-Service-Instance"]
    exam_id = str(uuid.UUID(exam["exam_id"]))
    # Test fixture only: answers stay inside this process, never exposed by an API or printed.
    sql = ("SELECT json_agg(json_build_object('question_id',q.question_id,'option_index',q.correct_option)) "
           "FROM exam_questions e JOIN questions q USING(question_id) WHERE e.exam_id='" + exam_id + "'")
    answers = json.loads(subprocess.check_output([
        "docker", "compose", "-f", "deploy/docker-compose.lab3.yml", "exec", "-T", "exam-db",
        "psql", "-U", "exam", "-d", "exam_db", "-At", "-c", sql,
    ], cwd=ROOT, text=True))
    for answer in answers:
        request(api, f"/exam-service/api/v1/exams/{exam_id}/answers", "POST", answer, player_auth())
    submit_key = str(uuid.uuid4())
    result, _ = request(api, f"/exam-service/api/v1/exams/{exam_id}/submit", "POST", {}, player_auth(submit_key))
    replay, _ = request(api, f"/exam-service/api/v1/exams/{exam_id}/submit", "POST", {}, player_auth(submit_key))
    assert result == replay and result["grade"] == 10 and result["xp_awarded"] > 0
    eventually(lambda: request(api, f"/player-service/api/v1/players/{player_id}", headers=player_auth())[0].get("xp", 0) >= result["xp_awarded"])
    print("PASS: Exam replay, answer/submit and real asynchronous Player XP", flush=True)

    if args.scale_demo:
        for service in SERVICES:
            try:
                subprocess.run(["docker", "compose", "-f", "deploy/docker-compose.lab3.yml", "up", "-d", "--no-deps", "--pull", "never", "--scale", service + "=3", service], cwd=ROOT, check=True, capture_output=True)
                eventually(lambda: len(active(service)) == 3)
                selected = {request(api, f"/{service}/api/v1/status")[1]["X-Service-Instance"] for _ in range(6)}
                assert len(selected) == 3
            finally:
                subprocess.run(["docker", "compose", "-f", "deploy/docker-compose.lab3.yml", "up", "-d", "--no-deps", "--pull", "never", "--scale", service + "=2", service], cwd=ROOT, check=True, capture_output=True)
            eventually(lambda: len(active(service)) == 2)
            print("PASS:", service, "dynamic scaling 2 -> 3 -> 2", flush=True)

    if args.fault_demo:
        faults(args, admin, auth, active)


def game_demo(api, player_id, player_auth, service_auth):
    """Exercise Game's real World/Base/Resource/Exam/Zombie calls through Gateway."""
    key = str(uuid.uuid4())
    body = {"name": "full-game-" + key[:8], "university": "FAF", "max_players": 8}
    lobby, first = request(api, "/game-service/api/v1/lobbies", "POST", body, player_auth(key), 201)
    lobby_id = lobby["lobby_id"]
    # Advance the stateless cursor when necessary, while using the same real client request.
    for _ in range(4):
        replay, second = request(api, "/game-service/api/v1/lobbies", "POST", body, player_auth(key), 201)
        assert replay["lobby_id"] == lobby_id and replay["base_id"] == lobby["base_id"]
        if first["X-Service-Instance"] != second["X-Service-Instance"]:
            break
    else:
        raise AssertionError("Lobby replay was not exercised on a second Game replica")
    world, _ = request(api, f"/world-service/api/v1/worlds/{lobby_id}", headers=player_auth())
    base, _ = request(api, f"/base-service/api/v1/bases/{lobby['base_id']}", headers=player_auth())
    assert base["home_room_id"] == world["home_room_id"]
    print("PASS: Game lobby replay across replicas prepares one real World/Base with the correct home room", flush=True)

    def ready_node():
        nodes, _ = request(api, f"/world-service/api/v1/worlds/{lobby_id}/nodes", headers=service_auth("world-service"))
        return next((n for n in nodes["items"] if n["item_id"] == "wood-01" and n["pool_id"]), None)

    node = eventually(ready_node)

    def action(kind, node_id=""):
        action_key = str(uuid.uuid4())
        body = {"action_type": kind, "target_room_id": node["room_id"], "target_node_id": node_id}
        started, _ = request(api, f"/game-service/api/v1/lobbies/{lobby_id}/actions", "POST", body, player_auth(action_key), 202)
        replay, _ = request(api, f"/game-service/api/v1/lobbies/{lobby_id}/actions", "POST", body, player_auth(action_key), 202)
        assert started["action_id"] == replay["action_id"]

        def complete():
            result, _ = request(api, "/game-service/api/v1/actions/" + started["action_id"], headers=player_auth())
            return result if result["status"] == "completed" else None

        return eventually(complete)

    result = action("chop_bench", node["node_id"])
    assert result["reward"]["items"] == [{"item_id": "wood-01", "count": 1}]
    balances, _ = request(api, f"/resource-service/api/v1/players/{player_id}/resources?lobby_id={lobby_id}", headers=player_auth())
    assert next(v["amount"] for v in balances["resources"] if v["item_id"] == "wood-01") == 1
    action("clear_room")
    room, _ = request(api, "/world-service/api/v1/rooms/" + node["room_id"], headers=player_auth())
    assert room["safe"]
    print("PASS: Game completes a real node gather once and clears the actual World room", flush=True)

    pool, _ = request(api, "/resource-service/api/v1/pools", "POST", {
        "lobby_id": lobby_id, "room_id": node["room_id"], "node_id": str(uuid.uuid4()), "kind": "node",
        "initial_stock": [{"item_id": "wood-01", "count": 30}],
    }, service_auth("resource-service", str(uuid.uuid4())), 201)
    request(api, "/resource-service/api/v1/transactions/gather", "POST", {
        "lobby_id": lobby_id, "player_id": player_id, "source_pool_id": pool["pool_id"],
        "action_id": str(uuid.uuid4()), "reason": "game-demo-fixture", "items": [{"item_id": "wood-01", "count": 30}],
    }, service_auth("resource-service", str(uuid.uuid4())))
    built = action("barricade_room")
    barricade_id = built["reward"]["barricade_id"]
    request(api, "/base-service/api/v1/barricades/" + barricade_id, "PATCH",
            {"operation": "damage", "amount": 5}, service_auth("base-service", str(uuid.uuid4())))
    repaired = action("repair_base")
    assert repaired["reward"]["health"] == repaired["reward"]["strength"]
    print("PASS: Game builds and repairs a real Base barricade using real Resource balances", flush=True)

    cycle_key = str(uuid.uuid4())
    path = f"/game-service/api/v1/lobbies/{lobby_id}/cycle"
    night, _ = request(api, path, "POST", {}, service_auth("game-service", cycle_key))
    replay, _ = request(api, path, "POST", {}, service_auth("game-service", cycle_key))
    assert night["phase"] == replay["phase"] == "night" and night["day"] == replay["day"] == 1
    day, _ = request(api, path, "POST", {}, service_auth("game-service", str(uuid.uuid4())))
    assert day["phase"] == "day" and day["day"] == 2
    print("PASS: Game sends valid World/Zombie cycle events; replay never advances the cycle twice", flush=True)

    zombie, _ = request(api, "/zombie-service/api/v1/zombies", "POST",
                        {"type_id": "professor_zombie", "lobby_id": lobby_id, "room_id": node["room_id"]},
                        service_auth("zombie-service", str(uuid.uuid4())), 201)
    encounter_key = str(uuid.uuid4())
    encounter_body = {"player_id": player_id, "zombie_id": zombie["zombie_id"], "zombie_type": "professor_zombie"}
    path = f"/game-service/api/v1/lobbies/{lobby_id}/encounters"
    encounter, _ = request(api, path, "POST", encounter_body, service_auth("game-service", encounter_key), 201)
    replay, _ = request(api, path, "POST", encounter_body, service_auth("game-service", encounter_key), 201)
    assert encounter == replay and encounter["outcome"] == "exam_started"
    exam, _ = request(api, "/exam-service/api/v1/exams/" + encounter["exam_id"], headers=player_auth())
    assert len(exam["questions"]) == 10
    # Answers are test setup inside the fixture DB, never exposed by an application API.
    exam_id = str(uuid.UUID(encounter["exam_id"]))
    sql = ("SELECT json_agg(json_build_object('question_id',q.question_id,'option_index',q.correct_option)) "
           "FROM exam_questions e JOIN questions q USING(question_id) WHERE e.exam_id='" + exam_id + "'")
    answers = json.loads(subprocess.check_output([
        "docker", "compose", "-f", "deploy/docker-compose.lab3.yml", "exec", "-T", "exam-db",
        "psql", "-U", "exam", "-d", "exam_db", "-At", "-c", sql,
    ], cwd=ROOT, text=True))
    for answer in answers:
        request(api, f"/exam-service/api/v1/exams/{exam_id}/answers", "POST", answer, player_auth())
    submit_key = str(uuid.uuid4())
    result, _ = request(api, f"/exam-service/api/v1/exams/{exam_id}/submit", "POST", {}, player_auth(submit_key))
    replay_result, _ = request(api, f"/exam-service/api/v1/exams/{exam_id}/submit", "POST", {}, player_auth(submit_key))
    assert result == replay_result and result["grade"] == 10

    def rewarded():
        inventory, _ = request(api, f"/player-service/api/v1/players/{player_id}/inventory", headers=player_auth())
        counts = {item["item_id"]: item["count"] for item in inventory["items"]}
        return counts.get("coffee-01") == 2 and counts.get("cheatsheet-01") == 1

    eventually(rewarded)
    print("PASS: Exam first-pass/perfect-score rewards reach Player inventory exactly once on submit replay", flush=True)

    def unlocked():
        wings, _ = request(api, f"/world-service/api/v1/worlds/{lobby_id}/wings", headers=player_auth())
        return sum(bool(w["unlocked"]) for w in wings["items"]) >= 2

    # Some courses intentionally have no World wing. Exercise a real supported unlock
    # command as a controlled fixture in that case; never claim every exam opens a wing.
    wings, _ = request(api, f"/world-service/api/v1/worlds/{lobby_id}/wings", headers=player_auth())
    if not any(w.get("unlock_course_id") == exam.get("course_id") for w in world["locked_wings"]):
        request(api, f"/world-service/api/v1/worlds/{lobby_id}/unlock", "POST",
                {"course_id": "math-101", "player_id": player_id}, service_auth("world-service", str(uuid.uuid4())))
    eventually(unlocked)

    def callback_received():
        query = "SELECT count(*) FROM received_events WHERE fingerprint LIKE '%" + lobby_id + "%'"
        count = subprocess.check_output([
            "docker", "compose", "-f", "deploy/docker-compose.lab3.yml", "exec", "-T", "game-db",
            "psql", "-U", "game", "-d", "game_db", "-At", "-c", query,
        ], cwd=ROOT, text=True).strip()
        return int(count) == 1

    eventually(callback_received)
    print("PASS: Game professor exam replay/pass, supported World unlock and authenticated Game callback", flush=True)


def faults(args, admin, auth, active):
    for offset, service in enumerate(SERVICES):
        identity = "lab3-test-" + uuid.uuid4().hex

        class Fixture(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == "/api/v1/status" else 500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "load": .95, "fixture": True}).encode())

            def log_message(self, *unused):
                pass

        server = ThreadingHTTPServer(("0.0.0.0", 19090 + offset), Fixture)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        registration = {"service": service, "instance_id": identity, "url": f"http://host.docker.internal:{19090 + offset}", "task_timeout": 10}
        try:
            request(args.discovery, "/v1/instances", "POST", registration, admin)
            eventually(lambda: any(i["instance_id"] == identity for i in active(service)))
            failures = 0
            for _ in range(18):
                # Actual Gateway-selected requests, not three manually forged failure reports.
                import urllib.request
                import urllib.error
                req = urllib.request.Request(args.gateway + f"/{service}/api/v1/lab3-failure-probe", headers=auth(service))
                try:
                    response = urllib.request.urlopen(req, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                response.read()
                if response.headers.get("X-Service-Instance") == identity:
                    assert response.status == 500
                    failures += 1
                if failures == 3:
                    break
            assert failures == 3
            request(args.discovery, "/v1/instances", "POST", registration, admin)
            state, _ = request(args.discovery, "/v1/instances", headers=admin)
            assert next(i for i in state["instances"] if i["instance_id"] == identity)["removed"]
            for _ in range(4):
                assert request(args.gateway, f"/{service}/api/v1/status")[1]["X-Service-Instance"] != identity
            request(args.discovery, f"/v1/instances/{identity}/restore", "POST", headers=admin)
            print("PASS:", service, "three real failures remove replica; heartbeat cannot restore; explicit restore works", flush=True)
        finally:
            request(args.discovery, f"/v1/instances/{identity}", "DELETE", headers=admin)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
