"""Bordes del deployer que las pruebas e2e no tocan — deuda D-02.

Las e2e (`test_rollback_e2e.py`) cubren el ciclo real contra Docker: camino
feliz y rollback. Lo que queda sin ejercitar son los bordes: que falte el
compose, que no haya `health_url`, que el propio rollback falle, que el estado
en disco este corrupto. Aqui se sustituye la ejecucion de `docker compose` por
un doble, asi que estas pruebas corren en milisegundos y sin daemon.
"""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from app.config import AppConfig, Settings
from app.deployer import CommandError, Deployer


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        webhook_secret="x" * 40,
        apps_file=tmp_path / "apps.yml",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
def proyecto(tmp_path: Path) -> Path:
    raiz = tmp_path / "app"
    raiz.mkdir()
    (raiz / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return raiz


def hacer_app(proyecto: Path, **extra) -> AppConfig:
    campos = dict(
        name="mi-api",
        repo="higerotech/mi-api",
        project_dir=proyecto,
        image="ghcr.io/higerotech/mi-api",
        health_url=None,
        health_timeout=1,
        health_interval=0.01,
    )
    campos.update(extra)
    return AppConfig(**campos)


class DeployerFalso(Deployer):
    """Deployer con `docker compose` sustituido por una lista de resultados."""

    def __init__(self, settings: Settings, fallos: dict[str, Exception] | None = None) -> None:
        super().__init__(settings)
        self.comandos: list[list[str]] = []
        self.tags: list[str] = []
        self._fallos = fallos or {}

    async def _run(self, app, args, tag):
        self.comandos.append(args)
        self.tags.append(tag)
        unidos = " ".join(args)
        for clave, error in self._fallos.items():
            if clave in unidos:
                raise error
        return "ok"


class _Silencioso(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # no ensuciar la salida de pytest
        pass


@pytest.fixture
def servidor_sano():
    """Un HTTP real que responde 200: para el camino de healthcheck correcto."""
    servidor = HTTPServer(("127.0.0.1", 0), partial(_Silencioso, directory="."))
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{servidor.server_port}/"
    servidor.shutdown()
    servidor.server_close()


class TestCaminoCorrecto:
    async def test_sin_health_url_el_despliegue_se_da_por_bueno(self, settings, proyecto):
        # Es el comportamiento actual y la razon de ser de la deuda DS-06.
        deployer = DeployerFalso(settings)
        resultado = await deployer.deploy(hacer_app(proyecto), "1a2b3c4d")

        assert resultado.ok
        assert "se omite" in resultado.steps[-1].detail

    async def test_persiste_el_tag_actual_y_el_anterior(self, settings, proyecto):
        deployer = DeployerFalso(settings)
        app = hacer_app(proyecto)

        await deployer.deploy(app, "1111111aaa")
        await deployer.deploy(app, "2222222bbb")

        estado = deployer.read_state(app)
        assert estado["current_tag"] == "sha-2222222"
        assert estado["previous_tag"] == "sha-1111111"

    async def test_inyecta_el_tag_en_pull_y_en_up(self, settings, proyecto):
        deployer = DeployerFalso(settings)
        await deployer.deploy(hacer_app(proyecto), "1a2b3c4d")

        assert deployer.tags == ["sha-1a2b3c4", "sha-1a2b3c4"]
        assert "pull" in " ".join(deployer.comandos[0])
        assert "up" in " ".join(deployer.comandos[1])

    async def test_el_healthcheck_correcto_cierra_el_despliegue(
        self, settings, proyecto, servidor_sano
    ):
        deployer = DeployerFalso(settings)
        resultado = await deployer.deploy(
            hacer_app(proyecto, health_url=servidor_sano, health_timeout=5), "1a2b3c4d"
        )

        assert resultado.ok
        assert "respondio 200" in resultado.steps[-1].detail


class TestFallos:
    async def test_si_falta_el_compose_no_se_ejecuta_nada(self, settings, tmp_path):
        vacio = tmp_path / "sin-compose"
        vacio.mkdir()
        deployer = DeployerFalso(settings)

        resultado = await deployer.deploy(hacer_app(vacio), "1a2b3c4d")

        assert resultado.ok is False
        assert "no existe" in resultado.error
        assert deployer.comandos == [], "no debe tocarse Docker si el compose no existe"

    async def test_un_pull_fallido_deja_el_error_legible(self, settings, proyecto):
        deployer = DeployerFalso(settings, fallos={"pull": CommandError("pull", 1, "denied")})

        resultado = await deployer.deploy(hacer_app(proyecto), "1a2b3c4d")

        assert resultado.ok is False
        assert "denied" in resultado.error

    async def test_el_healthcheck_agotado_marca_el_despliegue_como_fallido(
        self, settings, proyecto
    ):
        # Puerto cerrado: la conexion se rechaza de inmediato.
        app = hacer_app(
            proyecto, health_url="http://127.0.0.1:9/", health_timeout=1, health_interval=0.01
        )
        resultado = await DeployerFalso(settings).deploy(app, "1a2b3c4d")

        assert resultado.ok is False
        assert "healthcheck agotado" in resultado.error

    async def test_sin_tag_anterior_no_se_intenta_rollback(self, settings, proyecto):
        deployer = DeployerFalso(settings, fallos={"pull": CommandError("pull", 1, "denied")})

        resultado = await deployer.deploy(hacer_app(proyecto), "1a2b3c4d")

        assert resultado.rolled_back is False
        assert deployer.read_state(hacer_app(proyecto)) == {}

    async def test_con_rollback_desactivado_no_se_revierte(self, settings, proyecto):
        deployer = DeployerFalso(settings)
        app = hacer_app(proyecto, rollback=False)
        await deployer.deploy(app, "1111111aaa")

        deployer._fallos = {"pull": CommandError("pull", 1, "denied")}
        resultado = await deployer.deploy(app, "2222222bbb")

        assert resultado.ok is False
        assert resultado.rolled_back is False

    async def test_si_el_rollback_tambien_falla_se_registra_como_no_revertido(
        self, settings, proyecto
    ):
        """El peor caso: la app queda caida y el operador debe saberlo."""
        deployer = DeployerFalso(settings)
        app = hacer_app(proyecto)
        await deployer.deploy(app, "1111111aaa")

        # A partir de aqui falla todo, incluido el intento de volver atras.
        deployer._fallos = {"pull": CommandError("pull", 1, "registro caido")}
        resultado = await deployer.deploy(app, "2222222bbb")

        assert resultado.ok is False
        assert resultado.rolled_back is False
        assert any("rollback" in paso.name for paso in resultado.steps)


class TestEstadoYRegistro:
    async def test_un_estado_corrupto_se_trata_como_inexistente(self, settings, proyecto):
        """Un JSON roto no puede impedir que el servicio arranque o despliegue."""
        deployer = DeployerFalso(settings)
        app = hacer_app(proyecto)
        (settings.state_dir / f"{app.name}.json").write_text("{ esto no es json", encoding="utf-8")

        assert deployer.read_state(app) == {}

        resultado = await deployer.deploy(app, "1a2b3c4d")
        assert resultado.ok

    async def test_cada_despliegue_anade_una_linea_al_jsonl(self, settings, proyecto):
        deployer = DeployerFalso(settings)
        app = hacer_app(proyecto)

        await deployer.deploy(app, "1111111aaa")
        await deployer.deploy(app, "2222222bbb")

        lineas = (settings.log_dir / f"{app.name}.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lineas) == 2
        assert json.loads(lineas[1])["tag"] == "sha-2222222"

    async def test_el_estado_se_escribe_de_forma_atomica(self, settings, proyecto):
        """Se escribe a .tmp y se renombra: un corte no deja el fichero a medias."""
        deployer = DeployerFalso(settings)
        app = hacer_app(proyecto)
        await deployer.deploy(app, "1a2b3c4d")

        assert (settings.state_dir / f"{app.name}.json").exists()
        assert not (settings.state_dir / f"{app.name}.json.tmp").exists()
