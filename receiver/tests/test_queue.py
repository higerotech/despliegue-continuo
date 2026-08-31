"""La cola de despliegues — deuda D-03.

La propiedad que importa: **dos despliegues de la misma aplicacion nunca se
solapan** (RNF04), mientras que aplicaciones distintas avanzan en paralelo.
Estaba razonada en ADR-0008 pero no demostrada.

Se sustituye el Deployer por un doble que registra el orden real de ejecucion,
asi que estas pruebas no tocan Docker y corren en milisegundos.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import AppConfig
from app.deployer import DeployResult
from app.queue import DeployQueue


def hacer_app(nombre: str) -> AppConfig:
    return AppConfig(
        name=nombre,
        repo=f"higerotech/{nombre}",
        project_dir=Path("/srv/apps") / nombre,
        image=f"ghcr.io/higerotech/{nombre}",
    )


class DeployerEspia:
    """Registra cuando empieza y termina cada despliegue, y cuanto tarda."""

    def __init__(self, duracion: float = 0.05, falla: bool = False) -> None:
        self.duracion = duracion
        self.falla = falla
        self.eventos: list[str] = []
        self.en_curso = 0
        self.maximo_simultaneo_por_app: dict[str, int] = {}
        self._activos: dict[str, int] = {}

    async def deploy(self, app: AppConfig, sha: str) -> DeployResult:
        self._activos[app.name] = self._activos.get(app.name, 0) + 1
        self.maximo_simultaneo_por_app[app.name] = max(
            self.maximo_simultaneo_por_app.get(app.name, 0), self._activos[app.name]
        )
        self.eventos.append(f"inicio {app.name}:{sha}")
        try:
            await asyncio.sleep(self.duracion)
            if self.falla:
                raise RuntimeError("fallo simulado del deployer")
            return DeployResult(app=app.name, sha=sha, tag=f"sha-{sha}", ok=True, seconds=self.duracion)
        finally:
            self._activos[app.name] -= 1
            self.eventos.append(f"fin {app.name}:{sha}")


async def test_la_misma_app_se_despliega_en_orden_y_sin_solaparse():
    """RNF04: el nucleo de D-03."""
    espia = DeployerEspia(duracion=0.05)
    cola = DeployQueue(espia)
    app = hacer_app("api")

    for sha in ("aaa", "bbb", "ccc"):
        cola.submit(app, sha, f"delivery-{sha}")

    await cola.drain()

    assert espia.maximo_simultaneo_por_app["api"] == 1, "se solaparon despliegues de la misma app"
    assert espia.eventos == [
        "inicio api:aaa", "fin api:aaa",
        "inicio api:bbb", "fin api:bbb",
        "inicio api:ccc", "fin api:ccc",
    ]


async def test_apps_distintas_avanzan_en_paralelo():
    """Serializar por app no debe convertirse en serializar todo."""
    espia = DeployerEspia(duracion=0.15)
    cola = DeployQueue(espia)

    inicio = asyncio.get_running_loop().time()
    cola.submit(hacer_app("api"), "aaa", "d1")
    cola.submit(hacer_app("web"), "bbb", "d2")
    await cola.drain()
    transcurrido = asyncio.get_running_loop().time() - inicio

    # En serie tardaria >= 0.30 s; en paralelo, algo por encima de 0.15 s.
    assert transcurrido < 0.28, f"parecen haber ido en serie ({transcurrido:.2f}s)"


async def test_submit_devuelve_la_posicion_en_la_cola():
    cola = DeployQueue(DeployerEspia(duracion=0.05))
    app = hacer_app("api")

    primera = cola.submit(app, "aaa", "d1")
    segunda = cola.submit(app, "bbb", "d2")

    assert (primera, segunda) == (1, 2)
    await cola.drain()


async def test_el_historico_registra_los_despliegues_mas_recientes_primero():
    cola = DeployQueue(DeployerEspia(duracion=0.01), history_size=10)
    app = hacer_app("api")

    cola.submit(app, "aaa", "d1")
    cola.submit(app, "bbb", "d2")
    await cola.drain()

    historico = cola.snapshot()["history"]
    assert [e["sha"] for e in historico] == ["bbb", "aaa"]
    assert historico[0]["delivery_id"] == "d2"


async def test_el_historico_esta_acotado():
    """Sin cota, un servicio de larga vida acumularia memoria sin limite."""
    cola = DeployQueue(DeployerEspia(duracion=0.001), history_size=3)
    app = hacer_app("api")

    for i in range(6):
        cola.submit(app, f"sha{i}", f"d{i}")
    await cola.drain()

    assert len(cola.snapshot()["history"]) == 3


async def test_un_deployer_que_revienta_no_mata_al_worker():
    """Si un fallo inesperado tumbara el worker, esa app dejaria de desplegarse
    para siempre y en silencio."""
    espia = DeployerEspia(duracion=0.01, falla=True)
    cola = DeployQueue(espia)
    app = hacer_app("api")

    cola.submit(app, "aaa", "d1")
    cola.submit(app, "bbb", "d2")
    await cola.drain()

    # El segundo trabajo se proceso pese a haber reventado el primero.
    assert "inicio api:bbb" in espia.eventos


async def test_el_snapshot_expone_lo_que_esta_en_curso():
    espia = DeployerEspia(duracion=0.2)
    cola = DeployQueue(espia)
    app = hacer_app("api")

    cola.submit(app, "aaa", "d1")
    await asyncio.sleep(0.05)

    instantanea = cola.snapshot()
    assert instantanea["running"]["api"]["sha"] == "aaa"

    await cola.drain()
    assert cola.snapshot()["running"] == {}


async def test_drain_espera_a_que_terminen_los_despliegues_en_curso():
    """Un apagado que corte un despliegue a medias dejaria el estado a medias."""
    espia = DeployerEspia(duracion=0.1)
    cola = DeployQueue(espia)

    cola.submit(hacer_app("api"), "aaa", "d1")
    await cola.drain()

    assert espia.eventos[-1] == "fin api:aaa"


async def test_drain_sin_nada_encolado_no_falla():
    await DeployQueue(DeployerEspia()).drain()
