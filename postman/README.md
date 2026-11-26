# Lab 5 Postman demonstrations

The eight individual domain collections use Gateway on port `28080` for user
requests and Discovery on `28500` for replica evidence. Gateway, Message
Broker and Service Discovery have separate collections. Internal service
commands use Broker gRPC on `28600`. Requests labelled “removed: 404” verify
that the old Gateway routes are closed.

## Run in order

From the CPR root, start Docker Desktop and then:

```powershell
$lab5 = @('-p', 'pad-lab5-demo', '-f', 'deploy/docker-compose.lab4.yml',
  '-f', 'deploy/docker-compose.lab5-cache.yml', '-f', 'deploy/docker-compose.lab5-sagas.yml',
  '-f', 'deploy/docker-compose.lab5-release.yml')
# Set LAB5_CACHE_PASSWORD in deploy/.env before starting.
docker compose @lab5 up -d --no-build --pull missing
python -m pip install -r scripts/requirements-lab4.txt
python scripts/lab4-prepare-postman.py
```

Stop a Compose project already using the same host ports, or use another port
set and pass the corresponding `--gateway`, `--broker`, `--broker-grpc`
and `--discovery` values to the preparation script. Run `--help` for options.
Preparation checks all eight services through gRPC before creating fixtures.
If services are still registering, wait until they are healthy and retry.
For 2PC use the [two-phase setup](../README.md#crafting-two-phase-commit) and
prepare with `--craft-protocol 2pc`.

1. Import `lab5.postman_collection.json` for the complete team run, or the
   individual `<service>.postman_collection.json` you want to present.
2. Import the **private environment path printed by preparation**. Select
   **PAD Lab 5 LOCAL DEMO**. The committed environment is only an empty template.
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
| Exam | Topic-driven enrolment, prepared exam, answer/submit/replay, progress, ownership protection and course-catalogue cache hit |
| World | Real generated map, wings, rooms, room details; internal mutations removed from Gateway |
| Zombie | Public type catalogue/details and catalogue cache hit. Spawn, damage, kill and theft use gRPC |
| Resource | Real lobby/player balances; pools and transactions use gRPC |
| Base | State/Kiki, upgrade/replay, workbench, barricade construction and real repair/material consumption |
| Crafting | Catalogue cache, preview, real craft/replay, job/history, ownership protection and same-job correlation in Broker |
| Gateway | Health, subscriber dispatch, same-shard catalogue cache hit and legacy-route rejection |
| Message Broker | Topics, queues, dead letters, ZIP logs, metrics, operator authorization and Lab 5 settlement journal |
| Service Discovery | Registered gRPC replicas/load/topics, subscription list, eligible selection, operator auth |

For a successful action cancellation: send Game request 05, then immediately send
`DELETE {{gateway}}/api/v1/actions/{{actionId}}` with the player bearer token;
expect `204` before the accelerated action finishes (normally six seconds).
The automatic run uses a deterministic missing-action conflict (`409`) instead.
Kiki state is inside `GET /api/v1/bases/{baseId}`; this release has no separate
GET Kiki handler. Facility creation returns `200`, not `201`.

The eight domain folders each validate two eligible workers, their gRPC
registrations and their command topic. This checks application workers.
Use the [database failover checks](../README.md#postgresql-replication-and-failover)
to demonstrate PostgreSQL replication and promotion.

## Crafting availability diagnostic

Run [the separate availability diagnostic](lab5-crafting-diagnostics.postman_collection.json)
against fresh fixtures to check `GET /api/v1/recipes/available`. The public
contract expects `200`. The released service returned `403` in the live check.
The diagnostic retains the `200` assertion. The main Lab 5 collection verifies
the working catalogue, preview, craft and replay paths.

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

The runner defaults to the combined Lab 5 collection and exits nonzero for
failing assertions. It prints request names, statuses and assertion names.
Use the Postman desktop runner if you want to retain rotated environment values.

Regenerate collections with `python scripts/generate-lab4-postman.py` and check
them with `python scripts/generate-lab4-postman.py --check`. This updates the
individual files and combined suite together.
