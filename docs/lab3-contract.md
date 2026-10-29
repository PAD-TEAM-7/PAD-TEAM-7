# Lab 3 communication contract

Scope: Player, Game, Zombie, Resource, Gateway and Service Discovery. Existing business payloads remain v1-compatible.
See the [mentor guide](lab3-mentor-guide.md) for runnable examples and limitations.

## Domain status and instance identity

Player, Game, Zombie and Resource expose public `GET /api/v1/status`, also reachable through their Gateway prefix.

```json
{"status":"ok","service":"player-service","instance_id":"container-id","in_flight":2,"capacity":100,"load":0.02}
```

Status is 200 when the owning database is reachable and 503 when it is not. Status probes bypass
the business-request concurrency limit. Business responses identify the responding instance in
`X-Service-Instance`. The Gateway adds `X-Failure-Id`, a unique internal attempt ID used to
deduplicate circuit reports; reusing a client `X-Request-Id` cannot suppress failure counting.
Gateway and Discovery also expose public `/api/v1/status` for their own liveness.

## Discovery API (port 8500 internally)

Every `/v1` endpoint requires a shared `DISCOVERY_TOKEN` bearer token, at least 32 characters.
Only trusted internal services and operators receive it. Do not put it in frontend clients.

| Endpoint | Contract |
| --- | --- |
| `POST /v1/instances` | JSON `service`, unique `instance_id`, internal `url`, optional public `ws_url`, positive `task_timeout` in seconds. Creates/renews a 30-second lease. Returns instance state. |
| `GET /v1/instances` | `{"instances":[...]}`; each entry contains registration fields plus boolean `healthy`, `lease_valid`, `removed`. |
| `GET /v1/select/{service}` | Returns an eligible instance by round robin. Optional `affinity` query parameter establishes/reuses a persistent owner. No eligible owner/instance returns 503. |
| `POST /v1/instances/{id}/failures` | JSON `event_id` and optional `reason`. Duplicate events count once. Returns `{"removed":true/false}`. |
| `DELETE /v1/instances/{id}` | Expires the instance lease and marks it unhealthy. Historical state and quarantine are retained. |
| `POST /v1/instances/{id}/restore` | Performs a fresh probe; only a healthy instance with a live lease can be restored. Returns its state; otherwise 409. |

Unknown IDs return 404; invalid credentials 401; malformed registration 422; reusing an ID for
another service 409. `/health` and `/api/v1/status` are public liveness probes.

Eligibility requires `healthy && lease_valid && !removed`. Three failures in the rolling window
`task_timeout * 3.5` remove the instance. HTTP 5xx and transport failures count; ordinary client
4xx responses do not. Active failed health checks count as well. Heartbeat registration cannot
clear removal. The Gateway and Game caller share failure IDs to avoid double-counting one request.

## Routing and state

Set `DISCOVERY_URL`, `DISCOVERY_TOKEN` and `DISCOVERY_SERVICES=player-service,game-service` on the
Gateway. The extended deployment adds `zombie-service,resource-service` to that managed set.
Managed services have no static fallback. Selection failure returns the existing error
envelope with HTTP 503 and code `NO_HEALTHY_INSTANCE`.

For a Game route containing `/lobbies/{id}`, the affinity key is the lobby ID. WebSocket
negotiation uses the same key and returns the selected instance's registered `ws_url` plus the
existing lobby path and player token. The response shape remains unchanged. Game instances need
distinct public ports/addresses. A failed owner is not automatically reassigned.

Unmanaged teammate services keep the Lab 2 route contract. They can adopt this protocol later:
implement status, register/renew/deregister, wrap outbound calls and add their names to the
Gateway's managed set only when their deployed instances actually register.

Zombie and Resource use round robin without sticky routing because domain state is shared in
their own PostgreSQL databases. Zombie additionally holds a database advisory lock for the entire
same-zombie mutation, including external calls. This coordinates steal/damage/kill/despawn and
event cleanup across replicas. Resource's existing row and idempotency-key locks coordinate stock.
Lobby-finished Zombie cleanup now returns stolen inventory before despawn, under that same lock.
