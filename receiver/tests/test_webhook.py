"""Pruebas de extremo a extremo del endpoint, con el despliegue real sustituido."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app
from app.security import compute_signature

SECRET = "s" * 48
SHA = "1234567890abcdef1234567890abcdef12345678"

APPS_YML = """
apps:
  - name: mi-api
    repo: jeremialcala/mi-api
    project_dir: /srv/apps/mi-api
    image: ghcr.io/jeremialcala/mi-api
    branch: main
    event: workflow_run
    workflow: build
"""


class FakeQueue:
    """Registra lo que se le encola en vez de tocar Docker."""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, str]] = []

    def submit(self, app, sha, delivery_id):
        self.submitted.append((app.name, sha))
        return len(self.submitted)

    def snapshot(self):
        return {"running": {}, "pending": {}, "history": []}

    async def drain(self, timeout: float = 30.0):
        return None


@pytest.fixture
def client(tmp_path, monkeypatch):
    apps_file = tmp_path / "apps.yml"
    apps_file.write_text(APPS_YML, encoding="utf-8")

    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("APPS_FILE", str(apps_file))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))

    with TestClient(fastapi_app) as test_client:
        fastapi_app.state.queue = FakeQueue()
        yield test_client


def post(client, payload: dict, event: str = "workflow_run", delivery: str = "d-1", secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": compute_signature(secret, body),
            "Content-Type": "application/json",
        },
    )


def success_payload(branch: str = "main", workflow: str = "build", repo: str = "Jeremialcala/mi-api"):
    return {
        "action": "completed",
        "workflow_run": {
            "head_branch": branch,
            "head_sha": SHA,
            "conclusion": "success",
            "name": workflow,
        },
        "repository": {"full_name": repo},
    }


def test_encola_el_despliegue_cuando_todo_encaja(client):
    response = post(client, success_payload())
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["tag"] == "sha-1234567"
    assert fastapi_app.state.queue.submitted == [("mi-api", SHA)]


def test_rechaza_una_firma_de_otro_secreto(client):
    response = post(client, success_payload(), secret="otro-secreto-completamente-distinto")
    assert response.status_code == 401
    assert fastapi_app.state.queue.submitted == []


def test_rechaza_una_peticion_sin_firma(client):
    response = client.post("/webhook", json=success_payload(), headers={"X-GitHub-Event": "push"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "payload_kwargs",
    [
        {"branch": "develop"},          # rama no desplegable
        {"workflow": "tests"},          # workflow distinto del declarado
        {"repo": "otro/repo"},          # repo fuera del inventario
    ],
)
def test_ignora_lo_que_no_esta_declarado(client, payload_kwargs):
    response = post(client, success_payload(**payload_kwargs))
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    assert fastapi_app.state.queue.submitted == []


def test_la_reentrega_del_mismo_delivery_no_despliega_dos_veces(client):
    assert post(client, success_payload(), delivery="d-42").json()["status"] == "queued"
    repeated = post(client, success_payload(), delivery="d-42")
    assert repeated.json()["status"] == "ignored"
    assert len(fastapi_app.state.queue.submitted) == 1


def test_el_ping_de_github_no_dispara_nada(client):
    response = post(client, {"zen": "Non-blocking is better."}, event="ping")
    assert response.status_code == 202
    assert fastapi_app.state.queue.submitted == []


def test_health_responde_sin_autenticacion(client):
    assert client.get("/health").json() == {"status": "ok"}
