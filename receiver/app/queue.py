"""Cola de despliegues: una app nunca se despliega dos veces a la vez.

GitHub espera una respuesta en menos de 10 segundos, asi que el endpoint solo
encola y responde 202. El trabajo real lo hace un worker por aplicacion, lo que
serializa los despliegues de una misma app y permite que apps distintas avancen
en paralelo.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import AppConfig
from .deployer import Deployer, DeployResult

logger = logging.getLogger("cd.queue")


@dataclass(frozen=True)
class Job:
    app: AppConfig
    sha: str
    delivery_id: str
    queued_at: str


class DeployQueue:
    """Un worker por app, creado bajo demanda y detenido de forma ordenada."""

    def __init__(self, deployer: Deployer, history_size: int = 50) -> None:
        self._deployer = deployer
        self._queues: dict[str, asyncio.Queue[Job]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._running: dict[str, Job] = {}
        self._history: deque[dict] = deque(maxlen=history_size)

    def submit(self, app: AppConfig, sha: str, delivery_id: str) -> int:
        """Encola un despliegue y devuelve cuantos hay por delante de este."""
        queue = self._queues.get(app.name)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[app.name] = queue
            self._workers[app.name] = asyncio.create_task(
                self._worker(app.name, queue), name=f"deploy-worker:{app.name}"
            )

        job = Job(
            app=app,
            sha=sha,
            delivery_id=delivery_id,
            queued_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        queue.put_nowait(job)
        logger.info("encolado %s -> %s (delivery %s)", app.name, sha[:7], delivery_id)
        return queue.qsize()

    async def _worker(self, name: str, queue: asyncio.Queue[Job]) -> None:
        while True:
            job = await queue.get()
            self._running[name] = job
            try:
                result = await self._deployer.deploy(job.app, job.sha)
                self._record(job, result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("el worker de %s fallo de forma inesperada", name)
            finally:
                self._running.pop(name, None)
                queue.task_done()

    def _record(self, job: Job, result: DeployResult) -> None:
        entry = result.as_dict() | {"delivery_id": job.delivery_id, "queued_at": job.queued_at}
        self._history.appendleft(entry)
        if result.ok:
            logger.info("despliegue OK %s %s en %.1fs", result.app, result.tag, result.seconds)
        else:
            logger.error(
                "despliegue FALLIDO %s %s (rollback=%s): %s",
                result.app, result.tag, result.rolled_back, result.error.splitlines()[0:1],
            )

    def snapshot(self) -> dict:
        return {
            "running": {
                name: {"sha": job.sha, "queued_at": job.queued_at}
                for name, job in self._running.items()
            },
            "pending": {name: q.qsize() for name, q in self._queues.items() if q.qsize()},
            "history": list(self._history),
        }

    async def drain(self, timeout: float = 30.0) -> None:
        """Espera a que terminen los despliegues en curso y para los workers."""
        pending = [q.join() for q in self._queues.values()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("apagando con despliegues aun en curso")

        for task in self._workers.values():
            task.cancel()
        await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()
        self._queues.clear()
