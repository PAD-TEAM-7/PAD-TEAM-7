"""Run commit recovery and abort checks against the real 2PC participants."""
import argparse
import json
import subprocess
from uuid import UUID

# Run inside the Broker container. The restricted database password stays there.
CHECK = r"""
import base64
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from broker.saga import CraftSagas
from broker.store import Store
from broker.two_phase import TwoPhase

data = json.load(sys.stdin)
mode = data.pop("mode")
data["job_id"] = str(uuid4())
job = data["job_id"]
participant = TwoPhase.from_environment()
key = base64.b64encode(os.urandom(32)).decode()

def state(name):
    with participant.connect(name) as db:
        return participant.prepared(db, job), participant.committed(db, job)

with TemporaryDirectory() as directory:
    journal = Path(directory) / "broker.sqlite"
    store = Store(journal, key)
    saga = CraftSagas(store, "test-key" * 4, participant)
    saga.submit(data, "lab5-2pc-check", protocol="2pc")
    saga.step()
    decision = saga.list_recent()[0]["state"]
    try:
        assert saga.result(job) is None
        if mode == "commit":
            assert decision == "commit_2pc"
            assert all(state(name) == (True, False) for name in participant.participants)
            participant.decide("resource", job, commit=True)
            assert state("resource") == (False, True)
            assert state("player") == (True, False)
        else:
            assert decision == "abort_2pc"
            assert state("resource") == (True, False)
            assert state("player") == (False, False)
        store.close()
        store = Store(journal, key)
        saga = CraftSagas(store, "test-key" * 4, participant)
        saga.step()
        result = saga.result(job)
        assert result is not None
        if mode == "commit":
            assert result.status == 200
            saga.submit(data, "repeat", protocol="2pc")
            saga.step()
            assert saga.result(job) == result
            assert all(state(name) == (False, True) for name in participant.participants)
            print("PASS: both prepared, durable commit survived restart after first commit, replay unchanged.")
        else:
            assert result.status == 404
            assert all(state(name) == (False, False) for name in participant.participants)
            print("PASS: Player rejected preparation and Resource rolled back the prepared transaction.")
    finally:
        if decision == "commit_2pc":
            participant.decide_all(job, commit=True)
        elif decision == "abort_2pc":
            participant.decide_all(job, commit=False)
        store.close()
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--player", required=True, type=UUID)
    parser.add_argument("--lobby", required=True, type=UUID)
    parser.add_argument("--mode", choices=("commit", "abort"), required=True)
    args = parser.parse_args()
    if not args.project.startswith("pad-lab5") or not args.project.replace("-", "").isalnum():
        parser.error("Use a dedicated Lab 5 test project.")
    data = {"mode": args.mode, "player_id": str(args.player), "lobby_id": str(args.lobby),
            "inputs": [{"item_id": "wood-01", "count": 4}, {"item_id": "metal-01", "count": 2}],
            "output": {"item_id": "barricade-kit-01", "count": 1}}
    subprocess.run(
        ["docker", "exec", "-i", f"{args.project}-message-broker-1", "python", "-c", CHECK],
        input=json.dumps(data), text=True, check=True, timeout=65,
    )


if __name__ == "__main__":
    main()
