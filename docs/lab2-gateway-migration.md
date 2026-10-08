# Lab 2 — moving a service behind the API gateway

What each service owner changes so the whole team meets Lab 2. The gateway side is done
(`artflow/gateway`, see its README and *API Gateway (Lab 2)* in the CPR README). Exam and World
Service already follow every step below and can be used as a reference.

| Grade | What your service needs | Status |
| --- | --- | --- |
| 6 — all REST through the gateway | Read `<NAME>_SERVICE_URL` and append paths to it | Compose already points every URL at the gateway |
| 7 — WebSocket negotiation | Game only: keep validating `?token=` on upgrade | Gateway hands out Game's URL |
| 8 — task timeout + concurrency limit | Your own limits, with the codes below | Each owner |
| 9 — CI to DockerHub | A workflow that pushes `<version>` and `latest` on merge to `main` | Each owner |
| 10 — authorization at the gateway | Trust the signed `X-Auth-*` identity, ignore `Authorization` | Each owner, then leave `GATEWAY_LEGACY_AUTH_SERVICES` |

## Who does what

The gateway, Exam and World are done (`artflow/gateway:2.1.0`, `artflow/exam-service:2.0.0`,
`artflow/world-service:2.0.0`). Each other owner does the same for their two services:

| Owner | Services | To do |
| --- | --- | --- |
| Islam Abu Koush | Player, Game | 8, 9, 10 below for both. Game: merge the gateway-prefix fix (game-service#4), sign service tokens with `aud: "<name>-service"`, answer `ping` with `pong`. Player: `POST /api/v1/events`, and `404` instead of `500` on `/xp` for a bad id |
| Roenco Maxim | Zombie, Resource | 8, 9, 10 below for both |
| Gancear Nichita | Base, Crafting | 8, 9, 10 below for both. Check that the TypeScript clients append paths to the base URL (section 6) |

Release order for each service, so the stack keeps working at every step:

1. Add the limits (8) and the gateway identity (10) to the service. Keep accepting the token as well
   if you like: the gateway already sends the signed identity to every service, including the ones
   still in `GATEWAY_LEGACY_AUTH_SERVICES`.
2. Merge to `main`: CI (9) publishes `<owner>/<service>:2.0.0` and `latest`.
3. One CPR PR: set your `<SERVICE>_VERSION` to `2.0.0` in `deploy/.env.example` and remove your
   service from `GATEWAY_LEGACY_AUTH_SERVICES`. Grade 10 is complete for the team when that list is
   empty.

Two things that silently break grade 6:

- **An old Lab 1 `.env`.** It sets `PLAYER_SERVICE_URL=http://player-service:8001` and the like,
  and a value in `.env` overrides the gateway default in compose: the services then bypass the
  gateway without any error. Start again from `.env.example`, which leaves those lines commented.
- **Postman on `localhost:8001`–`8008`.** Those ports are no longer published. Use
  `http://localhost:8080/<service>-service/...` or the contract paths on `http://localhost:8080`.

## 6. Calling other services through the gateway

`deploy/docker-compose.yml` sets every service URL to the gateway, e.g.
`RESOURCE_SERVICE_URL=http://gateway:8080/resource-service`. Your client must **append** the path:

```go
url := strings.TrimRight(os.Getenv("RESOURCE_SERVICE_URL"), "/") + "/api/v1/pools"   // Go
```

```ts
const url = `${base.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;  // TypeScript, not new URL(path, base)
```

Fetch Player's keys through the gateway too: `PLAYER_JWKS_URL=http://gateway:8080/api/v1/auth/jwks`.

## 8. Task timeout and concurrency limit

Answer in the contract envelope:

| Situation | Status | `code` | Extra |
| --- | --- | --- | --- |
| More requests in progress than your limit | `429` | `CONCURRENCY_LIMIT_REACHED` | `Retry-After: 1` |
| A request runs longer than your task timeout | `504` | `TASK_TIMEOUT` | — |

Keep `/api/v1/health` and `/api/v1/ready` exempt. In Go, a `http.TimeoutHandler` plus a buffered
channel used as a semaphore does both. Also bound your SQL (`SET statement_timeout`, or a
`context.WithTimeout` on queries) so abandoned work actually stops.

## 9. CI

Copy `.github/workflows/ci.yml` from exam-service (Node) or the gateway (Python) and adapt the build
steps (`go test ./...`, `go build`). It reads the version, fails unless it starts with the lab number
(`2.`), refuses to overwrite a published version, and pushes `<version>` and `latest` for
`linux/amd64` and `linux/arm64`. Add the repository secrets `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN`.

## 10. Trusting the gateway identity

The gateway verifies the token and sends, instead of `Authorization`:
`X-Auth-Kind`, `X-Auth-Subject`, `X-Auth-Roles`, `X-Auth-Username`, `X-Auth-Timestamp`,
`X-Auth-Signature`. Verify the signature, then use kind/subject/roles exactly as you used the
token claims before (players on player endpoints, `403` to a player on service endpoints).

```go
// Go: returns the caller, or an error -> 401 UNAUTHENTICATED.
func GatewayIdentity(r *http.Request, secret []byte) (kind, subject string, roles []string, err error) {
	h := r.Header
	kind, subject = h.Get("X-Auth-Kind"), h.Get("X-Auth-Subject")
	ts := h.Get("X-Auth-Timestamp")
	canonical := strings.Join([]string{"v1", kind, subject, h.Get("X-Auth-Roles"),
		h.Get("X-Auth-Username"), ts, h.Get("X-Request-Id")}, "\n")
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(canonical))
	got, _ := hex.DecodeString(h.Get("X-Auth-Signature"))
	if kind == "" || subject == "" || !hmac.Equal(got, mac.Sum(nil)) {
		return "", "", nil, errors.New("requests must come through the API gateway")
	}
	sec, _ := strconv.ParseInt(ts, 10, 64)
	if d := time.Since(time.Unix(sec, 0)); d > time.Minute || d < -time.Minute {
		return "", "", nil, errors.New("gateway identity expired")
	}
	if r := h.Get("X-Auth-Roles"); r != "" {
		roles = strings.Split(r, ",")
	}
	return kind, subject, roles, nil
}
```

The TypeScript version is `src/auth/gateway-identity.verifier.ts` in exam-service. Read
`GATEWAY_IDENTITY_SECRET` from the environment (compose already passes it). When your service trusts
the identity, remove it from `GATEWAY_LEGACY_AUTH_SERVICES` in `deploy/.env.example`. From then on the
gateway stops forwarding your `Authorization` header.

## Integration findings from testing the whole stack

Found by running all eight services behind the gateway. None of them blocks the stack today:

| Service | Finding | Fix |
| --- | --- | --- |
| Game | Service tokens carry `aud: "world"` (and likely `"exam"`, `"base"`), the contract says `"world-service"` | Use `<name>-service`. The gateway and Exam/World accept both for now |
| Player, Game | `POST /api/v1/events` answers `404`, so `ExamPassed`, `AchievementUnlocked` and `WingUnlocked` are parked by the senders | Add the events webhook from the *Event catalogue* |
| Player | `POST /api/v1/players/{id}/xp` answers `500` for an id that is not a UUID | Answer `404 PLAYER_NOT_FOUND`: the sender retries `5xx` but not `4xx` |
| Game | The WebSocket does not answer `{"type":"ping"}` with `pong` (contract: *Heartbeat*) | Reply `{"type":"pong"}` |
