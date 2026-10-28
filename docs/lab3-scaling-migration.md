# Lab 3 — scaling a service up behind Discovery Service

What each service owner changes so the whole team meets Lab 3 ("Scaling Up"). Discovery Service,
the gateway's round-robin load balancer, Exam Service and World Service are done
(`artflow/discovery-service`, `artflow/gateway`, `artflow/exam-service`, `artflow/world-service`,
all `3.0.0`) and can be used as a reference. The rules are in the CPR README,
*Discovery Service (Lab 3)*.

| Grade | What your service needs | Reference |
| --- | --- | --- |
| 3 — status endpoint and logs | `GET /api/v1/status` in the team format; explicit `key=value` logs | Exam: `src/status/status.controller.ts`, `src/common/logging/` |
| 5 — dynamic instances | Register on startup, heartbeat, deregister on shutdown; `deploy.replicas` in compose | Exam: `src/discovery/discovery-registration.service.ts` |
| 6 — instance-agnostic logic | No per-instance state: shared database, `SKIP LOCKED` workers, migration lock | Exam: `src/database/migration-runner.ts`, `src/integrations/outbox/` |
| CI | Releases are `3.x.y`: set `LAB_NUMBER: "3"` in the workflow, bump the version | Exam: `.github/workflows/ci.yml` |

Until a service registers, the gateway keeps sending its requests to the fixed address
`http://<service>:<port>`, so nothing breaks while you migrate.

## Who does what

| Owner | Services | To do |
| --- | --- | --- |
| Islam Abu Koush | Player, Game | Status, logs, registration for both. **Game:** its WebSocket sessions and timers live in memory, so either keep Game at one instance (and say why), or share lobby events across instances (PostgreSQL `LISTEN/NOTIFY`, Redis pub/sub) and make the gateway's WebSocket negotiation hand out the URL of the instance that holds the lobby. **Player:** every replica must use the same signing key (the volume already shares it; make key creation safe when two replicas start together) |
| Roenco Maxim | Zombie, Resource | Status, logs, registration for both. Check that thefts, kills and resource transactions rely only on database locks or unique keys, never on memory |
| Gancear Nichita | Base, Crafting | Status, logs, registration for both. Check that crafting sagas and Kiki timers keep no state in memory; move timers to the database (`next_run_at` + `SKIP LOCKED`) if they do |

## 1. Status endpoint

`GET /api/v1/status`, no token, exempt from your concurrency limit, always `200`:

```json
{
  "service": "zombie-service",
  "instance_id": "zombie-service-3f2a9c1b7d4e",
  "version": "3.0.0",
  "status": "ok",
  "started_at": "2026-10-28T15:00:00Z",
  "uptime_seconds": 3600.2,
  "load": { "in_flight": 4, "max_concurrent": 100, "utilization": 0.04, "requests_total": 9120 },
  "checks": { "database": { "status": "ok", "latency_ms": 0.9 } },
  "discovery": { "status": "registered", "url": "http://172.20.0.5:8005" },
  "process": { "memory_rss_mb": 21.4 }
}
```

`load` must be the numbers your concurrency limit acts on; Discovery Service will read it to detect
critical load (grade 7). `status` is `degraded` when the database does not answer.

## 2. Logs

One line per event, `event=<name> key=value …`. At least: `started` (configuration, never secrets),
`request` (method, path, status, duration_ms, caller from `X-Auth-Kind`/`X-Auth-Subject`,
request_id), every call to another service (target, url, status, duration_ms), registration events,
limits hit and every failure with its reason. Probes at debug level. Keep `X-Request-Id`.

## 3. Registration (Go)

Your service tokens already exist: mint one with `aud: "discovery-service"` and your own service
name as `sub`. Compose passes `DISCOVERY_SERVICE_URL=http://discovery-service:8500` to every service.

