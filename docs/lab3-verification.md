# Lab 3 verification and delivery

Scope: Player/Game and Maxim's Zombie/Resource domain services; shared Gateway, Discovery and deployment.
Exam, World, Base and Crafting source code is unchanged.

## Completed verification

| Check | Result |
| --- | --- |
| Gateway unit/integration suite | 77 passed |
| Discovery registry, API, monitoring and SMTP suite | 7 passed |
| Gateway and Discovery Ruff checks | Passed |
| Player `go test -race ./...` | Passed |
| Game `go test -race ./...` | Passed |
| Player domain coverage (repository CI command) | 82.9%, above the required 80% |
| Game domain coverage (repository CI command) | 82.0%, above the required 80% |
| Fresh Docker Compose startup with two Player and two Game instances | Passed |
| Round-robin headers on live Player requests | Passed |
| Register/login across different replicas | Passed |
| Shared JWT signing key and one-use refresh rotation | Passed |
| Persistent Game lobby routing and matching negotiated WebSocket origin | Passed |
| Direct Game WebSocket ping/pong and live PlayerJoined event | Passed against the selected lobby owner |
| Dynamic Player scaling from two to three replicas and back | Passed without Gateway restart |
| Zombie `go test -race -cover ./...` with PostgreSQL | Passed; 80.4% domain coverage |
| Resource `go test -race -cover ./...` with PostgreSQL | Passed; 80.1% domain coverage |
| Two Zombie and two Resource replicas, readiness and round robin | Passed |
| Resource pool/gather replay across different replicas | Passed; a single stock mutation |
| Real Zombie → Resource theft replay and despawn return | Passed |
| Simultaneous kills on two Zombie replicas | Passed; one success, one 422, real Player receives loot/XP once |
| Genuine HTTP failure removal for Zombie and Resource | Passed for each service through the real Gateway |
| Removal persists across heartbeat; explicit restoration | Passed for both new services |
| Dynamic Zombie and Resource scaling from two to three replicas and back | Passed without Gateway restart |
| Zombie and Resource GitHub CI | Both PostgreSQL/race/coverage and amd64/arm64 image-build jobs passed |
| Three controlled failure reports, removal across heartbeat, health-checked restore | Passed |
| Three real HTTP 500 responses across different endpoints through Gateway | Passed; faulty instance excluded afterward |
| Critical-load monitor alert during live fault demo | Generated |
| Actual SMTP message delivery to a local test SMTP receiver | Passed; cooldown suppressed duplicate message |

The live checks are reproducible with `python -u scripts/lab3-smoke.py --fault-demo` from
the CPR root. They create fresh demo records and clean up the temporary fault-injection server.
They do not delete database volumes. The injected extra replica is explicitly a test fixture.
Maxim's checks are reproduced with `python -u scripts/lab3-maxim-smoke.py --fault-demo`.
Both new CI workflows provide PostgreSQL, use race detection and enforce 80% domain coverage.
Player, Game, Gateway and Discovery GitHub CI checks have also completed successfully.

## External prerequisites

External SMTP credentials and a developer recipient were not configured during implementation.
The SMTP implementation is tested, but delivery to the developer's actual provider/inbox remains
to be demonstrated after setting the `SMTP_*` values in `deploy/.env`. Do not claim this external
delivery has happened merely because a local test passed.

Existing protected branches require pull requests and two approving reviews. Changes are pushed
on review branches; merging still follows the team's review rules. New Discovery repository
access includes `huntrese`, `IslamAbukoush`, `cyberArtik`, `MaxRoenco` and `Nick34567890`.
Discovery has `main` and `development`, with `development` as default. GitHub rejected enforced
branch protection for this private repository with HTTP 403 because the account plan does not
support it. The repository remains private; use PR reviews by convention unless the plan is upgraded.
The Discovery repository has `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` Actions secrets configured
for its manual release workflow. Secret values are not stored in the source tree.

## Repositories and images

