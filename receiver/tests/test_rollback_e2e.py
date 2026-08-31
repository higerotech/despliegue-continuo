"""Prueba de extremo a extremo del rollback contra Docker real — deuda D-01.

RF05 es el requisito que convierte un despliegue roto en un incidente menor en
vez de un servicio caido, y hasta ahora solo estaba verificado a mano. Estas
pruebas ejercen el ciclo completo: `pull`, `up -d`, healthcheck y vuelta al tag
anterior.

Necesitan un Docker en marcha; si no lo hay, se saltan en lugar de fallar.

El escenario usa dos tags locales sobre un mismo repositorio de imagen:

* `sha-sano`  -> traefik/whoami, que sirve HTTP en el puerto 80.
* `sha-roto`  -> alpine, que arranca y termina de inmediato: el contenedor
                 queda en `exited` y nadie responde al healthcheck.

Ambos se crean con `docker tag` a partir de imagenes publicas, y el compose
lleva `pull_policy: never` para que `docker compose pull` no intente ir al
registro a por un repositorio que solo existe en local.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import uuid
import warnings
from pathlib import Path

import httpx
import pytest

from app.config import AppConfig, Settings
from app.deployer import Deployer

REPO_IMAGEN = "cd-e2e-rollback"
BASE_SANA = "traefik/whoami:v1.10"
BASE_ROTA = "alpine:3.20"

# tag_for() toma los 7 primeros caracteres del sha, asi que estos identificadores
# cortos producen tags legibles: "sano" -> "sha-sano", "roto" -> "sha-roto".
SHA_SANO = "sano"
SHA_SANO_2 = "sano2"
SHA_ROTO = "roto"

COMPOSE = """\
services:
  api:
    image: {repo}:${{IMAGE_TAG:?IMAGE_TAG es obligatorio}}
    pull_policy: never
    ports:
      - "127.0.0.1:{puerto}:80"
