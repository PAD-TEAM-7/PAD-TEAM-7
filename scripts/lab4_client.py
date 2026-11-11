"""Real Lab 4 gRPC/REST client. Secrets remain local and are never logged."""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import grpc
import jwt

from lab4_rpc import broker_pb2 as pb
from lab4_rpc import broker_pb2_grpc as rpc

ROOT = Path(__file__).resolve().parents[1]


def environment(path=ROOT / "deploy/.env"):
    result = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        result[name.strip()] = value.strip().strip('"').strip("'")
    return result


def http(
    base, path, *, method="GET", body=None, headers=None, expected=200, timeout=30
):
    data = None if body is None else json.dumps(body).encode()
    headers = {**(headers or {})}
    if data is not None:
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        base.rstrip("/") + path, data=data, headers=headers, method=method
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = raw
        if response.status != expected:
            # A failed login/registration must not print credentials or token bodies.
            code = (
                payload.get("error", {}).get("code", "UNKNOWN")
                if isinstance(payload, dict)
                else "NON_JSON"
            )
            raise AssertionError(
                f"{method} {path.split('?')[0]}: expected {expected}, received {response.status} ({code})"
            )
        return payload, {
            name.title(): value for name, value in response.headers.items()
        }


class Lab4Client:
    def __init__(
        self,
        env_file=ROOT / "deploy/.env",
        target="127.0.0.1:28600",
        gateway="http://localhost:28080",
        discovery="http://localhost:28500",
        operations="http://localhost:28601",
    ):
        self.env = environment(env_file)
        self.gateway, self.discovery, self.operations = gateway, discovery, operations
        self.channel = grpc.insecure_channel(
            target, options=[("grpc.max_receive_message_length", 4 << 20)]
        )
        self.stub = rpc.MessageBrokerStub(self.channel)

    def metadata(self, source):
        token = jwt.encode(
            {
                "sub": source,
                "roles": ["service"],
                "aud": "message-broker",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
            },
            self.env["SERVICE_JWT_SECRET"],
            algorithm="HS256",
        )
        return [("authorization", "Bearer " + token)]

    def call(
        self,
        service,
        path,
        method="GET",
        body=None,
        *,
        source="game-service",
        key="",
        expected=200,
        timeout=30,
        headers=None,
    ):
        command = pb.Command(
            request_id=str(uuid4()),
            topic="commands." + service.removesuffix("-service"),
            method=method,
            path=path,
            body=b"" if body is None else json.dumps(body).encode(),
            headers={"content-type": "application/json", **(headers or {})},
            idempotency_key=key,
            timeout_ms=int(timeout * 1000),
        )
        if service == "game-service" and path.startswith("/api/v1/lobbies/"):
            command.affinity = path.split("?", 1)[0].split("/")[4]
        reply = self.stub.Call(command, metadata=self.metadata(source), timeout=timeout)
        if reply.status != expected:
            raise AssertionError(
                f"gRPC {service} {method} {path.split('?')[0]}: expected {expected}, received {reply.status}"
            )
        payload = json.loads(reply.body) if reply.body else {}
        return payload, {name.title(): value for name, value in reply.headers.items()}

    def publish(self, event, *, source=None):
        source = source or event["producer"]
        return self.stub.Publish(
            pb.Event(
                event_id=event["event_id"],
                topic="events." + event["type"],
                producer=event["producer"],
                body=json.dumps(event).encode(),
                request_id=event["event_id"],
            ),
            metadata=self.metadata(source),
            timeout=10,
        )

    def registry(self):
        data, _ = http(
            self.discovery,
            "/v1/instances",
            headers={"Authorization": "Bearer " + self.env["DISCOVERY_TOKEN"]},
        )
        return data["instances"]

    def ops(self, path, method="GET", body=None, expected=200):
        return http(
            self.operations,
            path,
            method=method,
            body=body,
            headers={"Authorization": "Bearer " + self.env["BROKER_ADMIN_TOKEN"]},
            expected=expected,
        )

    def close(self):
        self.channel.close()


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(
                part in key.lower()
                for part in ("password", "token", "secret", "authorization")
            )
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / "deploy/.env")
    parser.add_argument("--target", default="127.0.0.1:28600")
    commands = parser.add_subparsers(dest="operation", required=True)
    call = commands.add_parser(
        "call", help="Authenticated logical-topic request, not a Gateway REST call"
    )
    call.add_argument("service")
    call.add_argument("path")
    call.add_argument("--source", default="game-service")
    call.add_argument("--method", default="GET")
    call.add_argument("--body-file", type=Path)
    call.add_argument("--key", default="")
    call.add_argument("--expected", type=int, default=200)
    commands.add_parser("topics")
    commands.add_parser("dead-letters")
    commands.add_parser("queues")
    args = parser.parse_args()
    client = Lab4Client(args.env_file, args.target)
    try:
        if args.operation == "call":
            body = (
                json.loads(args.body_file.read_text(encoding="utf-8"))
                if args.body_file
                else None
            )
            result, headers = client.call(
                args.service,
                args.path,
                args.method,
                body,
                source=args.source,
                key=args.key,
                expected=args.expected,
            )
            print(
                json.dumps(
                    {
                        "result": redact(result),
                        "instance": headers.get("X-Service-Instance"),
                    },
                    indent=2,
                )
            )
        else:
            print(json.dumps(client.ops("/v1/" + args.operation)[0], indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
