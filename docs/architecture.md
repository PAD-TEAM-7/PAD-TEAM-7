# Lab 5 architecture

The released stack layers [Lab 5 Compose overlays](../README.md#lab-5-warehouse-and-etl) over [the Lab 4 runtime](../deploy/docker-compose.lab4.yml). The diagrams show the release with caching, database high availability, analytics and Broker settlement enabled. The two-phase commit overlay selects the settlement protocol for new crafts.

## Runtime topology

```mermaid
flowchart TB
    accTitle: Lab 5 runtime topology
    accDescr: Client traffic reaches Gateway and Game. Broker routes domain requests through eight registered service groups.
    Client["Game client / Postman"]
    Operator["Local operator"]
    subgraph Entry["Request routing"]
        Gateway["Gateway · HTTP 8080"]
        Broker["Message Broker · gRPC 8600 / HTTP 8601"]
        Discovery["Service Discovery · HTTP 8500"]
    end
    subgraph Workers["Domain services · two application instances per service"]
        direction LR
        Player["Player · HTTP 8001"]
        Game["Game · HTTP / WebSocket 8002"]
        Exam["Exam · HTTP 8003"]
        World["World · HTTP 8004"]
        Zombie["Zombie · HTTP 8005"]
        Resource["Resource · HTTP 8006"]
        Base["Base · HTTP 8007"]
        Crafting["Crafting · HTTP 8008"]
    end
    Client -->|"REST · default host port 28080"| Gateway
    Client -->|"Direct WebSocket · ports 28002 / 28012"| Game
    Gateway -->|"gRPC Dispatch · authenticated user operations"| Broker
    Gateway -.->|"JWKS over HTTP"| Player
    Gateway -.->|"WebSocket owner lookup"| Discovery
    Broker -.->|"JWKS over HTTP"| Player
    Broker -.->|"HTTP registry / eligibility / failure reports"| Discovery
    Workers -.->|"HTTP registration and lease refresh"| Discovery
    Discovery -.->|"HTTP status probes"| Workers
    Broker <-->|commands.player| Player
    Broker <-->|commands.game| Game
    Broker <-->|commands.exam| Exam
    Broker <-->|commands.world| World
    Broker <-->|commands.zombie| Zombie
    Broker <-->|commands.resource| Resource
    Broker <-->|commands.base| Base
    Broker <-->|commands.crafting| Crafting
    Operator -->|"HTTP operations · loopback 28601"| Broker
    Operator -->|"HTTP registry · loopback 28500"| Discovery
```

Each worker link represents the shared gRPC contract in [`broker.proto`](../contracts/broker/v1/broker.proto). Workers make request/reply calls with `Call` and publish events with `Publish`. Broker invokes worker handlers with `Execute` and delivers subscriber events with `Deliver`. The port shown inside each worker is its HTTP listener. Each worker registered a private gRPC listener on port `9000` in the published deployment. The host exposes Game's two HTTP ports for direct WebSocket sessions. Broker's gRPC port is available on host loopback `28600` for development and demos.

| Service | Owns | PostgreSQL database | Router port with HA |
| --- | --- | --- | --- |
| Player | Accounts, player tokens and signing keys, inventory, XP, friends and presence | `player_db` | 15432 |
| Game | Lobbies, action timers, trades and WebSocket stream ownership | `game_db` | 15433 |
| Exam | Course enrolment, exam attempts, grades and achievements | `exam_db` | 15434 |
| World | Lobby maps, rooms, corridors and wing unlock state | `world_db` | 15435 |
| Zombie | Zombie catalogue and lobby-scoped zombie instances | `zombie_db` | 15436 |
| Resource | Lobby balances, stock pools and resource transaction ledger | `resource_db` | 15437 |
| Base | Lobby base, facilities, barricades and Kiki state | `base_db` | 15438 |
| Crafting | Recipe catalogue, craft jobs and their outbox | `crafting_db` | 15439 |

## Storage, cache and operations

```mermaid
flowchart TB
    accTitle: Lab 5 data and operations topology
    accDescr: Redis shards cache selected reads. Eight PostgreSQL writers replicate to standbys and feed a shared warehouse job.
    subgraph Cache["Gateway cache · consistent-hash ring"]
        Gateway["Gateway"]
        Redis1[("cache · Redis 6379")]
        Redis2[("cache-2 · Redis 6379")]
        Redis3[("cache-3 · Redis 6379")]
        Gateway --> Redis1
        Gateway --> Redis2
        Gateway --> Redis3
    end
    subgraph Persistence["Eight independent PostgreSQL clusters with the HA overlay"]
        Domain["Each service's two workers"]
        Router["db-router · TCP ports 15432–15439"]
        Writer[("One writer per service")]
        Standby1[("Standby 1 per service")]
        Standby2[("Standby 2 per service")]
        Domain -->|"Own database only"| Router
        Router -->|"Route new sessions to current writer"| Writer
        Writer -->|"Physical WAL streaming"| Standby1
        Writer -->|"Physical WAL streaming"| Standby2
    end
    ETL["warehouse-etl · one-shot job"] -->|"Eight read-only count queries"| Router
    ETL -->|"One atomic snapshot batch"| Warehouse[("warehouse · pad_warehouse")]
    Operator["Operator"] -.->|"Fence old writer and promote selected standby"| Persistence
    Operator -.->|"Run one-shot installer in 2PC mode"| Installer["two-phase-install"]
    Installer -->|"Install participant SQL in Player and Resource"| Router
    Broker["Message Broker"] --> BrokerDB[("Broker SQLite · queues, dead letters, settlement journal")]
    Broker -.->|"Optional 2PC · Player and Resource only"| Router
    Discovery["Service Discovery"] --> Registry[("Discovery SQLite · registry state")]
    Discovery -.->|"SMTP alerts when configured"| Mail["Developer inbox"]
    WorkersLogs["Domain workers, Gateway, Broker and Discovery"] --> Logs[("Shared system_logs volume · per-instance files")]
    Init["log-volume-init · one-shot job"] --> Logs
    Broker -->|"Collect ZIP for authenticated log download"| Logs
    Prometheus["Prometheus · host loopback 29090"] -->|"Scrape /metrics on Broker 8601"| Broker
    Grafana["Grafana · host loopback 23000"] -->|"Query metrics"| Prometheus
```

Gateway caches selected authenticated catalogue GETs for 30 seconds and Player public signing keys for 60 seconds. A cache miss goes to the original source. A shard failure falls back to the original source for its keys. The Redis nodes are standalone and the ring lives in Gateway.

The [HA overlay](../deploy/docker-compose.lab5-ha.yml) supplies each service with its own writer route. The two standbys replicate asynchronously. Failover is operator-led, and the old writer must be fenced before promotion. The [ETL job](../deploy/warehouse/extract.sh) reads one table count per service and writes eight aggregate rows in one warehouse transaction. Source reads are independent. With the base analytics overlay alone, ETL connects directly to each service database. With HA, it connects through the router.

## Example event flow

These subscriptions come from the worker registrations in the published stack,
as returned by Discovery `GET /v1/instances`.

| Service | Registered event topics, excluding the `events.` prefix |
| --- | --- |
| Player | `AchievementUnlocked`, `ExamPassed`, `ItemCrafted`, `ZombieKilled` |
| Game | `WingUnlocked` |
| Exam | `PlayerRegistered` |
| World | `ExamPassed`, `LobbyCreated` |
| Zombie | `CycleChanged`, `LobbyFinished` |
| Resource | `LobbyFinished` |
| Base | `LobbyCreated`, `LobbyFinished` |
| Crafting | `ExamPassed`, `PlayerLeveledUp`, `WingUnlocked` |

```mermaid
sequenceDiagram
    participant E as Exam
    participant B as Message Broker
    participant W as World
    participant R as Resource
    participant G as Game
    participant C as Crafting
    participant P as Player
    E->>B: Publish ExamPassed from Exam outbox
    B->>W: Deliver ExamPassed
    B->>C: Deliver ExamPassed
    B->>P: Deliver ExamPassed
    Note over W: Unlock the course wing in the lobby
    W->>B: Call commands.resource to create pools
    B->>R: Execute pool creation
    W->>B: Publish WingUnlocked from World outbox
    B->>G: Deliver WingUnlocked
    B->>C: Deliver WingUnlocked
```

The two World outbox outputs are independently dispatched. The drawing shows their relationship to the wing unlock. Broker queues events per subscriber service and may retry delivery. Consumers use event IDs to deduplicate.

## Crafting settlement

```mermaid
sequenceDiagram
    participant C as Crafting
    participant B as Message Broker
    participant R as Resource
    participant P as Player
    participant RD as Resource PostgreSQL
    participant PD as Player PostgreSQL
    C->>B: Call Crafting settlement with job ID
    Note over B: Persist settlement before execution
    alt Saga overlay
        B->>R: Consume materials through Resource worker
        B->>P: Grant inventory through Player worker
        opt Definitive grant rejection
            B->>R: Refund confirmed consumption
        end
    else Two-phase overlay
        B->>RD: Prepare deduction through db-router
        B->>PD: Prepare grant through db-router
        Note over B: Persist commit if both prepare, otherwise persist abort
        B->>RD: Resolve prepared Resource transaction
        B->>PD: Resolve prepared Player transaction
    end
    B-->>C: Return terminal result when resolved
```

The saga retries ambiguous outcomes using stable step keys. Two-phase commit uses the restricted functions installed by `two-phase-install` and the Broker journal to resume a durable decision after interruption. An unfinished craft can return HTTP 202 and is readable by job ID. The [Postman walkthrough](../postman/README.md) correlates the job with Broker's operator view.

## Implementation references

- [Base deployment](../deploy/docker-compose.lab4.yml), [analytics](../deploy/docker-compose.lab5.yml), [replication](../deploy/docker-compose.lab5-ha.yml), [cache](../deploy/docker-compose.lab5-cache.yml) and [published images](../deploy/docker-compose.lab5-release.yml).
- [Saga configuration](../deploy/docker-compose.lab5-sagas.yml), [two-phase configuration](../deploy/docker-compose.lab5-2pc.yml), [participant SQL](../deploy/two-phase) and [monitoring configuration](../deploy/monitoring).
- [Public routes, payloads and errors](../README.md#communication-contract) and [native gRPC examples](../postman/grpc/README.md).

## Repository responsibility

Player/Game, Zombie/Resource, Base/Crafting and Exam/World are the assigned
private repositories. Gateway, Discovery and Broker are shared. See the
[repository access and review policy](../README.md#protection-rules) for ownership and release rules.
