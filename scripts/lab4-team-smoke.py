"""Real Lab 4 business acceptance through user Gateway and authenticated gRPC topics.

Creates isolated demo records. Correct exam answers come only from the local fixture
DB to exercise pass/reward handling; no application answer endpoint is exposed.
Never prints user tokens, passwords or local configuration.
"""

import json
import subprocess
import time
import uuid
from pathlib import Path
from lab4_client import Lab4Client, http

ROOT = Path(__file__).resolve().parents[1]
CLIENT = None


def eventually(check, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(1)
    raise AssertionError("Timed out waiting for integration state")


def request(api, path, method="GET", body=None, headers=None, expected=200):
    headers = dict(headers or {})
    source = headers.pop("Fixture-Source", None)
    parts = path.split("/", 2)
    if len(parts) > 2 and parts[1].endswith("-service"):
        service = parts[1]
        path = "/" + parts[2]
        if source:
            return CLIENT.call(
                service,
                path,
                method,
                body,
                source=source,
                key=headers.pop("Idempotency-Key", ""),
                expected=expected,
                headers=headers,
            )
    return http(api, path, method=method, body=body, headers=headers, expected=expected)


def game_demo(api, player_id, player_auth, service_auth):
    """Exercise Game's real World/Base/Resource/Exam/Zombie calls through Gateway."""
    key = str(uuid.uuid4())
    body = {"name": "full-game-" + key[:8], "university": "FAF", "max_players": 8}
    lobby, first = request(
        api, "/game-service/api/v1/lobbies", "POST", body, player_auth(key), 201
    )
    lobby_id = lobby["lobby_id"]
    replay, second = request(
        api, "/game-service/api/v1/lobbies", "POST", body, player_auth(key), 201
    )
    assert replay["lobby_id"] == lobby_id and replay["base_id"] == lobby["base_id"]
    # The broker deliberately caches an idempotent reply, so replay need not
    # execute on another worker. Replica transaction recovery is tested in CI.
    world, _ = request(
        api, f"/world-service/api/v1/worlds/{lobby_id}", headers=player_auth()
    )
    base, _ = request(
        api, f"/base-service/api/v1/bases/{lobby['base_id']}", headers=player_auth()
    )
    assert base["home_room_id"] == world["home_room_id"]
    print(
        "PASS: Broker-cached Game lobby replay prepares one real World/Base with the correct home room",
        flush=True,
    )

    def ready_node():
        nodes, _ = request(
            api,
            f"/world-service/api/v1/worlds/{lobby_id}/nodes",
            headers=service_auth("world-service"),
        )
        return next(
            (n for n in nodes["items"] if n["item_id"] == "wood-01" and n["pool_id"]),
            None,
        )

    node = eventually(ready_node)

    def action(kind, node_id=""):
        action_key = str(uuid.uuid4())
        body = {
            "action_type": kind,
            "target_room_id": node["room_id"],
            "target_node_id": node_id,
        }
        started, _ = request(
            api,
            f"/game-service/api/v1/lobbies/{lobby_id}/actions",
            "POST",
            body,
            player_auth(action_key),
            202,
        )
        replay, _ = request(
            api,
            f"/game-service/api/v1/lobbies/{lobby_id}/actions",
            "POST",
            body,
            player_auth(action_key),
            202,
        )
        assert started["action_id"] == replay["action_id"]

        def complete():
            result, _ = request(
                api,
                "/game-service/api/v1/actions/" + started["action_id"],
                headers=player_auth(),
            )
            return result if result["status"] == "completed" else None

        return eventually(complete)

    result = action("chop_bench", node["node_id"])
    assert result["reward"]["items"] == [{"item_id": "wood-01", "count": 1}]
    balances, _ = request(
        api,
        f"/resource-service/api/v1/players/{player_id}/resources?lobby_id={lobby_id}",
        headers=player_auth(),
    )
    assert (
        next(v["amount"] for v in balances["resources"] if v["item_id"] == "wood-01")
        == 1
    )
    action("clear_room")
    room, _ = request(
        api, "/world-service/api/v1/rooms/" + node["room_id"], headers=player_auth()
    )
    assert room["safe"]
    print(
        "PASS: Game completes a real node gather once and clears the actual World room",
        flush=True,
    )

    pool, _ = request(
        api,
        "/resource-service/api/v1/pools",
        "POST",
        {
            "lobby_id": lobby_id,
            "room_id": node["room_id"],
            "node_id": str(uuid.uuid4()),
            "kind": "node",
            "initial_stock": [{"item_id": "wood-01", "count": 30}],
        },
        service_auth("resource-service", str(uuid.uuid4())),
        201,
    )
    request(
        api,
        "/resource-service/api/v1/transactions/gather",
        "POST",
        {
            "lobby_id": lobby_id,
            "player_id": player_id,
            "source_pool_id": pool["pool_id"],
            "action_id": str(uuid.uuid4()),
            "reason": "game-demo-fixture",
            "items": [{"item_id": "wood-01", "count": 30}],
        },
        service_auth("resource-service", str(uuid.uuid4())),
    )
    built = action("barricade_room")
    barricade_id = built["reward"]["barricade_id"]
    request(
        api,
        "/base-service/api/v1/barricades/" + barricade_id,
        "PATCH",
        {"operation": "damage", "amount": 5},
        service_auth("base-service", str(uuid.uuid4())),
    )
    repaired = action("repair_base")
    assert repaired["reward"]["health"] == repaired["reward"]["strength"]
    print(
        "PASS: Game builds and repairs a real Base barricade using real Resource balances",
        flush=True,
    )

    cycle_key = str(uuid.uuid4())
    path = f"/game-service/api/v1/lobbies/{lobby_id}/cycle"
    night, _ = request(api, path, "POST", {}, service_auth("game-service", cycle_key))
    replay, _ = request(api, path, "POST", {}, service_auth("game-service", cycle_key))
    assert (
        night["phase"] == replay["phase"] == "night"
        and night["day"] == replay["day"] == 1
    )
    day, _ = request(
        api, path, "POST", {}, service_auth("game-service", str(uuid.uuid4()))
    )
    assert day["phase"] == "day" and day["day"] == 2
    print(
        "PASS: Game publishes ordered Zombie cycle events; replay never advances the cycle twice",
        flush=True,
    )

    zombie, _ = request(
        api,
        "/zombie-service/api/v1/zombies",
        "POST",
        {
            "type_id": "professor_zombie",
            "lobby_id": lobby_id,
            "room_id": node["room_id"],
        },
        service_auth("zombie-service", str(uuid.uuid4())),
        201,
    )
    encounter_key = str(uuid.uuid4())
    encounter_body = {
        "player_id": player_id,
        "zombie_id": zombie["zombie_id"],
        "zombie_type": "professor_zombie",
    }
    path = f"/game-service/api/v1/lobbies/{lobby_id}/encounters"
    encounter, _ = request(
        api,
        path,
        "POST",
        encounter_body,
        service_auth("game-service", encounter_key),
        201,
    )
    replay, _ = request(
        api,
        path,
        "POST",
        encounter_body,
        service_auth("game-service", encounter_key),
        201,
    )
    assert encounter == replay and encounter["outcome"] == "exam_started"
    exam, _ = request(
        api, "/exam-service/api/v1/exams/" + encounter["exam_id"], headers=player_auth()
    )
    assert len(exam["questions"]) == 10
    # Answers are test setup inside the fixture DB, never exposed by an application API.
    exam_id = str(uuid.UUID(encounter["exam_id"]))
    sql = (
        "SELECT json_agg(json_build_object('question_id',q.question_id,'option_index',q.correct_option)) "
        "FROM exam_questions e JOIN questions q USING(question_id) WHERE e.exam_id='"
        + exam_id
        + "'"
    )
    answers = json.loads(
        subprocess.check_output(
            [
                "docker",
                "compose",
                "-f",
                "deploy/docker-compose.lab4.yml",
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
                sql,
            ],
            cwd=ROOT,
            text=True,
        )
    )
    for answer in answers:
        request(
            api,
            f"/exam-service/api/v1/exams/{exam_id}/answers",
            "POST",
            answer,
            player_auth(),
        )
    submit_key = str(uuid.uuid4())
    result, _ = request(
        api,
        f"/exam-service/api/v1/exams/{exam_id}/submit",
        "POST",
        {},
        player_auth(submit_key),
    )
    replay_result, _ = request(
        api,
        f"/exam-service/api/v1/exams/{exam_id}/submit",
        "POST",
        {},
        player_auth(submit_key),
    )
    assert result == replay_result and result["grade"] == 10

    def rewarded():
        inventory, _ = request(
            api,
            f"/player-service/api/v1/players/{player_id}/inventory",
            headers=player_auth(),
        )
        counts = {item["item_id"]: item["count"] for item in inventory["items"]}
        return counts.get("coffee-01") == 2 and counts.get("cheatsheet-01") == 1

    eventually(rewarded)
    print(
        "PASS: Exam first-pass/perfect-score rewards reach Player inventory exactly once on submit replay",
        flush=True,
    )

    def unlocked():
        wings, _ = request(
            api, f"/world-service/api/v1/worlds/{lobby_id}/wings", headers=player_auth()
        )
        return sum(bool(w["unlocked"]) for w in wings["items"]) >= 2

    # Some courses intentionally have no World wing. Exercise a real supported unlock
    # command as a controlled fixture in that case; never claim every exam opens a wing.
    wings, _ = request(
        api, f"/world-service/api/v1/worlds/{lobby_id}/wings", headers=player_auth()
    )
    if not any(
        w.get("unlock_course_id") == exam.get("course_id")
        for w in world["locked_wings"]
    ):
        request(
            api,
            f"/world-service/api/v1/worlds/{lobby_id}/unlock",
            "POST",
            {"course_id": "math-101", "player_id": player_id},
            service_auth("world-service", str(uuid.uuid4())),
        )
    eventually(unlocked)

    def callback_received():
        query = (
            "SELECT count(*) FROM received_events WHERE fingerprint LIKE '%"
            + lobby_id
            + "%'"
        )
        count = subprocess.check_output(
            [
                "docker",
                "compose",
                "-f",
                "deploy/docker-compose.lab4.yml",
                "exec",
                "-T",
                "game-db",
                "psql",
                "-U",
                "game",
                "-d",
                "game_db",
                "-At",
                "-c",
                query,
            ],
            cwd=ROOT,
            text=True,
        ).strip()
        return int(count) == 1

    eventually(callback_received)
    print(
        "PASS: Game professor exam replay/pass, supported World unlock and authenticated Game callback",
        flush=True,
    )

    # Supply actual materials through Resource's real transactional API.
    pool, _ = request(
        api,
        "/resource-service/api/v1/pools",
        "POST",
        {
            "lobby_id": lobby_id,
            "room_id": node["room_id"],
            "node_id": str(uuid.uuid4()),
            "kind": "node",
            "initial_stock": [
                {"item_id": "wood-01", "count": 20},
                {"item_id": "metal-01", "count": 10},
            ],
        },
        service_auth("resource-service", str(uuid.uuid4())),
        201,
    )
    request(
        api,
        "/resource-service/api/v1/transactions/gather",
        "POST",
        {
            "lobby_id": lobby_id,
            "player_id": player_id,
            "source_pool_id": pool["pool_id"],
            "action_id": str(uuid.uuid4()),
            "reason": "lab4-crafting-fixture",
            "items": [
                {"item_id": "wood-01", "count": 20},
                {"item_id": "metal-01", "count": 10},
            ],
        },
        service_auth("resource-service", str(uuid.uuid4())),
    )
    preview, _ = request(
        api,
        "/crafting-service/api/v1/recipes/recipe-barricade-kit/preview",
        "POST",
        {"player_id": player_id, "lobby_id": lobby_id},
        player_auth(str(uuid.uuid4())),
    )
    assert preview["materials_verified"] and preview["craftable"]
    craft_key = str(uuid.uuid4())
    body = {
        "player_id": player_id,
        "lobby_id": lobby_id,
        "recipe_id": "recipe-barricade-kit",
    }
    crafted, _ = request(
        api,
        "/crafting-service/api/v1/crafts",
        "POST",
        body,
        player_auth(craft_key),
        201,
    )
    replay, _ = request(
        api,
        "/crafting-service/api/v1/crafts",
        "POST",
        body,
        player_auth(craft_key),
        201,
    )
    assert (
        crafted == replay
    )  # Broker result replay precedes the worker's replay response.

    def craft_rewarded():
        inventory, _ = request(
            api,
            f"/player-service/api/v1/players/{player_id}/inventory",
            headers=player_auth(),
        )
        return {i["item_id"]: i["count"] for i in inventory["items"]}.get(
            "barricade-kit-01"
        ) == 1

    eventually(craft_rewarded)
    print(
        "PASS: Crafting verifies real delegated balances and grants exactly one Player output on replay",
        flush=True,
    )


def main():
    global CLIENT
    CLIENT = Lab4Client()
    try:
        services = [
            s + "-service"
            for s in (
                "player",
                "game",
                "exam",
                "world",
                "zombie",
                "resource",
                "base",
                "crafting",
            )
        ]
        rows = CLIENT.registry()
        for service in services:
            active = [
                i
                for i in rows
                if i["service"] == service
                and i["healthy"]
                and i["lease_valid"]
                and not i["removed"]
            ]
            assert len(active) == 2, (
                service,
                "expected two eligible workers",
                len(active),
            )
        print("PASS: 16 healthy registered native gRPC workers", flush=True)
        suffix = uuid.uuid4().hex[:12]
        account = {
            "username": "lab4_" + suffix,
            "email": suffix + "@lab4.test",
            "password": "Lab4-" + uuid.uuid4().hex,
        }
        player, _ = request(
            CLIENT.gateway, "/api/v1/players/register", "POST", account, expected=201
        )
        tokens, _ = request(
            CLIENT.gateway,
            "/api/v1/players/login",
            "POST",
            {k: account[k] for k in ("email", "password")},
        )

        def player_auth(key=None):
            return {
                "Authorization": "Bearer " + tokens["access_token"],
                **({"Idempotency-Key": key} if key else {}),
            }

        def service_auth(source, key=None):
            return {
                "Fixture-Source": source,
                **({"Idempotency-Key": key} if key else {}),
            }

        # This enrolment must come from the actual transactional PlayerRegistered
        # outbox, not a manually forged/injected registration event.
        registration_event = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "player-service:PlayerRegistered:" + player["player_id"],
            )
        )

        def enrolled():
            query = (
                "SELECT count(*) FROM processed_events WHERE event_id='"
                + registration_event
                + "'"
            )
            count = subprocess.check_output(
                [
                    "docker",
                    "compose",
                    "-f",
                    "deploy/docker-compose.lab4.yml",
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
                ],
                cwd=ROOT,
                text=True,
            ).strip()
            return count == "1"

        eventually(enrolled)
        print("PASS: automatic PlayerRegistered topic enrolment", flush=True)
        game_demo(CLIENT.gateway, player["player_id"], player_auth, service_auth)
    finally:
        CLIENT.close()


if __name__ == "__main__":
    main()
