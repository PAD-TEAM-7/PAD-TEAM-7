"""Generate every current Lab 4 REST collection from one reviewable manifest.

Run --check in CI to catch stale generated files. Internal operations are shown
as denied Gateway requests, with real gRPC examples alongside the collections.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
NIL = "00000000-0000-4000-8000-000000000000"


def script(kind, lines):
    return {"listen": kind, "script": {"type": "text/javascript", "exec": lines}}


def request(name, method, path, body=None, status=200, *, auth="playerToken", capture=None,
            checks=(), key="{{$guid}}", before=(), host="gateway", description=""):
    headers = [{"key": "X-Request-ID", "value": "{{$guid}}"}]
    if auth:
        headers.append({"key": "Authorization", "value": "Bearer {{" + auth + "}}"})
    r = {"method": method, "header": headers, "auth": {"type": "noauth"},
         "url": "{{" + host + "}}" + path,
         "description": description or "Lab 4: user REST enters Gateway; Gateway dispatches through Broker gRPC."}
    if body is not None:
        headers.append({"key": "Content-Type", "value": "application/json"})
        r["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    if method in ("POST", "PATCH", "PUT", "DELETE") and key:
        headers.append({"key": "Idempotency-Key", "value": key})
    tests = [f'pm.test("HTTP {status}", () => pm.response.to.have.status({status}));']
    if capture:
        tests.append(f'if (pm.response.code === {status}) {{ const data = pm.response.json();')
        for var, expr in capture.items():
            tests.append(f'pm.test("{var} returned", () => pm.expect({expr}).to.be.a("string").and.not.empty);')
            tests.append(f'pm.environment.set("{var}", {expr});')
        tests.append("}")
    tests += list(checks)
    events = [script("test", tests)]
    if before:
        events.insert(0, script("prerequest", list(before)))
    return {"name": name, "request": r, "event": events}


def denied(name, method, path, body=None):
    return request(name + " (removed: 404)", method, path, body, 404,
                   checks=('pm.test("Gateway removed route", () => pm.expect(pm.response.json().error.code).to.eql("ROUTE_NOT_FOUND"));',))


def collection(name, folders):
    return {"info": {"name": "PAD Lab 4 - " + name, "schema": SCHEMA,
            "description": "Current Lab 4 deployment (Gateway 28080, Broker 28601, Discovery 28500). "
            "FIRST run scripts/lab4-prepare-postman.py and import its PRIVATE environment. Run folders in order. "
            "Regenerate fixtures before repeating a collection. All requests assert their expected status. "
            "Removed service-only Gateway routes deliberately expect 404. Native gRPC examples: postman/grpc/README.md. "
            "Never export real environment credentials into Git."},
            "variable": [{"key": k, "value": v} for k, v in dict(gateway="http://localhost:28080",
                broker="http://localhost:28601", discovery="http://localhost:28500").items()],
            "item": [{"name": title, "item": items} for title, items in folders]}


def build():
    p = "/api/v1/players/{{playerId}}"
    l = "/api/v1/lobbies/{{lobbyId}}"
    b = "/api/v1/bases/{{baseId}}"
    w = "/api/v1/worlds/{{lobbyId}}"
    e = "/api/v1/exams/{{examId}}"
    player = [
        request("01 Register a fresh additional player", "POST", "/api/v1/players/register",
                dict(username="{{newUsername}}", email="{{newEmail}}", password="{{newPassword}}"), 201,
                auth=None, capture={"newPlayerId": "data.player_id"}, before=(
                    'const id = pm.variables.replaceIn("{{$guid}}").replace(/-/g, "");',
                    'pm.environment.set("newUsername", "pm_" + id.slice(0, 12));',
                    'pm.environment.set("newEmail", id + "@postman.invalid");',
                    'pm.environment.set("newPassword", "Demo_" + id + "A9!");')),
        request("02 Login real fixture player", "POST", "/api/v1/players/login",
                dict(email="{{playerEmail}}", password="{{playerPassword}}"), auth=None,
                capture={"playerToken": "data.access_token", "playerRefreshToken": "data.refresh_token"}),
        request("03 Public signing keys", "GET", "/api/v1/auth/jwks", auth=None,
                checks=('pm.test("RSA keys", () => pm.expect(pm.response.json().keys).to.be.an("array").and.not.empty);',)),
        request("04 Profile", "GET", p),
        request("05 Update own profile", "PATCH", p, dict(title="Lab 4 survivor", avatar="survivor.png")),
        request("06 Inventory", "GET", p + "/inventory"),
        request("07 Set presence", "PUT", p + "/presence", dict(status="online")),
        request("08 Add real friend", "POST", p + "/friends", dict(friend_id="{{otherPlayerId}}"), 201),
        request("09 List friends", "GET", p + "/friends"),
        request("10 Remove friend", "DELETE", p + "/friends/{{otherPlayerId}}", status=204),
        request("11 Refresh real session", "POST", "/api/v1/players/refresh",
                dict(refresh_token="{{playerRefreshToken}}"), auth=None,
                capture={"playerToken": "data.access_token", "playerRefreshToken": "data.refresh_token"}),
        request("12 Protected profile without token", "GET", p, status=401, auth=None),
        denied("XP is service-only", "POST", p + "/xp", dict(amount=1)),
        denied("Inventory mutation is service-only", "PATCH", p + "/inventory", {}),
        denied("Development tokens are disabled", "POST", "/api/v1/dev/tokens", {}),
    ]
    game = [
        request("01 List lobbies", "GET", "/api/v1/lobbies?university=FAF"),
        request("02 Create additional real World/Base lobby", "POST", "/api/v1/lobbies",
                dict(name="Postman extra lobby", university="FAF", max_players=8), 201,
                capture={"extraLobbyId": "data.lobby_id"}),
        request("03 Read prepared lobby", "GET", l),
        request("04 Other player joins", "POST", l + "/join", dict(player_id="{{otherPlayerId}}"), auth="otherPlayerToken"),
        request("05 Start real gather action", "POST", l + "/actions",
                dict(action_type="chop_bench", target_room_id="{{nodeRoomId}}", target_node_id="{{nodeId}}"), 202,
                capture={"actionId": "data.action_id"}),
        request("06 Read action", "GET", "/api/v1/actions/{{actionId}}"),
        request("07 Cancel a missing action - conflict", "DELETE", "/api/v1/actions/" + NIL, status=409,
                description="Current Game contract returns conflict when no running action can be cancelled. For a successful cancel, immediately DELETE /api/v1/actions/{{actionId}} after request 05 (before its six-second demo duration), expecting 204."),
        request("08 Offer an inventory-backed trade", "POST", l + "/trades",
                dict(to_player_id="{{otherPlayerId}}", offer=[dict(item_id="axe-01", count=1)], request=[]), 201,
                capture={"tradeId": "data.trade_id"}),
        request("09 Recipient declines trade", "POST", "/api/v1/trades/{{tradeId}}/respond", dict(decision="decline"), auth="otherPlayerToken"),
        request("10 WebSocket owner negotiation", "GET", "/ws/v1/lobbies/{{lobbyId}}",
                checks=('pm.test("Direct WebSocket URL", () => pm.expect(pm.response.json().url).to.match(/^wss?:/));',)),
        request("11 Other player leaves", "POST", l + "/leave", {}, 204, auth="otherPlayerToken"),
        denied("Cycle commands use Broker", "POST", l + "/cycle", {}),
        denied("Encounters use Broker", "POST", l + "/encounters", {}),
    ]
    exam = [request("01 Course catalogue", "GET", "/api/v1/courses"),
        request("02 Courses enrolled by PlayerRegistered topic", "GET", p + "/courses",
                checks=('pm.test("Real asynchronous enrolment", () => pm.expect(pm.response.json().items).to.be.an("array").and.not.empty);',)),
        request("03 Read prepared exam", "GET", e,
                checks=('pm.test("Ten questions", () => pm.expect(pm.response.json().questions).to.have.lengthOf(10));',)),
        request("04 Save answer", "POST", e + "/answers", dict(question_id="{{questionId}}", option_index=0)),
        request("05 Submit exam", "POST", e + "/submit", {}, key="{{examSubmitKey}}",
                before=('pm.environment.set("examSubmitKey", pm.variables.replaceIn("{{$guid}}"));',),
                checks=('pm.environment.set("examSubmitResult", JSON.stringify(pm.response.json()));',)),
        request("06 Replay submit - identical outcome", "POST", e + "/submit", {}, key="{{examSubmitKey}}",
                checks=('pm.test("Exactly same submit result", () => pm.expect(JSON.stringify(pm.response.json())).to.eql(pm.environment.get("examSubmitResult")));',)),
        request("07 Exam history", "GET", p + "/exams?limit=10"),
        request("08 Achievements", "GET", p + "/achievements"),
        request("09 Diploma progress", "GET", p + "/diploma"),
        request("10 Other player cannot view this exam", "GET", e, status=403, auth="otherPlayerToken"),
        denied("Issue exam is service-only", "POST", "/api/v1/exams", {}),
        denied("Internal passed-course lookup", "GET", p + "/passed"),
    ]
    world = [request("01 Generated map", "GET", w), request("02 Wing unlock state", "GET", w + "/wings"),
        request("03 Rooms", "GET", w + "/rooms"), request("04 Room detail", "GET", "/api/v1/rooms/{{roomId}}"),
        request("05 Missing world", "GET", "/api/v1/worlds/" + NIL, status=404),
        denied("Generation is service-only", "POST", "/api/v1/worlds", {}),
        denied("Nodes use Broker gRPC", "GET", w + "/nodes"),
        denied("Spawns use Broker gRPC", "GET", w + "/spawns"),
        denied("Unlock uses Broker", "POST", w + "/unlock", {}),
        denied("Room mutation uses Broker", "PATCH", "/api/v1/rooms/{{roomId}}", dict(safe=True)),
    ]
    zombie = [request("01 Zombie type catalogue", "GET", "/api/v1/zombie-types",
                      capture={"zombieTypeId": "data.items[0].type_id"}),
        request("02 Zombie type details", "GET", "/api/v1/zombie-types/{{zombieTypeId}}"),
        denied("Spawn uses Broker", "POST", "/api/v1/zombies", {}),
        denied("Damage uses Broker", "POST", "/api/v1/zombies/" + NIL + "/damage", dict(amount=1)),
        denied("Kill uses Broker", "POST", "/api/v1/zombies/" + NIL + "/kill", {}),
        denied("Steal uses Broker", "POST", "/api/v1/zombies/" + NIL + "/steal", {}),
    ]
    resource = [request("01 Real lobby-scoped balances", "GET", p + "/resources?lobby_id={{lobbyId}}",
                        checks=('pm.test("Real balances", () => pm.expect(pm.response.json().resources).to.be.an("array").and.not.empty);',)),
        request("02 Balances require player authentication", "GET", p + "/resources?lobby_id={{lobbyId}}", status=401, auth=None),
        denied("Pools are service-only", "POST", "/api/v1/pools", {}),
        denied("Gather uses Broker", "POST", "/api/v1/transactions/gather", {}),
        denied("Consume uses Broker", "POST", "/api/v1/transactions/consume", {}),
        denied("Transfer uses Broker", "POST", "/api/v1/transactions/transfer", {}),
        denied("Compensation uses Broker", "POST", "/api/v1/transactions/" + NIL + "/compensate", {}),
    ]
    base = [request("01 Find base by lobby", "GET", "/api/v1/bases?lobby_id={{lobbyId}}"),
        request("02 Base state", "GET", b), request("03 Facilities", "GET", b + "/facilities"),
        request("04 Barricades", "GET", b + "/barricades"), request("05 Kiki state inside base response", "GET", b,
                checks=('pm.test("Kiki state is included", () => pm.expect(pm.response.json().kiki.present).to.eql(true));',)),
        request("05b No separate Kiki GET handler in this release", "GET", b + "/kiki", status=404,
                description="Gateway allowlists this path, but Base only implements POST Kiki. Read Kiki state from the base object instead."),
        request("06 Upgrade using real Resource materials", "POST", b + "/upgrade", dict(player_id="{{playerId}}"),
                key="{{baseUpgradeKey}}", before=('pm.environment.set("baseUpgradeKey", pm.variables.replaceIn("{{$guid}}"));',),
                checks=('pm.test("Level two", () => pm.expect(pm.response.json().level).to.eql(2));',)),
        request("07 Replay upgrade - no second upgrade", "POST", b + "/upgrade", dict(player_id="{{playerId}}"),
                key="{{baseUpgradeKey}}", checks=('pm.test("Still level two", () => pm.expect(pm.response.json().level).to.eql(2));',)),
        request("08 Build workbench", "POST", b + "/facilities", dict(player_id="{{playerId}}", type="workbench")),
        request("09 Build in a real room", "POST", b + "/barricades", dict(player_id="{{playerId}}", room_id="{{buildRoomId}}", material="wood"), 201),
        request("10 Repair prepared damaged barricade", "PATCH", "/api/v1/barricades/{{repairBarricadeId}}",
                dict(operation="repair", player_id="{{playerId}}"), checks=('pm.test("Health restored", () => pm.expect(pm.response.json().health).to.eql(pm.response.json().strength));',)),
        denied("Base creation is service-only", "POST", "/api/v1/bases", {}),
        denied("Blueprint route is absent from current public surface", "GET", b + "/blueprints"),
    ]
    craft_body = dict(player_id="{{playerId}}", lobby_id="{{lobbyId}}", base_id="{{baseId}}", recipe_id="recipe-barricade-kit")
    crafting = [request("01 Recipe catalogue", "GET", "/api/v1/recipes"),
        request("02 Filter recipes", "GET", "/api/v1/recipes?category=defense"),
        request("03 Recipe detail", "GET", "/api/v1/recipes/recipe-barricade-kit"),
        request("04 Available recipes - known service regression", "GET", "/api/v1/recipes/available?player_id={{playerId}}&lobby_id={{lobbyId}}&base_id={{baseId}}",
                description="Expected 200. Current release fails: Crafting requests Player profile with a service identity (403) and World /api/v1/wings?lobby_id=... (404; current World API is /api/v1/worlds/{lobbyId}/wings with player auth). Keep this assertion failing until the service integration is fixed; do not treat 403/404 as success."),
        request("05 Preview verifies real Resource balances", "POST", "/api/v1/recipes/recipe-barricade-kit/preview",
                dict(player_id="{{playerId}}", lobby_id="{{lobbyId}}", base_id="{{baseId}}"),
                checks=('pm.test("Real materials verified", () => pm.expect(pm.response.json().materials_verified).to.eql(true));',
                        'pm.test("Craftable", () => pm.expect(pm.response.json().craftable).to.eql(true));')),
        request("06 Craft with real consumption and Player reward", "POST", "/api/v1/crafts", craft_body, 201,
                capture={"jobId": "data.job_id"}, key="{{craftKey}}",
                before=('pm.environment.set("craftKey", pm.variables.replaceIn("{{$guid}}"));',)),
        request("07 Replay craft - same job", "POST", "/api/v1/crafts", craft_body, 201, key="{{craftKey}}",
                checks=('pm.test("Same job", () => pm.expect(pm.response.json().job_id).to.eql(pm.environment.get("jobId")));',)),
        request("08 Job state", "GET", "/api/v1/crafts/{{jobId}}"),
        request("09 Craft history", "GET", "/api/v1/crafts?player_id={{playerId}}&limit=10"),
        request("10 Another player's history is forbidden", "GET", "/api/v1/crafts?player_id={{otherPlayerId}}", status=403),
        request("11 Unknown recipe", "GET", "/api/v1/recipes/recipe-missing", status=404),
    ]
    gateway = [request("01 Gateway health", "GET", "/health", auth=None),
        request("02 Gateway status", "GET", "/api/v1/status", auth=None),
        request("03 Player through subscriber queue", "GET", p),
        denied("Old service prefix", "GET", "/player-service/api/v1/health"),
        denied("Service-only events", "POST", "/world-service/api/v1/events", {}),
        denied("Old upstream management", "GET", "/health/upstreams"),
        denied("Old routing table", "GET", "/gateway/routes"),
        denied("Development service token issuer", "POST", "/gateway/dev/tokens", {}),
    ]
    broker = [request("01 Broker health", "GET", "/health", auth=None, host="broker"),
        request("02 Topics and interested subscriber groups", "GET", "/v1/topics", auth="brokerAdminToken", host="broker",
                checks=('pm.test("Command topics for all eight services", () => { const names = pm.response.json().topics.map(t => t.name); ["player","game","exam","world","zombie","resource","base","crafting"].forEach(s => pm.expect(names).to.include("commands." + s)); });',)),
        request("03 Durable queues", "GET", "/v1/queues", auth="brokerAdminToken", host="broker"),
        request("04 Dead letter metadata", "GET", "/v1/dead-letters", auth="brokerAdminToken", host="broker",
                checks=('pm.test("Dead letter list", () => pm.expect(pm.response.json().dead_letters).to.be.an("array"));',)),
        request("05 Download ZIP logs - Send and Download", "GET", "/v1/logs/download", auth="brokerAdminToken", host="broker",
                checks=('pm.test("ZIP file", () => pm.expect(pm.response.headers.get("Content-Type")).to.include("application/zip"));',)),
        request("06 Metrics", "GET", "/metrics", auth=None, host="broker",
                checks=('pm.test("Thread-per-request metric", () => pm.expect(pm.response.text()).to.include("broker_rpc_threads_started_total"));',)),
        request("07 Operator token is required", "GET", "/v1/topics", auth=None, host="broker", status=401),
        request("08 Unknown dead letter cannot replay", "POST", "/v1/dead-letters/" + NIL + "/replay", {}, 404, auth="brokerAdminToken", host="broker"),
        request("09 Unknown instance cannot restore", "POST", "/v1/instances/" + NIL + "/restore", {}, 409, auth="brokerAdminToken", host="broker"),
    ]
    discovery = [request("01 Discovery health", "GET", "/health", auth=None, host="discovery"),
        request("02 gRPC replicas, real load and subscriptions", "GET", "/v1/instances", auth="discoveryToken", host="discovery",
                checks=('pm.test("All eight services registered with gRPC and topics", () => { const rows = pm.response.json().instances.filter(i => i.healthy && i.lease_valid && !i.removed); ["player","game","exam","world","zombie","resource","base","crafting"].forEach(s => { const replicas = rows.filter(i => i.service === s + "-service"); pm.expect(replicas.length).to.be.at.least(2); replicas.forEach(i => { pm.expect(i.grpc_url).to.be.a("string").and.not.empty; pm.expect(i.topics).to.include("commands." + s); }); }); });',)),
        request("03 Registered topic interests", "GET", "/v1/topics", auth="discoveryToken", host="discovery"),
        request("04 Eligible Base replica", "GET", "/v1/select/base-service", auth="discoveryToken", host="discovery"),
        request("05 Discovery token is required", "GET", "/v1/instances", auth=None, host="discovery", status=401),
    ]
    groups = dict(player=player, game=game, exam=exam, world=world, zombie=zombie,
                  resource=resource, base=base, crafting=crafting, gateway=gateway,
                  **{"message-broker": broker, "service-discovery": discovery})
    artifacts = {}
    for name, items in groups.items():
        filename = name if name in ("gateway", "message-broker", "service-discovery") else name + "-service"
        artifacts["postman/" + filename + ".postman_collection.json"] = collection(filename, [(filename, items)])
    artifacts["postman/lab4.postman_collection.json"] = collection("complete team demonstration", list(groups.items()))
    artifacts["postman/lab4.postman_environment.json"] = {
        "name": "PAD Lab 4 TEMPLATE - run preparation for real fixtures", "_postman_variable_scope": "environment",
        "values": [{"key": k, "value": v, "enabled": True, "type": "secret" if k.endswith("Token") else "default"}
                   for k, v in dict(gateway="http://localhost:28080", broker="http://localhost:28601",
                       discovery="http://localhost:28500", brokerGrpc="localhost:28600", brokerAdminToken="", discoveryToken="").items()]}
    # Portable templates, not fake REST endpoints. Preparation renders actual
    # protobuf JSON (base64 bytes) plus short-lived service metadata privately.
    grpc_examples = []

    def call(name, destination, path, method="GET", body=None, source="game-service", expected=200):
        grpc_examples.append({"name": name, "source": source, "rpc": "pad.broker.v1.MessageBroker/Call",
            "expected_reply_status": expected, "message": {"topic": "commands." + destination,
                "method": method, "path": path, "timeout_ms": 10000}, "json_body": body})

    for service in ("player", "game", "exam", "world", "zombie", "resource", "base", "crafting"):
        call(service + " real status/load", service, "/api/v1/status")
    call("Player internal inventory read", "player", p + "/inventory", source="crafting-service")
    call("Player internal XP award", "player", p + "/xp", "POST",
         dict(amount=1, reason="postman-grpc", source="exam-service"), source="exam-service")
    call("Game internal cycle", "game", l + "/cycle", "POST", {})
    call("Exam internal passed courses", "exam", p + "/passed", source="crafting-service")
    call("World internal nodes", "world", w + "/nodes")
    call("World internal spawns", "world", w + "/spawns", source="zombie-service")
    call("Zombie spawn", "zombie", "/api/v1/zombies", "POST",
         dict(type_id="tourist_zombie", lobby_id="{{lobbyId}}", room_id="{{roomId}}"), expected=201)
    call("Zombie damage prepared fixture", "zombie", "/api/v1/zombies/{{zombieId}}/damage", "POST",
         dict(amount=1, source_player_id="{{playerId}}", weapon_item_id="axe-01"))
    call("Resource create pool", "resource", "/api/v1/pools", "POST",
         dict(lobby_id="{{lobbyId}}", room_id="{{roomId}}", node_id="{{$guid}}", kind="node",
              initial_stock=[dict(item_id="wood-01", count=5)]), source="world-service", expected=201)
    call("Resource gather real materials", "resource", "/api/v1/transactions/gather", "POST",
         dict(lobby_id="{{lobbyId}}", player_id="{{playerId}}", source_pool_id="{{sourcePoolId}}",
              items=[dict(item_id="wood-01", count=1)], reason="postman-grpc", action_id="{{$guid}}"))
    call("Base internal blueprints", "base", b + "/blueprints", source="crafting-service")
    call("Crafting internal catalogue", "crafting", "/api/v1/recipes")
    artifacts["postman/grpc/requests.json"] = {"description": "Render with lab4-prepare-postman.py. Service operations use Call; Dispatch is Gateway-only. JSON bodies become protobuf base64 bytes.",
                                                "requests": grpc_examples}
    return artifacts


def check_gateway_coverage(artifacts):
    """When the private Gateway checkout exists, cover every allowlisted variant.

    CI without private submodule credentials still checks generated-file parity.
    Literal IDs remain regex wildcards; alternatives and optional suffixes are
    expanded so merely covering one member of a grouped route does not suffice.
    """
    route_file = ROOT / "gateway-service/app/user_routes.py"
    if not route_file.exists():
        return
    import runpy
    routes = runpy.run_path(str(route_file))["USER_ROUTES"]
    requests = [item["request"] for folder in artifacts["postman/lab4.postman_collection.json"]["item"]
                for item in folder["item"]]

    def expand(pattern):
        # Protect the character-class + quantifier from grouping expansion.
        match = re.search(r"\(([^()]+)\)(\?)?", pattern)
        if not match:
            return [pattern]
        choices = match[1].split("|") + ([""] if match[2] else [])
        return [result for choice in choices for result in expand(pattern[:match.start()] + choice + pattern[match.end():])]

    missing = []
    for methods, pattern in routes:
        for method in methods.split("|"):
            for variant in expand(pattern):
                if not any(r["method"] == method and r["url"].startswith("{{gateway}}") and
                           re.fullmatch(variant, r["url"].removeprefix("{{gateway}}").split("?", 1)[0])
                           for r in requests):
                    missing.append(method + " " + variant)
    if missing:
        raise SystemExit("Missing current Gateway route examples: " + ", ".join(missing))
    print("Every current Gateway user-route method/variant has a Postman example.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    artifacts = build()
    check_gateway_coverage(artifacts)
    for name, data in artifacts.items():
        text = json.dumps(data, indent=2, ensure_ascii=True) + "\n"
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if stale:
        raise SystemExit("Regenerate Postman collections: " + ", ".join(stale))
    print("Lab 4 Postman artifacts " + ("are current." if args.check else "generated."))


if __name__ == "__main__":
    main()
