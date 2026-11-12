"""Create real, isolated Lab 4 fixtures and a PRIVATE Postman environment.

Requires the running Lab 4 Compose stack. No development token endpoints or
database edits: internal setup uses authenticated broker gRPC. Never commit the
output; it contains demo user credentials and local operator tokens.
"""
import argparse
import base64
import json
import re
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from google.protobuf.json_format import ParseDict
import jwt
from lab4_rpc import broker_pb2

from lab4_client import Lab4Client, ROOT, http


def prepare(output):
    output = Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise SystemExit("Choose an output outside the repository (contains credentials).")
    c = Lab4Client()
    try:
        http(c.gateway, "/health")
        c.ops("/v1/topics")
        for service in ("player", "game", "exam", "world", "zombie", "resource", "base", "crafting"):
            c.call(service + "-service", "/api/v1/status")
        values = dict(gateway=c.gateway, broker=c.operations, discovery=c.discovery,
                      brokerGrpc="localhost:28600", brokerAdminToken=c.env["BROKER_ADMIN_TOKEN"],
                      discoveryToken=c.env["DISCOVERY_TOKEN"])

        def user(prefix):
            suffix = uuid4().hex[:12]
            account = dict(username="pm_" + suffix, email=suffix + "@postman.invalid",
                           password="Demo_" + uuid4().hex + "A9!")
            player, _ = http(c.gateway, "/api/v1/players/register", method="POST", body=account, expected=201)
            login, _ = http(c.gateway, "/api/v1/players/login", method="POST",
                            body={k: account[k] for k in ("email", "password")})
            values.update({prefix + "Id": player["player_id"], prefix + "Token": login["access_token"],
                           prefix + "RefreshToken": login["refresh_token"], prefix + "Email": account["email"],
                           prefix + "Password": account["password"]})
            return player["player_id"]

        player_id, other_id = user("player"), user("otherPlayer")
        headers = {"Authorization": "Bearer " + values["playerToken"], "Idempotency-Key": str(uuid4())}
        lobby, _ = http(c.gateway, "/api/v1/lobbies", method="POST", headers=headers,
                        body=dict(name="Postman Lab 4 " + uuid4().hex[:8], university="FAF", max_players=8), expected=201)
        values.update(lobbyId=lobby["lobby_id"], baseId=lobby["base_id"])
        world, _ = http(c.gateway, "/api/v1/worlds/" + values["lobbyId"], headers=headers)
        values["roomId"] = world["rooms"][0]["room_id"]
        # Keep separate rooms for a build request and a pre-damaged repair fixture.
        values["buildRoomId"] = world["rooms"][1]["room_id"]
        nodes, _ = c.call("world-service", f"/api/v1/worlds/{values['lobbyId']}/nodes", source="game-service")
        node = next(n for n in nodes["items"] if n["item_id"] == "wood-01")
        values.update(nodeId=node["node_id"], nodeRoomId=node["room_id"])
        materials = [{"item_id": name, "count": 200} for name in ("wood-01", "metal-01", "paper-01", "electronics-01")]
        pool, _ = c.call("resource-service", "/api/v1/pools", "POST",
                         dict(lobby_id=values["lobbyId"], room_id=values["roomId"], node_id=str(uuid4()),
                              kind="node", initial_stock=[{**m, "count": 300} for m in materials]), source="resource-service", key=str(uuid4()), expected=201)
        c.call("resource-service", "/api/v1/transactions/gather", "POST",
               dict(lobby_id=values["lobbyId"], player_id=player_id, source_pool_id=pool["pool_id"],
                    action_id=str(uuid4()), reason="postman-fixture", items=materials),
               source="resource-service", key=str(uuid4()))
        values["sourcePoolId"] = pool["pool_id"]
        barricade, _ = c.call("base-service", f"/api/v1/bases/{values['baseId']}/barricades", "POST",
                             dict(player_id=player_id, room_id=values["roomId"], material="wood"),
                             source="base-service", key=str(uuid4()), expected=201)
        values["repairBarricadeId"] = barricade["barricade_id"]
        c.call("base-service", "/api/v1/barricades/" + barricade["barricade_id"], "PATCH",
               dict(operation="damage", amount=5), source="game-service", key=str(uuid4()))
        exam, _ = c.call("exam-service", "/api/v1/exams", "POST",
                         dict(player_id=player_id, lobby_id=values["lobbyId"], encounter_id=str(uuid4())),
                         source="game-service", key=str(uuid4()), expected=201)
        values["examId"] = exam["exam_id"]
        view, _ = http(c.gateway, "/api/v1/exams/" + values["examId"], headers=headers)
        values["questionId"] = view["questions"][0]["question_id"]
        zombie, _ = c.call("zombie-service", "/api/v1/zombies", "POST",
                           dict(type_id="tourist_zombie", lobby_id=values["lobbyId"], room_id=values["roomId"]),
                           source="game-service", key=str(uuid4()), expected=201)
        values["zombieId"] = zombie["zombie_id"]
        # PlayerRegistered must reach Exam through the real topic delivery.
        deadline = time.monotonic() + 30
        while True:
            courses, _ = http(c.gateway, f"/api/v1/players/{player_id}/courses", headers=headers)
            if courses.get("items"):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("PlayerRegistered enrolment did not arrive")
            time.sleep(1)
        data = {"name": "PAD Lab 4 LOCAL DEMO", "_postman_variable_scope": "environment",
                "values": [{"key": k, "value": v, "enabled": True,
                            "type": "secret" if any(s in k.lower() for s in ("token", "password")) else "default"}
                           for k, v in values.items()]}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        templates = json.loads((ROOT / "postman/grpc/requests.json").read_text(encoding="utf-8"))
        rendered = []
        for example in templates["requests"]:
            def expand(match):
                return str(uuid4()) if match[1] == "$guid" else str(values[match[1]])
            row = json.loads(re.sub(r"\{\{([^}]+)\}\}", expand, json.dumps(example)))
            message = row.pop("message")
            body = row.pop("json_body")
            message.update(request_id=str(uuid4()), headers={"content-type": "application/json"},
                           idempotency_key=str(uuid4()) if message["method"] != "GET" else "")
            if body is not None:
                message["body"] = base64.b64encode(json.dumps(body).encode()).decode()
            if message["topic"] == "commands.game" and "/lobbies/" in message["path"]:
                message["affinity"] = values["lobbyId"]
            ParseDict(message, broker_pb2.Command())  # Validate real protobuf field/bytes encoding.
            token = jwt.encode(dict(sub=row["source"], roles=["service"], aud="message-broker",
                                    iat=int(time.time()), exp=int(time.time()) + 3600),
                               c.env["SERVICE_JWT_SECRET"], algorithm="HS256")
            row.update(message=message, target="localhost:28600",
                       metadata={"authorization": "Bearer " + token})
            rendered.append(row)
        grpc_output = output.with_name("PAD-LAB4-GRPC-PRIVATE.json")
        grpc_output.write_text(json.dumps({"requests": rendered}, indent=2) + "\n", encoding="utf-8")
        print("Private Postman environment (do not share): " + str(output))
        print("Private rendered gRPC examples (tokens expire in one hour): " + str(grpc_output))
        print("Ready: eight services, two real users, lobby/world/base, materials, damaged barricade and active exam.")
    finally:
        c.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(tempfile.gettempdir()) / "PAD-LAB4-LOCAL.postman_environment.json")
    prepare(parser.parse_args().output)
