"""El reconciliador — ADR-0011.

El incidente que lo origina: el 2026-10-23 el webhook `completed` de un build
correcto murio por timeout y GitHub no lo reintento. Nadie se entero.

La invariante que estas pruebas defienden: **solo se recupera lo que el
receptor no vio; nunca se repite una decision ya tomada.** Por eso hay una
prueba por cada motivo para NO desplegar, y solo una para desplegar.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.config import AppConfig, load_settings
from app.deployer import Deployer, DeployResult
from app.queue import DeployQueue
from app.reconcile import AL_DIA, DESPLEGAR, OMITIR, Reconciliador, planificar

AHORA = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
SHA_NUEVO = "d365c761f980c95ec5bfef36dbac9808f1cbe480"
SHA_VIEJO = "ae1138e000000000000000000000000000000000"
REPO = "higerotech/morocota"


def hacer_app(nombre="morocota-api", workflow="build-api", **extra) -> AppConfig:
    return AppConfig(
        name=nombre, repo=REPO, project_dir=Path("/srv/apps") / nombre,
        image=f"ghcr.io/{REPO}/{nombre}", workflow=workflow, **extra,
    )


def hacer_run(
    sha=SHA_NUEVO, nombre="build-api", hace=timedelta(minutes=20), run_id=101, **extra
) -> dict:
    terminado = AHORA - hace
    run = {
        "id": run_id,
        "name": nombre,
        "head_sha": sha,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "created_at": (terminado - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "updated_at": terminado.isoformat().replace("+00:00", "Z"),
        "head_repository": {"full_name": REPO},
    }
    run.update(extra)
    return run


def estado_viejo(_app=None) -> dict:
    return {"current_tag": "sha-ae1138e", "updated_at": "2026-10-23T13:00:00+00:00"}


def plan(apps, runs, *, estado=estado_viejo, intentados=lambda a: set(),
         en_cola=lambda n, s: False, errores=None, gracia=120):
    apps = {a.name: a for a in apps}
    return planificar(
        apps, {(REPO, "main"): runs}, errores or {},
        estado=estado, intentados=intentados, en_cola=en_cola,
        ahora=AHORA, gracia=gracia,
    )


def unica(decisiones):
    assert len(decisiones) == 1
    return decisiones[0]


class TestPlanificar:
    def test_un_build_correcto_cuyo_webhook_no_llego_se_despliega(self):
        decision = unica(plan([hacer_app()], [hacer_run()]))
        assert decision.accion == DESPLEGAR
        assert decision.sha == SHA_NUEVO
        assert decision.run_id == 101

    def test_si_ya_corre_ese_tag_esta_al_dia(self):
        decision = unica(plan(
            [hacer_app()], [hacer_run()],
            estado=lambda a: {"current_tag": "sha-d365c76", "updated_at": "2026-10-23T13:00:00+00:00"},
        ))
        assert decision.accion == AL_DIA

    def test_un_commit_que_ya_fallo_y_volvio_atras_no_se_reintenta(self):
        """Sin esta regla, un rollback se repetiria en cada ciclo."""
        decision = unica(plan([hacer_app()], [hacer_run()], intentados=lambda a: {SHA_NUEVO}))
        assert decision.accion == OMITIR
        assert "ya se intento" in decision.motivo

    def test_un_commit_en_cola_no_se_encola_otra_vez(self):
        decision = unica(plan(
            [hacer_app()], [hacer_run()], en_cola=lambda n, s: (n, s) == ("morocota-api", SHA_NUEVO)
        ))
        assert decision.accion == OMITIR
        assert "cola" in decision.motivo

    def test_respeta_una_alineacion_a_mano_posterior_al_run(self):
        """Como la del 2026-10-23: el estado se escribio despues del run."""
        decision = unica(plan(
            [hacer_app()], [hacer_run(hace=timedelta(minutes=20))],
            estado=lambda a: {"current_tag": "sha-ae1138e", "updated_at": "2026-10-23T15:50:00+00:00"},
        ))
        assert decision.accion == OMITIR
        assert "posterior" in decision.motivo

    def test_da_margen_al_webhook_de_un_run_recien_terminado(self):
        decision = unica(plan([hacer_app()], [hacer_run(hace=timedelta(seconds=30))]))
        assert decision.accion == OMITIR
        assert "margen" in decision.motivo

    def test_pasado_el_margen_si_despliega(self):
        decision = unica(plan([hacer_app()], [hacer_run(hace=timedelta(seconds=121))]))
        assert decision.accion == DESPLEGAR

    def test_elige_el_run_correcto_mas_reciente(self):
        runs = [
            hacer_run(sha=SHA_VIEJO, run_id=90, hace=timedelta(hours=3)),
            hacer_run(sha=SHA_NUEVO, run_id=101),
        ]
        assert unica(plan([hacer_app()], runs)).sha == SHA_NUEVO

    @pytest.mark.parametrize("cambio", [
        {"name": "CI"},
        {"conclusion": "failure"},
        {"head_branch": "develop"},
        {"event": "pull_request"},
        {"head_repository": {"full_name": "atacante/morocota"}},
    ], ids=["otro-workflow", "fallido", "otra-rama", "pull-request", "fork"])
    def test_ignora_runs_que_no_son_el_build_de_la_app(self, cambio):
        decision = unica(plan([hacer_app()], [hacer_run(**cambio)]))
        assert decision.accion == OMITIR
        assert "sin runs correctos" in decision.motivo

    def test_en_un_monorepo_cada_app_toma_el_run_de_su_workflow(self):
        apps = [hacer_app("morocota-api", "build-api"), hacer_app("morocota-worker", "build-worker")]
        runs = [hacer_run(nombre="build-api", run_id=1), hacer_run(sha=SHA_VIEJO, nombre="build-worker", run_id=2)]
        decisiones = {d.app: d for d in plan(apps, runs, estado=lambda a: {})}
        assert decisiones["morocota-api"].run_id == 1
        assert decisiones["morocota-worker"].sha == SHA_VIEJO

    def test_no_reconcilia_apps_sin_workflow_ni_apps_push(self):
        apps = [hacer_app(workflow=None), hacer_app("web", workflow=None, event="push")]
        for decision in plan(apps, [hacer_run()]):
            assert decision.accion == OMITIR
            assert "solo se reconcilian" in decision.motivo

    def test_un_error_de_la_api_no_despliega_nada(self):
        decision = unica(plan([hacer_app()], [], errores={(REPO, "main"): "HTTP 404"}))
        assert decision.accion == OMITIR
        assert "HTTP 404" in decision.motivo

    def test_una_app_sin_estado_previo_tambien_se_recupera(self):
        decision = unica(plan([hacer_app()], [hacer_run()], estado=lambda a: {}))
        assert decision.accion == DESPLEGAR


class TestHistoricoDeIntentos:
    def test_lee_los_shas_del_jsonl_y_sobrevive_a_lineas_rotas(self, tmp_path):
        settings = load_settings({
            "WEBHOOK_SECRET": "s" * 48, "STATE_DIR": str(tmp_path / "s"), "LOG_DIR": str(tmp_path / "l"),
        })
        deployer = Deployer(settings)
        app = hacer_app()
        assert deployer.attempted_shas(app) == set()

        deployer._append_log(DeployResult(app=app.name, sha=SHA_VIEJO, tag="sha-ae1138e", ok=True))
        deployer._append_log(DeployResult(app=app.name, sha=SHA_NUEVO, tag="sha-d365c76", ok=False))
        with (tmp_path / "l" / "morocota-api.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{esto no es json\n")

        assert deployer.attempted_shas(app) == {SHA_VIEJO, SHA_NUEVO}


class TestCola:
    async def test_sabe_si_un_commit_esta_esperando_o_en_curso(self):
        class Lento:
            async def deploy(self, app, sha):
                import asyncio
                await asyncio.sleep(0.05)
                return DeployResult(app=app.name, sha=sha, tag=sha, ok=True)

        cola = DeployQueue(Lento())
        app = hacer_app()
        cola.submit(app, "a" * 40, "d1")
        cola.submit(app, "b" * 40, "d2")
        assert cola.contains(app.name, "a" * 40)
        assert cola.contains(app.name, "b" * 40)
        assert not cola.contains(app.name, "c" * 40)
        await cola.drain(timeout=2)
        assert not cola.contains(app.name, "a" * 40)
        assert not cola.contains(app.name, "b" * 40)


# ------------------------------------------------------------- ciclo completo


class ColaFalsa:
    def __init__(self):
        self.encolados: list[tuple[str, str, str]] = []

    def contains(self, app_name, sha):
        return False

    def submit(self, app, sha, delivery_id):
        self.encolados.append((app.name, sha, delivery_id))
        return 1


class DeployerFalso:
    def read_state(self, app):
        return estado_viejo()

    def attempted_shas(self, app):
        return set()


def ajustes(**extra):
    env = {"WEBHOOK_SECRET": "s" * 48, "RECONCILE_INTERVAL": "300"}
    env.update(extra)
    return load_settings(env)


def api(runs: list[dict], estado: int = 200, registro: list | None = None):
    def responder(peticion: httpx.Request) -> httpx.Response:
        if registro is not None:
            registro.append(peticion)
        return httpx.Response(estado, json={"total_count": len(runs), "workflow_runs": runs})
    return httpx.MockTransport(responder)


def reconciliador(transport, cola=None, **extra):
    app = hacer_app()
    return Reconciliador(
        ajustes(**extra), apps=lambda: {app.name: app}, deployer=DeployerFalso(),
        queue=cola if cola is not None else ColaFalsa(), transport=transport, reloj=lambda: AHORA,
    )


class TestCiclo:
    async def test_encola_lo_perdido_con_un_delivery_que_dice_de_donde_viene(self):
        cola = ColaFalsa()
        rec = reconciliador(api([hacer_run()]), cola)
        await rec.ciclo()
        assert cola.encolados == [("morocota-api", SHA_NUEVO, "reconciliacion-run-101")]
        assert rec.snapshot()["recuperados"] == 1

    async def test_en_modo_observar_no_encola(self):
        cola = ColaFalsa()
        rec = reconciliador(api([hacer_run()]), cola, RECONCILE_DRY_RUN="true")
        decisiones = await rec.ciclo()
        assert decisiones[0].accion == DESPLEGAR
        assert cola.encolados == []
        assert rec.snapshot()["modo"] == "observar"

    async def test_consulta_la_rama_solo_runs_correctos_y_manda_el_token(self):
        registro: list[httpx.Request] = []
        await reconciliador(api([], registro=registro), RECONCILE_TOKEN="ghp_x").ciclo()
        (peticion,) = registro
        assert peticion.url.path == f"/repos/{REPO}/actions/runs"
        assert peticion.url.params["branch"] == "main"
        assert peticion.url.params["status"] == "success"
        assert peticion.headers["Authorization"] == "Bearer ghp_x"

    async def test_sin_token_no_manda_cabecera_de_autorizacion(self):
        registro: list[httpx.Request] = []
        await reconciliador(api([], registro=registro)).ciclo()
        assert "Authorization" not in registro[0].headers

    async def test_un_repo_privado_sin_token_queda_registrado_y_no_rompe_el_ciclo(self, caplog):
        cola = ColaFalsa()
        rec = reconciliador(api([], estado=404), cola)
        await rec.ciclo()
        await rec.ciclo()
        assert cola.encolados == []
        assert "falta RECONCILE_TOKEN" in rec.snapshot()["errores"][f"{REPO}@main"]
        # El aviso sale una vez, no en cada ciclo.
        assert sum("no se pudo consultar" in r.message for r in caplog.records) == 1

    async def test_una_red_caida_tampoco_rompe_el_ciclo(self):
        def caido(peticion):
            raise httpx.ConnectTimeout("sin red", request=peticion)
        rec = reconciliador(httpx.MockTransport(caido))
        decisiones = await rec.ciclo()
        assert decisiones[0].accion == OMITIR
        assert rec.snapshot()["errores"]

    async def test_el_token_no_aparece_en_el_estado_publicado(self):
        rec = reconciliador(api([hacer_run()]), RECONCILE_TOKEN="ghp_secreto")
        await rec.ciclo()
        publicado = json.dumps(rec.snapshot())
        assert "ghp_secreto" not in publicado
        assert rec.snapshot()["con_token"] is True


class TestAjustes:
    def test_desactivado_por_defecto(self):
        settings = load_settings({"WEBHOOK_SECRET": "s" * 48})
        assert settings.reconcile_interval == 0
        assert settings.reconcile_dry_run is False

    def test_rechaza_un_intervalo_demasiado_corto(self):
        from app.config import ConfigError
        with pytest.raises(ConfigError):
            ajustes(RECONCILE_INTERVAL="10")

    def test_rechaza_un_booleano_ambiguo(self):
        from app.config import ConfigError
        with pytest.raises(ConfigError):
            ajustes(RECONCILE_DRY_RUN="quizas")

    def test_normaliza_la_url_de_la_api(self):
        assert ajustes(RECONCILE_API_URL="https://ghe.example/api/v3/").reconcile_api_url == (
            "https://ghe.example/api/v3"
        )


class TestRobustez:
    def test_un_run_sin_head_sha_se_omite_con_el_motivo_del_webhook(self):
        decision = unica(plan([hacer_app()], [hacer_run(head_sha="")]))
        assert decision.accion == OMITIR
        assert "head_sha" in decision.motivo

    async def test_avisa_cuando_un_repo_vuelve_a_responder(self, caplog):
        respuestas = iter([404, 200])

        def alterna(peticion):
            return httpx.Response(next(respuestas), json={"workflow_runs": []})

        caplog.set_level("INFO", logger="cd.reconcile")
        rec = reconciliador(httpx.MockTransport(alterna))
        await rec.ciclo()
        await rec.ciclo()
        assert any("vuelve a responder" in r.message for r in caplog.records)
        assert rec.snapshot()["errores"] == {}

    async def test_el_bucle_sobrevive_a_un_ciclo_que_revienta(self, monkeypatch):
        import asyncio

        import app.reconcile as modulo

        vueltas = []
        dormir = asyncio.sleep  # el original: se parchea el modulo asyncio entero

        async def sin_espera(_segundos):
            if len(vueltas) >= 2:
                raise asyncio.CancelledError
            await dormir(0)

        async def revienta():
            vueltas.append(1)
            raise RuntimeError("fallo inesperado")

        monkeypatch.setattr(modulo.asyncio, "sleep", sin_espera)
        rec = reconciliador(api([]))
        monkeypatch.setattr(rec, "ciclo", revienta)
        with pytest.raises(asyncio.CancelledError):
            await rec.bucle()
        assert len(vueltas) == 2
