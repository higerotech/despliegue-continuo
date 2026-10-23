"""Receptor de webhooks de GitHub que dispara despliegues locales.

Solo /webhook esta pensado para exponerse a traves del tunel. /status y
/reload exigen bucle local o un token, y /health no revela nada.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from .config import AppConfig, ConfigError, load_apps, load_settings
from .deployer import Deployer
from .events import IgnoredEvent, match_app, parse_event
from .notify import Notificador
from .queue import DeployQueue
from .reconcile import Reconciliador
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
# httpx registra cada peticion con su URL completa en INFO. Con el reconciliador
# serian cientos de lineas al dia, y la URL del canal de avisos puede llevar un
# secreto (el tema de ntfy, el token de un webhook de Slack).
logging.getLogger("httpx").setLevel(logging.WARNING)

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.apps = load_apps(settings.apps_file)
    app.state.deliveries = DeliveryCache(settings.delivery_cache_size)
    notificador = Notificador(
        settings.notify_url, settings.notify_on, settings.notify_timeout
    )
    deployer = Deployer(settings)
    app.state.queue = DeployQueue(deployer, settings.history_size, notificador)
    if not notificador.habilitado:
        logger.info("canal de avisos desactivado (NOTIFY_URL vacia)")

    app.state.reconciliador = None
    tarea_reconciliacion = None
    if settings.reconcile_interval:
        # Lee el inventario y la cola de app.state en cada ciclo, no una copia
        # tomada al arrancar: asi ve el inventario que deje cada /reload.
        reconciliador = Reconciliador(
            settings,
            apps=lambda: app.state.apps,
            deployer=deployer,
            queue=_ColaVigente(app),
        )
        app.state.reconciliador = reconciliador
        tarea_reconciliacion = asyncio.create_task(
            reconciliador.bucle(), name="reconciliador"
        )
        logger.info(
            "reconciliador activo: cada %ss, modo %s, %s token",
            settings.reconcile_interval, reconciliador.modo,
            "con" if settings.reconcile_token else "sin",
        )
        if not settings.reconcile_token and reconciliador.consultas_por_hora() > 50:
            logger.warning(
                "sin RECONCILE_TOKEN el limite es de 60 consultas/h y esta "
                "configuracion hace %.0f: sube RECONCILE_INTERVAL o define un token",
                reconciliador.consultas_por_hora(),
            )
    else:
        logger.info("reconciliador desactivado (RECONCILE_INTERVAL=0)")

    logger.info(
        "receptor listo en %s:%s con %d app(s): %s",
        settings.host, settings.port, len(app.state.apps),
        ", ".join(sorted(a.name for a in app.state.apps.values())),
    )
    try:
        yield
    finally:
        # Primero el reconciliador: que no encole nada mientras se vacia la cola.
        if tarea_reconciliacion is not None:
            tarea_reconciliacion.cancel()
            await asyncio.gather(tarea_reconciliacion, return_exceptions=True)
        await app.state.queue.drain()
        logger.info("receptor detenido")


class _ColaVigente:
    """Delega en la cola que haya en app.state en cada momento."""

    def __init__(self, fastapi_app: FastAPI) -> None:
        self._app = fastapi_app

    def contains(self, app_name: str, sha: str) -> bool:
        return self._app.state.queue.contains(app_name, sha)

    def submit(self, target: AppConfig, sha: str, delivery_id: str) -> int:
        return self._app.state.queue.submit(target, sha, delivery_id)


app = FastAPI(title="CD Receiver", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)


def _skip(reason: str, **extra) -> JSONResponse:
    """202 con motivo: GitHub lo muestra en Recent Deliveries y ahorra depuracion."""
    logger.info("ignorado: %s", reason)
    return JSONResponse({"status": "ignored", "reason": reason, **extra}, status_code=202)


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

    match = match_app(request.app.state.apps, intent)
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
    reconciliador = request.app.state.reconciliador
    return JSONResponse({
        "apps": apps,
        **request.app.state.queue.snapshot(),
        "reconciliacion": reconciliador.snapshot() if reconciliador else {"activo": False},
    })


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
