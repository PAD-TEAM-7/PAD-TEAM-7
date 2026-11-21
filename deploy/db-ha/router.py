"""Forward PostgreSQL connections to exactly one writable node per database."""
import asyncio
import os

import psycopg


from layout import FIRST_PORT, OWNED_DATABASES


class Cluster:
    def __init__(self, service):
        self.service = service
        self.password = os.environ[f"{service.upper()}_DB_PASSWORD"]
        self.nodes = [f"{service}-db", f"{service}-db-replica-1", f"{service}-db-replica-2"]
        self.writer = None

    async def writable(self, host):
        try:
            async with asyncio.timeout(2):
                connection = await psycopg.AsyncConnection.connect(
                    host=host, user=self.service, password=self.password,
                    dbname=f"{self.service}_db", connect_timeout=2,
                )
                async with connection:
                    row = await (await connection.execute("SELECT NOT pg_is_in_recovery()")).fetchone()
                    return row == (True,)
        except (TimeoutError, psycopg.Error):
            return False

    async def refresh(self):
        while True:
            answers = await asyncio.gather(*(self.writable(node) for node in self.nodes))
            writers = [node for node, ready in zip(self.nodes, answers) if ready]
            writer = writers[0] if len(writers) == 1 else None
            if writer != self.writer:
                print(f"{self.service}: writer={writer or 'unavailable'}", flush=True)
            self.writer = writer
            await asyncio.sleep(0.75)

    async def forward(self, reader, writer):
        upstream = None
        tasks = []
        try:
            if self.writer is None:
                return
            async with asyncio.timeout(3):
                source, upstream = await asyncio.open_connection(self.writer, 5432)
            tasks = [
                asyncio.create_task(copy(reader, upstream)),
                asyncio.create_task(copy(source, writer)),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, TimeoutError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if upstream is not None:
                upstream.close()
            writer.close()


async def copy(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


async def main():
    clusters = [Cluster(service) for service in OWNED_DATABASES]
    servers = await asyncio.gather(*(
        asyncio.start_server(cluster.forward, "0.0.0.0", FIRST_PORT + index)
        for index, cluster in enumerate(clusters)
    ))
    await asyncio.gather(*(cluster.refresh() for cluster in clusters),
                         *(server.serve_forever() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
