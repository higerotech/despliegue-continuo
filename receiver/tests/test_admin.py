"""Endpoints de administracion y bordes del webhook.

`/status` y `/reload` no se publican por el tunel (ADR-0004), pero el receptor
no puede confiar en esa unica capa: tambien exige origen loopback o token. Aqui
se verifica esa segunda barrera, que es la que sigue en pie si alguien cambia
la configuracion del tunel.

El TestClient se presenta con host `testclient`, que no es loopback, asi que
por defecto estas pruebas ven el mismo rechazo que veria Internet.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app
from app.security import compute_signature

SECRET = "s" * 48
TOKEN = "t" * 32

APPS_YML = """
apps:
  - name: mi-api
    repo: higerotech/mi-api
    project_dir: /srv/apps/mi-api
    image: ghcr.io/higerotech/mi-api
    branch: main
    event: workflow_run
    workflow: build
"""


class ColaFalsa:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, str]] = []

    def submit(self, app, sha, delivery_id):
        self.submitted.append((app.name, sha))
        return len(self.submitted)

    def snapshot(self):
        return {"running": {}, "pending": {"mi-api": 2}, "history": [{"app": "mi-api", "ok": True}]}

    async def drain(self, timeout: float = 30.0):
        return None


def montar(tmp_path, monkeypatch, **entorno):
    apps_file = tmp_path / "apps.yml"
    apps_file.write_text(APPS_YML, encoding="utf-8")

    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("APPS_FILE", str(apps_file))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("STATUS_TOKEN", raising=False)
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)
    return apps_file


@pytest.fixture
def client(tmp_path, monkeypatch):
    montar(tmp_path, monkeypatch)
    with TestClient(fastapi_app) as c:
        fastapi_app.state.queue = ColaFalsa()
        yield c


@pytest.fixture
def client_con_token(tmp_path, monkeypatch):
    apps_file = montar(tmp_path, monkeypatch, STATUS_TOKEN=TOKEN)
    with TestClient(fastapi_app) as c:
        fastapi_app.state.queue = ColaFalsa()
        c.apps_file = apps_file
        yield c


AUTORIZADO = {"Authorization": f"Bearer {TOKEN}"}


class TestStatus:
    def test_sin_autorizacion_responde_403(self, client):
        respuesta = client.get("/status")
        assert respuesta.status_code == 403
        assert respuesta.json()["reason"] == "no autorizado"

    def test_un_token_incorrecto_no_sirve(self, client_con_token):
        respuesta = client_con_token.get("/status", headers={"Authorization": "Bearer noesel"})
        assert respuesta.status_code == 403

    def test_con_token_devuelve_inventario_cola_e_historico(self, client_con_token):
        cuerpo = client_con_token.get("/status", headers=AUTORIZADO).json()

        assert cuerpo["apps"]["mi-api"]["repo"] == "higerotech/mi-api"
        assert cuerpo["apps"]["mi-api"]["branch"] == "main"
        assert cuerpo["pending"] == {"mi-api": 2}
        assert cuerpo["history"][0]["app"] == "mi-api"


class TestReload:
    def test_sin_autorizacion_responde_403(self, client):
        assert client.post("/reload").status_code == 403

    def test_recarga_el_inventario(self, client_con_token):
        respuesta = client_con_token.post("/reload", headers=AUTORIZADO)
        assert respuesta.status_code == 200
        assert respuesta.json() == {"status": "reloaded", "apps": ["mi-api"]}

    def test_ve_una_app_nueva_sin_reiniciar(self, client_con_token):
        client_con_token.apps_file.write_text(
            APPS_YML
            + """
  - name: web
    repo: higerotech/web
    project_dir: /srv/apps/web
    image: ghcr.io/higerotech/web
""",
            encoding="utf-8",
        )
        respuesta = client_con_token.post("/reload", headers=AUTORIZADO)
        assert respuesta.json()["apps"] == ["mi-api", "web"]

    def test_un_inventario_roto_no_tumba_el_inventario_vigente(self, client_con_token):
        """Recargar basura no puede dejar al receptor sin apps desplegables."""
        client_con_token.apps_file.write_text("apps: []", encoding="utf-8")

        respuesta = client_con_token.post("/reload", headers=AUTORIZADO)
        assert respuesta.status_code == 400
        assert "lista no vacia" in respuesta.json()["reason"]

        # El inventario anterior sigue en pie.
        vigente = client_con_token.get("/status", headers=AUTORIZADO).json()
        assert "mi-api" in vigente["apps"]


class TestBordesDelWebhook:
    def _firmar(self, client, cuerpo: bytes, evento="workflow_run", delivery="d-1"):
        return client.post(
            "/webhook",
            content=cuerpo,
            headers={
                "X-GitHub-Event": evento,
                "X-GitHub-Delivery": delivery,
                "X-Hub-Signature-256": compute_signature(SECRET, cuerpo),
                "Content-Type": "application/json",
            },
        )

    def test_un_cuerpo_demasiado_grande_se_rechaza_antes_de_parsear(self, tmp_path, monkeypatch):
        montar(tmp_path, monkeypatch, MAX_BODY_BYTES="300")
        with TestClient(fastapi_app) as client:
            fastapi_app.state.queue = ColaFalsa()
            grande = json.dumps({"relleno": "x" * 1000}).encode()

            respuesta = self._firmar(client, grande)

            assert respuesta.status_code == 413
            assert fastapi_app.state.queue.submitted == []

    def test_json_invalido_con_firma_valida_da_400(self, client):
        respuesta = self._firmar(client, b"{esto no es json")
        assert respuesta.status_code == 400
        assert respuesta.json()["reason"] == "JSON invalido"

    def test_un_push_a_una_app_que_escucha_workflow_run_se_ignora(self, client):
        """El inventario decide el tipo de evento; un push no cuela por su cuenta."""
        cuerpo = json.dumps(
            {
                "ref": "refs/heads/main",
                "after": "a" * 40,
                "deleted": False,
                "repository": {"full_name": "higerotech/mi-api"},
            }
        ).encode()

        respuesta = self._firmar(client, cuerpo, evento="push")

        assert respuesta.status_code == 202
        assert respuesta.json()["status"] == "ignored"
        assert "escucha" in respuesta.json()["reason"]
        assert fastapi_app.state.queue.submitted == []
