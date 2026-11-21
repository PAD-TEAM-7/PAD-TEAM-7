"""Fence an old writer before promoting a PostgreSQL standby."""
import argparse
import subprocess
import tempfile
import time
from pathlib import Path

from layout import OWNED_DATABASES

ROOT = Path(__file__).resolve().parents[2]


def switchover(service, source, target, project, env_file, source_down=False):
    compose = ["docker", "compose", "--env-file", str(env_file), "-p", project]
    for name in ("lab4", "lab5", "lab5-ha"):
        compose += ["-f", f"deploy/docker-compose.{name}.yml"]

    def run(*args):
        return subprocess.check_output(compose + list(args), cwd=ROOT, text=True).strip()

    def sql(node, statement):
        return run("exec", "-T", node, "psql", "-XAt", "-v", "ON_ERROR_STOP=1",
                   "-U", service, "-d", f"{service}_db", "-c", statement)

    def wait_for(check, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(0.5)
        raise RuntimeError("Timed out. Inspect the nodes before attempting further recovery.")

    if sql(target, "SELECT pg_is_in_recovery()") != "t":
        raise SystemExit(f"{target} is not a standby.")
    nodes = (f"{service}-db", f"{service}-db-replica-1", f"{service}-db-replica-2")
    for node in nodes:
        if node not in (source, target) and run("ps", "-q", node):
            if sql(node, "SELECT pg_is_in_recovery()") != "t":
                raise RuntimeError(f"{node} is already writable. Promotion was cancelled.")
    fence = 'touch "$PGDATA/.fenced"'
    if source_down:
        if run("ps", "-q", source):
            raise RuntimeError("The source is still running. Use the planned procedure.")
        # Stop disables the container restart policy even if postgres just crashed.
        run("stop", "--timeout", "30", source)
        run("run", "--rm", "-T", "--no-deps", "--entrypoint", "/bin/sh", source, "-ec", fence)
    else:
        if sql(source, "SELECT pg_is_in_recovery()") != "f":
            raise SystemExit(f"{source} is not writable.")
        lsn = sql(source, "SELECT pg_current_wal_flush_lsn()")
        if not lsn or any(character not in "0123456789ABCDEF/" for character in lsn):
            raise RuntimeError("PostgreSQL returned an invalid WAL position.")

        def replayed():
            return sql(target, f"SELECT pg_last_wal_replay_lsn() >= '{lsn}'::pg_lsn") == "t"

        # For a planned switch, pause application writes before invoking this script.
        wait_for(replayed, 30)
        run("exec", "-T", source, "sh", "-ec", fence)
        run("stop", "--timeout", "30", source)
        wait_for(replayed, 15)
    if run("ps", "-q", source):
        raise RuntimeError("The old writer is still running. Promotion was cancelled.")
    if sql(target, "SELECT pg_promote(true, 30)") != "t":
        raise RuntimeError("Promotion did not complete. The old writer stays fenced.")
    print(f"{source} stopped and fenced. {target} is now the writer.")
    if source_down:
        print("Verify recent writes. Asynchronous replication cannot guarantee zero loss on a crash.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=OWNED_DATABASES)
    parser.add_argument("--from-node", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--to-node", type=int, choices=(0, 1, 2), default=1)
    parser.add_argument("--project", default="pad-lab5")
    parser.add_argument("--env-file", type=Path, default=ROOT / "deploy/.env")
    parser.add_argument("--source-down", action="store_true", help="Promote after an already stopped writer")
    args = parser.parse_args()
    primary = f"{args.service}-db"
    nodes = (primary, f"{primary}-replica-1", f"{primary}-replica-2")
    if args.from_node == args.to_node or not args.project.startswith("pad-lab5") or not args.project.replace("-", "").isalnum():
        parser.error("Choose separate nodes and a project starting with pad-lab5.")
    lock = Path(tempfile.gettempdir()) / f"{args.project}-{args.service}-promotion.lock"
    handle = lock.open("x")
    try:
        handle.close()
        switchover(args.service, nodes[args.from_node], nodes[args.to_node],
                   args.project, args.env_file.resolve(), args.source_down)
    finally:
        lock.unlink()
