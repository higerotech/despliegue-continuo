"""Receptor de webhooks de GitHub que dispara despliegues locales.

Solo /webhook esta pensado para exponerse a traves del tunel. /status y
/reload exigen bucle local o un token, y /health no revela nada.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from .config import AppConfig, ConfigError, load_apps, load_settings
from .deployer import Deployer
from .events import DeployIntent, IgnoredEvent, parse_event
from .queue import DeployQueue
from .security import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    DeliveryCache,
    SignatureError,
    verify_signature,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("cd.receiver")

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.apps = load_apps(settings.apps_file)
    app.state.deliveries = DeliveryCache(settings.delivery_cache_size)
    app.state.queue = DeployQueue(Deployer(settings), settings.history_size)

    logger.info(
        "receptor listo en %s:%s con %d app(s): %s",
        settings.host, settings.port, len(app.state.apps),
        ", ".join(sorted(a.name for a in app.state.apps.values())),
    )
    try:
        yield
    finally:
        await app.state.queue.drain()
        logger.info("receptor detenido")


app = FastAPI(title="CD Receiver", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)


def _skip(reason: str, **extra) -> JSONResponse:
    """202 con motivo: GitHub lo muestra en Recent Deliveries y ahorra depuracion."""
    logger.info("ignorado: %s", reason)
    return JSONResponse({"status": "ignored", "reason": reason, **extra}, status_code=202)


def _match_app(apps: dict[str, AppConfig], intent: DeployIntent) -> AppConfig | str:
    """Devuelve la app a desplegar o un texto explicando por que no aplica."""
    target = apps.get(intent.repo)
    if target is None:
        return f"el repo {intent.repo} no esta en el inventario de apps"
    if target.event != intent.event:
        return f"{target.name} escucha {target.event!r}, no {intent.event!r}"
    if intent.branch != target.branch:
        return f"rama {intent.branch!r} distinta de la desplegable ({target.branch!r})"
    if target.workflow and intent.workflow != target.workflow:
        return f"workflow {intent.workflow!r} distinto del esperado ({target.workflow!r})"
    return target


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str | None = Header(default=None, alias=EVENT_HEADER),
    x_github_delivery: str | None = Header(default=None, alias=DELIVERY_HEADER),
    x_hub_signature_256: str | None = Header(default=None, alias=SIGNATURE_HEADER),
) -> Response:
    settings = request.app.state.settings

    body = await request.body()
    if len(body) > settings.max_body_bytes:
        return JSONResponse({"status": "error", "reason": "payload demasiado grande"}, status_code=413)

    # La firma se valida ANTES de tocar el JSON: nada sin firmar se parsea.
    try:
        verify_signature(settings.webhook_secret, body, x_hub_signature_256)
    except SignatureError as exc:
        logger.warning("firma rechazada (%s) delivery=%s", exc, x_github_delivery)
        return JSONResponse({"status": "error", "reason": "firma invalida"}, status_code=401)

    if request.app.state.deliveries.seen_before(x_github_delivery):
        return _skip(f"delivery {x_github_delivery} ya procesado")

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"status": "error", "reason": "JSON invalido"}, status_code=400)

    try:
        intent = parse_event(x_github_event or "", payload)
    except IgnoredEvent as exc:
        return _skip(str(exc))

    match = _match_app(request.app.state.apps, intent)
    if isinstance(match, str):
        return _skip(match)

    position = request.app.state.queue.submit(match, intent.sha, x_github_delivery or "manual")
    return JSONResponse(
        {
            "status": "queued",
            "app": match.name,
            "sha": intent.sha,
            "tag": match.tag_for(intent.sha),
            "position": position,
        },
        status_code=202,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def _authorized(request: Request, authorization: str | None) -> bool:
    token = os.environ.get("STATUS_TOKEN", "").strip()
    if token and authorization == f"Bearer {token}":
        return True
    client = request.client.host if request.client else ""
    return client in LOOPBACK


@app.get("/status")
async def status(request: Request, authorization: str | None = Header(default=None)) -> Response:
    if not _authorized(request, authorization):
        return JSONResponse({"status": "error", "reason": "no autorizado"}, status_code=403)

    deployer = Deployer(request.app.state.settings)
    apps = {
        target.name: {
            "repo": target.repo,
            "branch": target.branch,
            "event": target.event,
            **deployer.read_state(target),
        }
        for target in request.app.state.apps.values()
    }
    return JSONResponse({"apps": apps, **request.app.state.queue.snapshot()})


@app.post("/reload")
async def reload(request: Request, authorization: str | None = Header(default=None)) -> Response:
    """Relee apps.yml sin reiniciar el servicio ni cortar despliegues en curso."""
    if not _authorized(request, authorization):
        return JSONResponse({"status": "error", "reason": "no autorizado"}, status_code=403)

    try:
        request.app.state.apps = load_apps(request.app.state.settings.apps_file)
    except ConfigError as exc:
        return JSONResponse({"status": "error", "reason": str(exc)}, status_code=400)

    names = sorted(a.name for a in request.app.state.apps.values())
    logger.info("inventario recargado: %s", ", ".join(names))
    return JSONResponse({"status": "reloaded", "apps": names})
