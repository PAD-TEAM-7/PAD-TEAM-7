"""Demonstrate streaming and writer failover on an isolated Lab 5 stack."""
import argparse
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from failover import ROOT, switchover
from layout import FIRST_PORT, OWNED_DATABASES


def verify(service, project, env_file):
    primary = f"{service}-db"
    nodes = [primary, f"{primary}-replica-1", f"{primary}-replica-2"]
    port = FIRST_PORT + OWNED_DATABASES.index(service)
    table = "pad_lab5_probe_" + uuid4().hex
    router = f"{project}-db-router-1"
    program = """
import os, sys
import psycopg
service, port, statement = sys.argv[1:]
with psycopg.connect(host="127.0.0.1", port=int(port), user=service,
                    password=os.environ[service.upper() + "_DB_PASSWORD"],
                    dbname=service + "_db", connect_timeout=3) as db:
    cursor = db.execute(statement)
    if cursor.description:
        print(cursor.fetchone()[0])
"""

    def command(*args):
        return subprocess.check_output(["docker", *args], cwd=ROOT, text=True, timeout=15).strip()

    def route(statement):
        return command("exec", router, "python", "-c", program, service, str(port), statement)

    def direct(node, statement):
        return command("exec", f"{project}-{node}-1", "psql", "-XAt", "-v", "ON_ERROR_STOP=1",
                       "-U", service, "-d", f"{service}_db", "-c", statement)

    def wait_for(check):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            try:
                if check():
                    return
            except subprocess.SubprocessError:
                pass
            time.sleep(0.75)
        raise AssertionError(f"{service}: expected database state did not arrive")

    wait_for(lambda: all(direct(node, "SELECT pg_is_in_recovery()") == "t" for node in nodes[1:]))
    wait_for(lambda: direct(primary, "SELECT count(*) FROM pg_stat_replication WHERE state='streaming'") == "2")
    route(f"CREATE TABLE {table}(value INT PRIMARY KEY)")
    try:
        route(f"INSERT INTO {table} VALUES (101)")
        select = f"SELECT count(*) FROM {table}"
        wait_for(lambda: all(direct(node, select) == "1" for node in nodes[1:]))
        switchover(service, primary, nodes[1], project, env_file)
        wait_for(lambda: route(select) == "1")
        route(f"INSERT INTO {table} VALUES (202)")
        wait_for(lambda: direct(nodes[2], select) == "2")
    finally:
        route(f"DROP TABLE IF EXISTS {table}")
    print(f"PASS {service}: two streaming replicas, retained pre-switch write, accepted new write "
          "through unchanged address, and replicated it to the remaining standby.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("services", nargs="+", choices=OWNED_DATABASES)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / "deploy/.env")
    args = parser.parse_args()
    if not args.project.startswith("pad-lab5") or not args.project.replace("-", "").isalnum():
        parser.error("Use a disposable project whose name starts with pad-lab5.")
    if not args.env_file.exists():
        parser.error("Provide the Compose environment with --env-file.")
    for name in args.services:
        verify(name, args.project, args.env_file.resolve())