- [Tracking issue #27](https://github.com/PAD-TEAM-7/PAD-TEAM-7/issues/27)
- [Maxim extension issue #29](https://github.com/PAD-TEAM-7/PAD-TEAM-7/issues/29)
- [Private Service Discovery source](https://github.com/PAD-TEAM-7/service-discovery)
- CPR review branch: `feat/shared/27-lab3-scaling`
- Extended CPR branch including Maxim: `feat/shared/29-maxim-lab3-scaling` (based on the original Lab 3 branch)
- Zombie branch: `feat/zombie-service/29-lab3-scaling`
- Resource branch: `feat/resource-service/29-lab3-scaling`
- Player review branch: `feat/player-service/27-lab3-scaling`
- Game review branch: `feat/game-service/27-lab3-scaling`
- Gateway review branch: `feat/gateway-service/27-lab3-scaling`

Versioned image references used by the deployment:

- `geografix/service-discovery:3.0.0`
- `geografix/pad-gateway:3.0.0`
- `geografix/player-service:3.0.0`
- `geografix/game-service:3.0.0`
- `geografix/zombie-service:3.0.0`
- `geografix/resource-service:3.0.0`

## Published source and review links

- [CPR deployment and guide PR #28](https://github.com/PAD-TEAM-7/PAD-TEAM-7/pull/28)
- [Maxim deployment and guide PR #30](https://github.com/PAD-TEAM-7/PAD-TEAM-7/pull/30), stacked on #28
- [Player PR #12](https://github.com/IslamAbukoush/player-service/pull/12)
- [Game PR #14](https://github.com/IslamAbukoush/game-service/pull/14)
- [Gateway PR #8](https://github.com/cyberArtik/gateway/pull/8)
- [Zombie PR #2](https://github.com/MaxRoenco/zombie-service/pull/2)
- [Resource PR #2](https://github.com/MaxRoenco/resource-service/pull/2)
- [Zombie successful CI run](https://github.com/MaxRoenco/zombie-service/actions/runs/40856869686)
- [Resource successful CI run](https://github.com/MaxRoenco/resource-service/actions/runs/40856877337)
- [Discovery CI: successful initial run](https://github.com/PAD-TEAM-7/service-discovery/actions/runs/40852382541)

## DockerHub publication

The deployment's six `3.0.0` images were built from the committed Lab 3 source and pushed:

| Image | Published digest |
| --- | --- |
| [Service Discovery](https://hub.docker.com/r/geografix/service-discovery/tags) | `sha256:f51a937aae3418655c79ce55d5e0bf76f81fb0be6fed45d7da17f96b083fa1b8` |
| [Gateway](https://hub.docker.com/r/geografix/pad-gateway/tags) | `sha256:1055ab7f737cf31ae0d5232c00d86361ec66bea23c4946ef0b4fb6b6fbaa63c3` |
| [Player](https://hub.docker.com/r/geografix/player-service/tags) | `sha256:17203606cac5f8647adc0415eb9f5483486cd1bcf2b92e71790c44e929773fae` |
| [Game](https://hub.docker.com/r/geografix/game-service/tags) | `sha256:22379a1cb237ddecfa93f04d68ac3c365f928c6f1c7f20b9d2faefe72ce58c9f` |
| [Zombie](https://hub.docker.com/r/geografix/zombie-service/tags) | `sha256:4629268864b597d23a83a0014ba166c6b24f9875a5f4b194bad170a9f6e06760` |
| [Resource](https://hub.docker.com/r/geografix/resource-service/tags) | `sha256:7be5baf5fb12f04774023b2c0cf4b683e449f8629ed6b99427274097d72fa338` |

These tags are immutable releases. Future runtime changes need a new version before publication.
Private service source remains accessible only to authorized collaborators; the runnable images
are public as required by the laboratory.
DockerHub's anonymous repository API confirmed `is_private=false` for all six repositories,
and remote manifest inspection matched each digest above.
The published Zombie/Resource tags target Linux amd64, as tested on this machine. CI additionally
builds Linux arm64 using native Go cross-compilation and runner emulation for the Alpine setup.
