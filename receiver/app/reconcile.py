"""Reconciliador: recupera los despliegues cuyo webhook no llego (ADR-0011).

GitHub NO reintenta una entrega fallida. Si el tunel, la red o el propio
receptor no responden en 10 segundos, el evento se pierde y el build que
termino bien no se despliega nunca, sin aviso. Ocurrio el 2026-10-23: 19
entregas de un repo murieron por timeout mientras una WAN entraba y salia del
balanceo.

Cada cierto tiempo se pregunta a la API de GitHub por el ultimo run correcto
del workflow de cada app y se compara con lo que corre. La invariante es una
sola: **solo se recupera lo que el receptor no vio; nunca se repite una
decision ya tomada.** Un despliegue que fallo y volvio atras, una alineacion a
mano o un despliegue en cola se respetan tal cual.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from .config import AppConfig, Settings
from .events import IgnoredEvent, match_app, parse_event

logger = logging.getLogger("cd.reconcile")

DESPLEGAR = "desplegar"
AL_DIA = "al dia"
OMITIR = "omitir"

# Los runs se piden por workflow, no por repo: en un monorepo con workflows
# programados (un sondeo cada 15 minutos) los 100 ultimos runs correctos del
# repo son todos del sondeo y el build de una app no aparece nunca. Una pagina
# de su propio workflow basta: lo que se recupera es por definicion reciente.
_POR_PAGINA = 20


@dataclass(frozen=True)
class Decision:
    app: str
    accion: str
    motivo: str
    sha: str | None = None
    run_id: int | None = None


def _instante(texto: Any) -> datetime | None:
    if not texto:
        return None
    try:
        valor = datetime.fromisoformat(str(texto))
    except ValueError:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


def reconciliable(app: AppConfig) -> bool:
    """Solo apps que esperan un workflow con nombre.

    Una app `push` no tiene run que consultar, y una `workflow_run` sin
    `workflow` aceptaria cualquier workflow correcto del repo: reconciliarla
    desplegaria con el primero que pasara, sea o no un build.
    """
    return app.event == "workflow_run" and bool(app.workflow)


def _ultimo_run(app: AppConfig, runs: list[dict]) -> dict | None:
    propios = [
        run for run in runs
        if run.get("name") == app.workflow
        and run.get("conclusion") == "success"
        and run.get("head_branch") == app.branch
        # Un pull_request de un fork cuya rama se llame igual traeria el mismo
        # head_branch. Su imagen no se publica, pero mejor no intentarlo.
        and run.get("event") != "pull_request"
        and str((run.get("head_repository") or {}).get("full_name", "")).lower() == app.repo
    ]
    if not propios:
        return None
    return max(propios, key=lambda run: (str(run.get("created_at", "")), int(run.get("id") or 0)))


def planificar(
    apps: dict[str, AppConfig],
    runs: dict[tuple[str, str], list[dict]],
    errores: dict[tuple[str, str], str],
    estado: Callable[[AppConfig], dict],
    intentados: Callable[[AppConfig], set[str]],
    en_cola: Callable[[str, str], bool],
    ahora: datetime,
    gracia: float,
) -> list[Decision]:
    """Decide, app por app, si hay un despliegue perdido que recuperar.

    Funcion pura: todo lo que necesita se le pasa, para poder probar cada
    regla sin red ni Docker. Las reglas van en orden y la primera que aplica
    decide.
    """
    decisiones: list[Decision] = []
    for app in sorted(apps.values(), key=lambda a: a.name):
        if not reconciliable(app):
            decisiones.append(Decision(
                app.name, OMITIR, "solo se reconcilian apps workflow_run que nombran su workflow"
            ))
            continue

        clave = (app.repo, app.branch)
        if clave in errores:
            decisiones.append(Decision(app.name, OMITIR, f"no se pudo consultar GitHub: {errores[clave]}"))
            continue

        run = _ultimo_run(app, runs.get(clave, []))
        if run is None:
            decisiones.append(Decision(
                app.name, OMITIR, f"sin runs correctos recientes de {app.workflow!r} en {app.branch}"
            ))
            continue

        # El run pasa por el mismo filtro que un webhook. Si el webhook lo
        # habria rechazado, el reconciliador tambien.
        try:
            intent = parse_event("workflow_run", {
                "action": "completed",
                "workflow_run": run,
                "repository": {"full_name": app.repo},
            })
        except IgnoredEvent as exc:
            decisiones.append(Decision(app.name, OMITIR, str(exc), run_id=run.get("id")))
            continue
        destino = match_app(apps, intent)
        if destino is not app:
            motivo = destino if isinstance(destino, str) else f"corresponde a {destino.name}"
            decisiones.append(Decision(app.name, OMITIR, motivo, intent.sha, run.get("id")))
            continue

        sha, run_id = intent.sha, run.get("id")
        actual = estado(app)
        if actual.get("current_tag") == app.tag_for(sha):
            decisiones.append(Decision(app.name, AL_DIA, f"corre {app.tag_for(sha)}", sha, run_id))
            continue
        if sha in intentados(app):
            decisiones.append(Decision(
                app.name, OMITIR, "ese commit ya se intento (ver el historico): no se repite", sha, run_id
            ))
            continue
        if en_cola(app.name, sha):
            decisiones.append(Decision(app.name, OMITIR, "ya esta en la cola", sha, run_id))
            continue

        terminado = _instante(run.get("updated_at"))
        escrito = _instante(actual.get("updated_at"))
        if terminado and escrito and escrito >= terminado:
            # Alguien desplego o alineo esta app DESPUES de que acabara el run:
            # esa decision es mas reciente que el evento y manda.
            decisiones.append(Decision(
                app.name, OMITIR,
                f"el estado ({escrito.isoformat(timespec='seconds')}) es posterior al run: se respeta",
                sha, run_id,
            ))
            continue
        if terminado is None or (ahora - terminado).total_seconds() < gracia:
            decisiones.append(Decision(
                app.name, OMITIR, "el run acaba de terminar: se da margen al webhook", sha, run_id
            ))
            continue

        decisiones.append(Decision(
            app.name, DESPLEGAR,
            f"el run {run_id} termino bien y su webhook no llego (corre {actual.get('current_tag')})",
            sha, run_id,
        ))
    return decisiones


class Reconciliador:
    """Ejecuta `planificar` cada cierto tiempo y encola lo que haga falta."""

    def __init__(
        self,
        settings: Settings,
        apps: Callable[[], dict[str, AppConfig]],
        deployer: Any,
        queue: Any,
        transport: httpx.AsyncBaseTransport | None = None,
        reloj: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._apps = apps
        self._deployer = deployer
        self._queue = queue
        self._transport = transport
        self._reloj = reloj or (lambda: datetime.now(timezone.utc))
        self._errores_avisados: dict[tuple[str, str], str] = {}
        # repo -> {nombre del workflow: ids}. Los ids no cambian al renombrar,
        # asi que se piden una vez y solo se vuelven a pedir si falta un nombre.
        self._workflows: dict[str, dict[str, list[int]]] = {}
        self._ultimo: dict[str, Any] = {}
        self.recuperados = 0

    @property
    def modo(self) -> str:
        return "observar" if self._settings.reconcile_dry_run else "desplegar"

    def consultas_por_hora(self) -> float:
        """Una consulta por workflow y ciclo; la lista de workflows va aparte y se guarda."""
        grupos = {(a.repo, a.branch, a.workflow) for a in self._apps().values() if reconciliable(a)}
        return len(grupos) * 3600 / max(self._settings.reconcile_interval, 1)

    def _cabeceras(self) -> dict[str, str]:
        cabeceras = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "despliegue-continuo-reconciliador",
        }
        if self._settings.reconcile_token:
            cabeceras["Authorization"] = f"Bearer {self._settings.reconcile_token}"
        return cabeceras

    @staticmethod
    def _comprobar(respuesta: httpx.Response) -> dict:
        if respuesta.status_code == 404:
            raise RuntimeError("HTTP 404: el repo no existe o es privado y falta RECONCILE_TOKEN")
        if respuesta.status_code in (403, 429):
            raise RuntimeError(f"HTTP {respuesta.status_code}: limite de la API o token sin permiso")
        respuesta.raise_for_status()
        return respuesta.json()

    async def _ids(self, client: httpx.AsyncClient, repo: str, nombres: set[str]) -> dict[str, list[int]]:
        ids = self._workflows.get(repo)
        if ids is None or not nombres.issubset(ids):
            datos = self._comprobar(await client.get(
                f"/repos/{repo}/actions/workflows", params={"per_page": 100}
            ))
            ids = {}
            for workflow in datos.get("workflows") or []:
                if workflow.get("name") and workflow.get("id"):
                    ids.setdefault(workflow["name"], []).append(int(workflow["id"]))
            self._workflows[repo] = ids
        return ids

    async def _consultar(
        self, client: httpx.AsyncClient, repo: str, rama: str, nombres: set[str]
    ) -> list[dict]:
        ids = await self._ids(client, repo, nombres)
        runs: list[dict] = []
        # Un nombre que no existe en el repo no se consulta: `planificar` lo
        # explica como "sin runs correctos" sin tapar a las demas apps del repo.
        for ident in sorted(i for nombre in nombres for i in ids.get(nombre, [])):
            datos = self._comprobar(await client.get(
                f"/repos/{repo}/actions/workflows/{ident}/runs",
                params={
                    "branch": rama,
                    "status": "success",
                    "per_page": _POR_PAGINA,
                    "exclude_pull_requests": "true",
                },
            ))
            runs.extend(datos.get("workflow_runs") or [])
        return runs

    def _registrar_error(self, clave: tuple[str, str], error: str | None) -> None:
        """Avisa en el log al cambiar el error de un repo, no en cada ciclo."""
        previo = self._errores_avisados.get(clave)
        if error and error != previo:
            logger.warning("reconciliacion: no se pudo consultar %s@%s: %s", *clave, error)
            self._errores_avisados[clave] = error
        elif not error and previo:
            logger.info("reconciliacion: %s@%s vuelve a responder", *clave)
            del self._errores_avisados[clave]

    async def ciclo(self) -> list[Decision]:
        apps = self._apps()
        grupos: dict[tuple[str, str], set[str]] = {}
        for a in apps.values():
            if reconciliable(a):
                grupos.setdefault((a.repo, a.branch), set()).add(str(a.workflow))
        runs: dict[tuple[str, str], list[dict]] = {}
        errores: dict[tuple[str, str], str] = {}

        async with httpx.AsyncClient(
            base_url=self._settings.reconcile_api_url,
            headers=self._cabeceras(),
            timeout=10.0,
            transport=self._transport,
        ) as client:
            for clave in sorted(grupos):
                try:
                    runs[clave] = await self._consultar(client, *clave, grupos[clave])
                except Exception as exc:  # una caida de red no debe parar el resto
                    errores[clave] = str(exc) or type(exc).__name__
                self._registrar_error(clave, errores.get(clave))

        decisiones = planificar(
            apps, runs, errores,
            estado=self._deployer.read_state,
            intentados=self._deployer.attempted_shas,
            en_cola=self._queue.contains,
            ahora=self._reloj(),
            gracia=self._settings.reconcile_grace,
        )

        for decision in decisiones:
            if decision.accion != DESPLEGAR:
                continue
            if self._settings.reconcile_dry_run:
                logger.warning("[observar] se desplegaria %s %s: %s",
                               decision.app, (decision.sha or "")[:7], decision.motivo)
                continue
            logger.warning("entrega perdida: se encola %s %s: %s",
                           decision.app, (decision.sha or "")[:7], decision.motivo)
            self._queue.submit(apps[decision.app], decision.sha, f"reconciliacion-run-{decision.run_id}")
            self.recuperados += 1

        self._ultimo = {
            "ciclo": self._reloj().isoformat(timespec="seconds"),
            "decisiones": [asdict(d) for d in decisiones],
            "errores": {f"{repo}@{rama}": msg for (repo, rama), msg in errores.items()},
        }
        return decisiones

    async def bucle(self) -> None:
        while True:
            await asyncio.sleep(self._settings.reconcile_interval)
            try:
                await self.ciclo()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("el ciclo de reconciliacion fallo de forma inesperada")

    def snapshot(self) -> dict:
        return {
            "activo": True,
            "modo": self.modo,
            "intervalo": self._settings.reconcile_interval,
            "gracia": self._settings.reconcile_grace,
            "con_token": bool(self._settings.reconcile_token),
            "recuperados": self.recuperados,
            **self._ultimo,
        }
