"""Isolated acceptance worker; mount into the broker image, never a production service.

Two real gRPC replicas expose authenticated test controls and measured in-flight
load. They register as their own service group and never replace domain workers.
"""

import hmac
import json
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import grpc
import httpx
from broker.generated import broker_pb2 as pb
from broker.generated import broker_pb2_grpc as rpc

STOP = threading.Event()


class Replica(rpc.ServiceWorkerServicer):
    def __init__(self, suffix, http_port, grpc_port):
        self.identity = os.environ["FIXTURE_ID"] + "-" + suffix
        self.http_port, self.grpc_port = http_port, grpc_port
        self.lock = threading.Lock()
        self.active = self.calls = self.failures = 0
        self.fail = False

    def Execute(self, command, context):
        if not hmac.compare_digest(
            dict(context.invocation_metadata()).get("authorization", ""),
            "Bearer " + os.environ["BROKER_WORKER_TOKEN"],
        ):
            context.abort(
                grpc.StatusCode.UNAUTHENTICATED, "Broker authentication required"
            )
        with self.lock:
            self.active += 1
            self.calls += 1
        try:
            delay = min(
                8,
                max(
                    0,
                    float(parse_qs(urlsplit(command.path).query).get("delay", [0])[0]),
                ),
            )
            STOP.wait(delay)
            with self.lock:
                failed = self.fail
                self.failures += int(failed)
            return pb.Reply(
                status=503 if failed else 200,
                body=json.dumps({"instance_id": self.identity}).encode(),
            )
        finally:
            with self.lock:
                self.active -= 1

    def start(self):
        replica = self

        class Probe(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with replica.lock:
                    self.reply(
                        200,
                        {
                            "status": "ok",
                            "instance_id": replica.identity,
                            "in_flight": replica.active,
                            "capacity": 1,
                            "load": float(replica.active),
                            "calls": replica.calls,
                            "failures": replica.failures,
                        },
                    )

            def do_POST(self):
                if not hmac.compare_digest(
                    self.headers.get("Authorization", ""),
                    "Bearer " + os.environ["BROKER_ADMIN_TOKEN"],
                ):
                    return self.reply(
                        401, {"error": "Operator authentication required"}
                    )
                size = int(self.headers.get("Content-Length", "0"))
                if size > 100:
                    return self.reply(413, {})
                body = json.loads(self.rfile.read(size))
                with replica.lock:
                    replica.fail = bool(body.get("fail"))
                self.reply(200, {"configured": True})

        self.http = ThreadingHTTPServer(("0.0.0.0", self.http_port), Probe)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.grpc = grpc.server(ThreadPoolExecutor(max_workers=8))
        rpc.add_ServiceWorkerServicer_to_server(self, self.grpc)
        self.grpc.add_insecure_port("0.0.0.0:" + str(self.grpc_port))
        self.grpc.start()

    def registration(self):
        host = os.environ["HOSTNAME"]
        return {
            "service": "lab4-fixture-service",
            "instance_id": self.identity,
            "url": f"http://{host}:{self.http_port}",
            "grpc_url": f"{host}:{self.grpc_port}",
            "topics": ["commands.lab4-fixture"],
            "task_timeout": 15,
        }


def main():
    replicas = [Replica("a", 8001, 9001), Replica("b", 8002, 9002)]
    for item in replicas:
        item.start()
    headers = {"Authorization": "Bearer " + os.environ["DISCOVERY_TOKEN"]}
    with httpx.Client(timeout=3) as client:
        try:
            while not STOP.is_set():
                for item in replicas:
                    client.post(
                        "http://service-discovery:8500/v1/instances",
                        headers=headers,
                        json=item.registration(),
                    ).raise_for_status()
                STOP.wait(3)
        finally:
            for item in replicas:
                client.delete(
                    "http://service-discovery:8500/v1/instances/" + item.identity,
                    headers=headers,
                )
                item.grpc.stop(1).wait()
                item.http.shutdown()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    main()
