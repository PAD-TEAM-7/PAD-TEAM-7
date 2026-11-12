# Lab 4 Postman demonstrations

All eight domain collections now target **Gateway `28080`** and the current Lab 4
user API. They use real Player login, real IDs and real dependencies. Internal
service commands are **gRPC through Broker `28600`**, not REST through Gateway.
Requests labelled “removed: 404” demonstrate that those old routes are closed.

## Run in order

From the CPR root, start Docker Desktop and then:

```powershell
docker compose -f deploy/docker-compose.lab4.yml up -d --no-build --pull missing
python -m pip install -r scripts/requirements-lab4.txt
python scripts/lab4-prepare-postman.py
```

If services are still starting, wait for their databases/registrations and retry
preparation. It checks every service through real gRPC before creating data.
On this workstation the local `START_LAB4_DEMO_PRIVATE.ps1` also starts Docker,
waits for the replicas and prepares the private environment.

1. Import `lab4.postman_collection.json` for the complete team run, **or** the
   individual `<service>.postman_collection.json` you want to present.
2. Import the **private environment path printed by preparation**. Select
   **PAD Lab 4 LOCAL DEMO**. The committed environment is only an empty template.
3. Run the complete collection/folder **top to bottom**, one iteration, with
   200–1000 ms delay. Run each individual collection against freshly prepared
   fixtures. The combined collection runs all service folders in sequence.
4. Preparation creates two demo accounts, a real lobby/World/Base, real Resource
   balances, a damaged barricade and an active exam. It does not alter database
   rows directly, bypass authentication or use development token routes.
5. Before another run, rerun preparation and **reimport** the environment: exam
   submission, base upgrades, repairs and builds change real state. Active exams
   also expire; prepare shortly before presenting.

Successful creation captures new IDs automatically. Login/refresh replaces
environment tokens. Do not create same-named global/collection variables or
manually replace IDs. Keep the environment private: it contains demo passwords,
refresh/access tokens and operator credentials. Never commit it or share it.

For ZIP logs choose **Send and Download** on Broker request 05. Dead letters can
legitimately be empty; use the controlled operations smoke below to produce one.

## What each collection demonstrates

| Collection | Current working flow |
| --- | --- |
| Player | Register/login/refresh, JWKS, profile, inventory, presence, friends, authentication failures |
| Game | Lobby creation with World/Base dependencies, join/leave, real gathering action, trade/decline, direct WebSocket negotiation |
| Exam | Topic-driven enrolment, prepared exam, answer/submit/replay, progress, achievements, diploma, ownership protection |
| World | Real generated map, wings, rooms, room details; internal mutations removed from Gateway |
| Zombie | Public type catalogue/details; spawn/damage/kill/theft use gRPC |
| Resource | Real lobby/player balances; pools and transactions use gRPC |
| Base | State/Kiki, upgrade/replay, workbench, barricade construction and real repair/material consumption |
| Crafting | Catalogue/detail/preview, real craft/replay, job/history, ownership protection; availability regression below |
| Gateway | Health, subscriber dispatch, removed prefixes, events and development/management routes |
| Message Broker | Topics, durable queue counts, dead-letter metadata, ZIP logs, metrics, operator auth, rejected unknown replay/restore |
| Service Discovery | Registered gRPC replicas/load/topics, subscription list, eligible selection, operator auth |

For a successful action cancellation: send Game request 05, then immediately send
`DELETE {{gateway}}/api/v1/actions/{{actionId}}` with the player bearer token;
expect `204` before the accelerated action finishes (normally six seconds).
The automatic run uses a deterministic missing-action conflict (`409`) instead.
Kiki state is inside `GET /api/v1/bases/{baseId}`; this release has no separate
GET Kiki handler. Facility creation returns `200`, not `201`.

## Known integration failure detected by this suite

**Crafting request 04, available recipes, expects `200` and currently fails.**
Its unlock evaluation calls Player's player-only profile as a service (`403`),
and calls World `/api/v1/wings?lobby_id=...` (`404`). World currently exposes
`/api/v1/worlds/{lobbyId}/wings` with player authentication. Fix the Crafting
clients/delegated read contracts in a service release; do not loosen auth or
change this test to accept failure. The other Crafting flow verifies an ungated
recipe with actual Resource consumption and Player output. This collection
update alone does not establish a full-mark result.

## Native gRPC and grades 6–10

See [grpc/README.md](grpc/README.md) and import the shared protobuf. All services
have current gRPC request examples; the REST collections do not pretend a removed
service route is a working user request.

These existing acceptance clients demonstrate things a REST collection cannot
prove by itself. They create controlled fixtures; the last two briefly interrupt
the local Lab 4 stack and retain volumes:

```powershell
python scripts/lab4-team-smoke.py          # real eight-service business flow
python scripts/lab4-operations-smoke.py    # load routing, failures, dead letters/replay, log export
python scripts/lab4-availability-smoke.py  # real business failover/scaling
python scripts/lab4-durability-smoke.py    # accepted work survives Broker restart
```

## Execute actual Postman assertions from the terminal

```powershell
npm install --prefix .git/lab4-newman --no-audit --no-fund newman@6.2.1
python scripts/lab4-prepare-postman.py
node scripts/lab4-postman-run.cjs "$env:TEMP\PAD-LAB4-LOCAL.postman_environment.json"
# Or pass postman/world-service.postman_collection.json as the final argument.
```

The runner prints request names/statuses and assertion names, never credentials
or response bodies. It exits nonzero for failing assertions, including the known
Crafting availability issue. It does not export the rotated environment; use the
Postman desktop runner when you want to keep using its updated variables.

Regenerate collections with `python scripts/generate-lab4-postman.py` and check
them with `python scripts/generate-lab4-postman.py --check`. This updates the
individual files and combined suite together.
