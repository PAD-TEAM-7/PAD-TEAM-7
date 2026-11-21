"""Render the Compose overrides for eight independent PostgreSQL clusters."""
import argparse
import json
from pathlib import Path

from layout import FIRST_PORT, OWNED_DATABASES


def build():
    volumes = {}
    services = {}
    environment = {f"{name.upper()}_DB_PASSWORD": "${" + name.upper() + "_DB_PASSWORD}"
                   for name in OWNED_DATABASES}
    services["db-router"] = {
        "build": "./db-ha", "restart": "unless-stopped", "environment": environment,
        "depends_on": {f"{name}-db": {"condition": "service_started", "required": False}
                       for name in OWNED_DATABASES},
    }
    services["warehouse-etl"] = {
        "environment": {"ETL_DB_ROUTER": "db-router"},
        "depends_on": {"db-router": {"condition": "service_started"}},
    }
    for index, name in enumerate(OWNED_DATABASES):
        port = FIRST_PORT + index
        password = environment[f"{name.upper()}_DB_PASSWORD"]
        database = f"{name}_db"
        primary = f"{name}-db"
        start = {"entrypoint": ["/bin/sh", "/db-ha/start.sh"], "volumes": ["./db-ha:/db-ha:ro"]}
        services[primary] = start
        for number in (1, 2):
            replica = f"{primary}-replica-{number}"
            volume = f"{name}_db_replica_{number}_data"
            volumes[volume] = None
            services[replica] = {
                **start, "image": "postgres:16-alpine", "restart": "unless-stopped",
                "depends_on": {"db-router": {"condition": "service_started"}},
                "environment": {
                    "POSTGRES_USER": name, "POSTGRES_DB": database, "POSTGRES_PASSWORD": password,
                    "FOLLOW_HOST": "db-router", "FOLLOW_PORT": str(port),
                    "PGDATA": "/var/lib/postgresql/data/replica",
                },
                "volumes": [*start["volumes"], f"{volume}:/var/lib/postgresql/data"],
                "healthcheck": {"test": ["CMD", "pg_isready", "-U", name, "-d", database],
                                "interval": "3s", "timeout": "2s", "retries": 30},
            }
        if name in ("player", "game", "resource", "zombie"):
            settings = {"DATABASE_URL": f"postgres://{name}:{password}@db-router:{port}/{database}?sslmode=disable"}
        else:
            settings = {"DB_HOST": "db-router", "DB_PORT": str(port)}
        workers = [f"{name}-service"]
        if name == "game":
            workers.append("game-service-2")
        for worker in workers:
            services[worker] = {
                "environment": settings,
                "depends_on": {
                    "db-router": {"condition": "service_started"},
                    # Override Lab 4's hard dependency on the original writer.
                    primary: {"condition": "service_started", "required": False},
                },
            }
        services["warehouse-etl"]["environment"][f"ETL_{name.upper()}_PORT"] = str(port)
    return {"services": services, "volumes": volumes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).resolve().parent.parent / "docker-compose.lab5-ha.yml"
    rendered = json.dumps(build(), indent=2) + "\n"
    if args.check:
        if output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Regenerate docker-compose.lab5-ha.yml")
    else:
        output.write_text(rendered, encoding="utf-8")
