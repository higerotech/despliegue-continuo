"""Traduccion de payloads de GitHub a una intencion de despliegue normalizada."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_EMPTY_SHA = "0" * 40


@dataclass(frozen=True)
class DeployIntent:
    """Lo unico que el resto del sistema necesita saber del webhook."""

    repo: str
    branch: str
    sha: str
    event: str
    workflow: str | None = None


class IgnoredEvent(Exception):
    """El evento es legitimo pero no debe disparar un despliegue."""


def parse_event(event: str, payload: dict[str, Any]) -> DeployIntent:
    """Devuelve la intencion de despliegue o lanza IgnoredEvent con el motivo."""
    if event == "ping":
        raise IgnoredEvent("ping de verificacion de GitHub")
    if event == "push":
        return _parse_push(payload)
    if event == "workflow_run":
        return _parse_workflow_run(payload)
    raise IgnoredEvent(f"evento {event!r} no manejado")


def _repo_name(payload: dict[str, Any]) -> str:
    repo = (payload.get("repository") or {}).get("full_name")
    if not repo:
        raise IgnoredEvent("el payload no identifica el repositorio")
    return str(repo).lower()


def _parse_push(payload: dict[str, Any]) -> DeployIntent:
    ref = str(payload.get("ref", ""))
    if not ref.startswith("refs/heads/"):
        raise IgnoredEvent(f"ref {ref!r} no es una rama")
    if payload.get("deleted"):
        raise IgnoredEvent("push de borrado de rama")

    sha = str(payload.get("after", ""))
    if not sha or sha == _EMPTY_SHA:
        raise IgnoredEvent("push sin commit destino")

    return DeployIntent(
        repo=_repo_name(payload),
        branch=ref.removeprefix("refs/heads/"),
        sha=sha,
        event="push",
    )


def _parse_workflow_run(payload: dict[str, Any]) -> DeployIntent:
    if payload.get("action") != "completed":
        raise IgnoredEvent(f"workflow_run action={payload.get('action')!r}")

    run = payload.get("workflow_run") or {}
    conclusion = run.get("conclusion")
    if conclusion != "success":
        raise IgnoredEvent(f"workflow_run conclusion={conclusion!r}")

    sha = str(run.get("head_sha", ""))
    if not sha:
        raise IgnoredEvent("workflow_run sin head_sha")

    return DeployIntent(
        repo=_repo_name(payload),
        branch=str(run.get("head_branch") or ""),
        sha=sha,
        event="workflow_run",
        workflow=run.get("name"),
    )