```go
// Registration of this instance in Discovery Service (CPR → Discovery Service (Lab 3)).
// Start it after the HTTP server listens; call Deregister from the SIGTERM handler.
type Registration struct {
	base, id, url, service, version string
	token                           func(audience string) string
	interval                        time.Duration
}

func NewRegistration(service, port, version string, token func(string) string) *Registration {
	base := strings.TrimRight(os.Getenv("DISCOVERY_SERVICE_URL"), "/")
	if base == "" {
		return nil // discovery off: the gateway uses the fixed URL
	}
	host, _ := os.Hostname()
	return &Registration{base: base, id: service + "-" + host, url: "http://" + ownIP() + ":" + port,
		service: service, version: version, token: token, interval: 5 * time.Second}
}

func (r *Registration) call(ctx context.Context, method, path string, body any) (*http.Response, error) {
	var payload io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		payload = bytes.NewReader(b)
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, method, r.base+path, payload)
	req.Header.Set("Authorization", "Bearer "+r.token("discovery-service"))
	req.Header.Set("Content-Type", "application/json")
	return http.DefaultClient.Do(req)
}

func (r *Registration) register(ctx context.Context) {
	for delay := time.Second; ctx.Err() == nil; delay = min(delay*2, 10*time.Second) {
		res, err := r.call(ctx, http.MethodPost, "/api/v1/instances", map[string]any{
			"service": r.service, "instance_id": r.id, "url": r.url, "version": r.version})
		if err == nil && res.StatusCode < 300 {
			var answer struct{ HeartbeatIntervalSeconds float64 `json:"heartbeat_interval_seconds"` }
			_ = json.NewDecoder(res.Body).Decode(&answer)
			res.Body.Close()
			if answer.HeartbeatIntervalSeconds > 0 {
				r.interval = time.Duration(answer.HeartbeatIntervalSeconds * float64(time.Second))
			}
			slog.Info("registered", "event", "registered", "instance", r.id, "url", r.url)
			return
		}
		slog.Warn("registration failed", "event", "registration_failed", "instance", r.id, "error", err, "retry_in", delay)
		time.Sleep(delay)
	}
}

// Run registers, then sends heartbeats until ctx is cancelled.
func (r *Registration) Run(ctx context.Context) {
	r.register(ctx)
	ticker := time.NewTicker(r.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			res, err := r.call(ctx, http.MethodPut, "/api/v1/instances/"+r.id+"/heartbeat", nil)
			if err == nil && res.StatusCode == http.StatusNotFound {
				slog.Warn("re-registering", "event", "heartbeat_rejected", "instance", r.id)
				r.register(ctx)
			}
			if res != nil {
				res.Body.Close()
			}
		}
	}
}

func (r *Registration) Deregister() {
	if res, err := r.call(context.Background(), http.MethodDelete, "/api/v1/instances/"+r.id, nil); err == nil {
		res.Body.Close()
		slog.Info("deregistered", "event", "deregistered", "instance", r.id)
	}
}

// ownIP is this container's address on the Docker network.
func ownIP() string {
	addrs, _ := net.InterfaceAddrs()
	for _, a := range addrs {
		if ip, ok := a.(*net.IPNet); ok && !ip.IP.IsLoopback() && ip.IP.To4() != nil {
			return ip.IP.String()
		}
	}
	return "127.0.0.1"
}
```

TypeScript owners: copy `src/discovery/discovery-registration.service.ts` from exam-service and
call `start(port)` after `app.listen`, with `app.enableShutdownHooks()` so `docker stop` deregisters.

## 4. Instance-agnostic logic

Any instance may get any request:

- Keep state in the database: idempotency keys, processed event ids, saga steps, timers.
- Background workers claim work with `SELECT … FOR UPDATE SKIP LOCKED`.
- Run migrations under `SELECT pg_advisory_lock(hashtext('<service>:migrations'))`, so replicas
  starting together on a fresh database do not collide (see Exam's `migration-runner.ts`).
- In-memory caches are fine only if they are pure (rebuilt from the database) and short-lived.

## 5. Compose and release

When your service registers, add to its block in `deploy/docker-compose.yml`:

```yaml
    deploy:
      replicas: ${ZOMBIE_REPLICAS:-3}
    depends_on:
      discovery-service:
        condition: service_started
```

remove any `container_name` and published `ports` from it, add `ZOMBIE_REPLICAS=3` to
`deploy/.env.example`, and publish a `3.x.y` release through CI (`LAB_NUMBER: "3"`).

Verify:

```bash
docker compose up -d
curl -s localhost:8080/gateway/instances            # your instances, in rotation order
for i in 1 2 3 4; do curl -s -o /dev/null -D - localhost:8080/api/v1/zombie-types -H "Authorization: Bearer $T" | grep -i x-upstream-instance; done
docker compose up -d --scale zombie-service=2        # one instance deregisters, traffic continues
```