"""


def _hay_docker() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=60
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _hay_docker(), reason="requiere un Docker en marcha"),
]


def _correr(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def imagenes() -> None:
    """Crea los tags locales del escenario y los retira al terminar."""
    for base in (BASE_SANA, BASE_ROTA):
        assert _correr("docker", "pull", base).returncode == 0, f"no se pudo traer {base}"

    for base, tag in (
        (BASE_SANA, SHA_SANO),
        (BASE_SANA, SHA_SANO_2),
        (BASE_ROTA, SHA_ROTO),
    ):
        destino = f"{REPO_IMAGEN}:sha-{tag}"
        assert _correr("docker", "tag", base, destino).returncode == 0

    yield

    for tag in (SHA_SANO, SHA_SANO_2, SHA_ROTO):
        _correr("docker", "rmi", "-f", f"{REPO_IMAGEN}:sha-{tag}", timeout=120)


class Escenario:
    """Una aplicacion desplegable de verdad, con su compose y su puerto."""

    def __init__(self, raiz: Path) -> None:
        self.proyecto = f"cd-e2e-{uuid.uuid4().hex[:8]}"
        self.puerto = _puerto_libre()
        self.raiz = raiz

        (raiz / "docker-compose.yml").write_text(
            COMPOSE.format(repo=REPO_IMAGEN, puerto=self.puerto), encoding="utf-8"
        )

        self.app = AppConfig(
            name=self.proyecto,
            repo="higerotech/cd-e2e",
            project_dir=raiz,
            image=REPO_IMAGEN,
            health_url=f"http://127.0.0.1:{self.puerto}/",
            health_timeout=30,
            health_interval=1.0,
            rollback=True,
            command_timeout=240,
        )
        self.deployer = Deployer(
            Settings(
                webhook_secret="x" * 40,
                apps_file=raiz / "apps.yml",
                state_dir=raiz / "state",
                log_dir=raiz / "logs",
                # Vacio a proposito: habla con el Docker del entorno, no con el
                # socket-proxy, que en una maquina de desarrollo no existe.
                docker_host="",
            )
        )

    @property
    def contenedor(self) -> str:
        return f"{self.proyecto}-api-1"

    def imagen_viva(self) -> str:
        salida = _correr(
            "docker", "inspect", "-f", "{{.Config.Image}}", self.contenedor, timeout=60
        )
        return salida.stdout.strip()

    def responde(self) -> bool:
        try:
            return httpx.get(self.app.health_url, timeout=5.0).status_code == 200
        except httpx.HTTPError:
            return False

    def derribar(self) -> None:
        """Derriba el stack y verifica que de verdad se fue.

        El compose usa `${IMAGE_TAG:?}`, asi que **`down` tambien necesita la
        variable**: sin ella compose ni siquiera parsea el fichero y falla en
        silencio, dejando el contenedor vivo y su red retenida. Con suficientes
        ejecuciones eso agota los rangos de red del daemon
        ("all predefined address pools have been fully subnetted") y rompe
        pruebas que no tienen nada que ver.
        """
        derribado = subprocess.run(
            [
                "docker", "compose",
                "-f", str(self.app.compose_path),
                "-p", self.proyecto,
                "down", "-v", "--remove-orphans",
            ],
            env={**os.environ, "IMAGE_TAG": f"sha-{SHA_SANO}"},
            capture_output=True,
            text=True,
            timeout=180,
        )
        if derribado.returncode == 0:
            return

        # Red de seguridad: si compose no pudo, se fuerza a mano. Un teardown
        # que falla en silencio contamina las ejecuciones siguientes.
        _correr("docker", "rm", "-f", self.contenedor, timeout=60)
        _correr("docker", "network", "rm", f"{self.proyecto}_default", timeout=60)
        warnings.warn(
            f"`compose down` fallo para {self.proyecto} y hubo que forzar la limpieza: "
            f"{derribado.stdout}{derribado.stderr}",
            stacklevel=2,
        )


@pytest.fixture
def escenario(tmp_path: Path, imagenes) -> Escenario:
    caso = Escenario(tmp_path)
    try:
        yield caso
    finally:
        caso.derribar()


async def test_el_despliegue_sano_deja_el_servicio_respondiendo(escenario: Escenario):
    resultado = await escenario.deployer.deploy(escenario.app, SHA_SANO)

    assert resultado.ok, f"el despliegue sano fallo: {resultado.error}"
    assert resultado.rolled_back is False
    assert resultado.tag == "sha-sano"
    assert escenario.responde()
    assert escenario.imagen_viva() == f"{REPO_IMAGEN}:sha-sano"


async def test_el_rollback_restaura_la_version_anterior(escenario: Escenario):
    """El corazon de D-01: un despliegue que no supera el healthcheck vuelve atras."""
    sano = await escenario.deployer.deploy(escenario.app, SHA_SANO)
    assert sano.ok, f"el despliegue previo debia funcionar: {sano.error}"

    roto = await escenario.deployer.deploy(escenario.app, SHA_ROTO)

    assert roto.ok is False, "un contenedor que muere no puede darse por bueno"
    assert roto.rolled_back is True, "deberia haber revertido al tag anterior"
    assert "healthcheck" in roto.error.lower() or "agotado" in roto.error.lower()

    # Lo que de verdad importa: el servicio vuelve a estar en pie, en la version buena.
    assert escenario.imagen_viva() == f"{REPO_IMAGEN}:sha-sano"
    assert escenario.responde(), "tras el rollback el servicio debe volver a responder"


async def test_el_estado_en_disco_no_avanza_al_tag_roto(escenario: Escenario):
    """Un despliegue fallido no puede quedar registrado como la version viva."""
    await escenario.deployer.deploy(escenario.app, SHA_SANO)
    await escenario.deployer.deploy(escenario.app, SHA_ROTO)

    estado = escenario.deployer.read_state(escenario.app)
    assert estado["current_tag"] == "sha-sano", (
        "el tag roto nunca debe persistirse como actual: si lo hiciera, el "
        "siguiente rollback volveria a una version que no funciona"
    )


async def test_encadenar_despliegues_sanos_conserva_el_anterior(escenario: Escenario):
    """El destino del rollback es el ultimo tag que SI paso el healthcheck."""
    await escenario.deployer.deploy(escenario.app, SHA_SANO)
    segundo = await escenario.deployer.deploy(escenario.app, SHA_SANO_2)

    assert segundo.ok
    estado = escenario.deployer.read_state(escenario.app)
    assert estado["current_tag"] == "sha-sano2"
    assert estado["previous_tag"] == "sha-sano"


async def test_sin_version_anterior_no_hay_rollback(escenario: Escenario):
    """El primer despliegue de una app no tiene a donde volver: falla y se detiene."""
    resultado = await escenario.deployer.deploy(escenario.app, SHA_ROTO)

    assert resultado.ok is False
    assert resultado.rolled_back is False, "no habia tag anterior al que volver"
    assert escenario.deployer.read_state(escenario.app) == {}


async def test_el_fallo_queda_registrado_en_el_jsonl(escenario: Escenario):
    """Sin este rastro, un despliegue fallido es invisible (deuda DS-03)."""
    await escenario.deployer.deploy(escenario.app, SHA_SANO)
    await escenario.deployer.deploy(escenario.app, SHA_ROTO)

    registro = (escenario.deployer._settings.log_dir / f"{escenario.app.name}.jsonl")
    lineas = registro.read_text(encoding="utf-8").strip().splitlines()

    assert len(lineas) == 2
    assert '"ok": true' in lineas[0]
    assert '"ok": false' in lineas[1]
    assert '"rolled_back": true' in lineas[1]
