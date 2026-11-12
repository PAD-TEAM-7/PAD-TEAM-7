# Native Lab 4 gRPC requests in Postman

REST collections cover public users and operations. Internal requests use the
actual protobuf and Broker; do not restore removed Gateway REST routes.

1. Run `python scripts/lab4-prepare-postman.py` from the CPR root. Besides the
   environment, it prints the path to **PAD-LAB4-GRPC-PRIVATE.json** outside Git.
   This is a set of rendered request examples, not a REST collection to import.
2. In Postman create **New → gRPC**. Server: `localhost:28600`, TLS off for this
   local Compose deployment. Import `contracts/broker/v1/broker.proto` as the
   service definition. Choose `pad.broker.v1.MessageBroker` → **Call**.
3. Open the private JSON locally. Pick an entry, copy its `message` object into
   Message, and add its `metadata.authorization` as the `authorization` metadata
   value. **Do not paste the whole entry as a protobuf message.** Invoke it.
4. Expect `Reply.status` equal to the entry's `expected_reply_status`. gRPC OK
   alone does not prove business success. `body` is protobuf `bytes` containing
   JSON; examples encode input bytes as base64. Inspect `instance_id` and
   `attempt_id` to show the selected worker and physical attempt.
5. Tokens expire after an hour. Rerun preparation for fresh examples/fixtures.
   Preserve request/idempotency IDs to demonstrate replay. To execute a new
   mutation, change both IDs. Some examples change real demo state; run the REST
   suite first or prepare again before switching demonstrations.

The committed [requests.json](requests.json) is a credential-free template. The
rendered file has actual IDs, encoded payloads and separate service identities.
It has status/load probes for all eight services plus:

| Service | Internal gRPC examples |
| --- | --- |
| Player | Service inventory read, Exam XP award |
| Game | Cycle command and lobby affinity |
| Exam | Passed-course lookup for Crafting |
| World | Node and spawn-point queries |
| Zombie | Spawn, damage the prepared zombie |
| Resource | Create a pool, gather from the prepared real stock |
| Base | Blueprint lookup used by services |
| Crafting | Catalogue via its logical command topic |

`Call` uses `topic: commands.<name>` with no `destination`. `Dispatch` is reserved
for authenticated Gateway subscriber queues and needs Gateway's verified identity;
a normal service token cannot impersonate it. `Execute`/`Deliver` target private
workers and are not exposed as user APIs.

Topic fan-out uses **Publish**, with a producer matching the event type, a unique
`event_id`, and an `events.<Type>` topic. Demonstrate real topic delivery by
registering a player, then inspecting Exam course enrolment, Broker `/v1/topics`
and Discovery `/v1/instances` interests. Use `scripts/lab4-durability-smoke.py` for
delivery across restart and `scripts/lab4-operations-smoke.py` for poison-message
dead letters and replay. They use controlled fixtures rather than forging real
domain events against existing players.

Treat the private file like the Postman environment: do not commit it, send it to
teammates, attach it to a PR, or display authorization metadata to the mentor.
