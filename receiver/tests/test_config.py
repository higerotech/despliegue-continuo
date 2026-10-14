"""El arranque debe fallar ruidosamente si la configuracion no es segura."""

from __future__ import annotations

import pytest

from app.config import ConfigError, load_apps, load_settings

VALID_ENV = {"WEBHOOK_SECRET": "x" * 40}


class TestSettings:
    def test_falla_sin_secreto(self):
        with pytest.raises(ConfigError, match="WEBHOOK_SECRET"):
            load_settings({})

    def test_falla_con_un_secreto_corto(self):
        with pytest.raises(ConfigError, match="32 caracteres"):
            load_settings({"WEBHOOK_SECRET": "corto"})

    def test_escucha_en_loopback_por_defecto(self):
        # Exponerse en 0.0.0.0 solo puede ser una decision explicita.
        assert load_settings(VALID_ENV).host == "127.0.0.1"

    def test_apunta_al_socket_proxy_por_defecto(self):
        # ADR-0005: nunca unix:///var/run/docker.sock, que exigiria el grupo docker.
        assert load_settings(VALID_ENV).docker_host == "tcp://127.0.0.1:2375"

    def test_el_docker_host_se_puede_sobrescribir(self):
        settings = load_settings(VALID_ENV | {"DOCKER_HOST": "tcp://127.0.0.1:2999"})
        assert settings.docker_host == "tcp://127.0.0.1:2999"

    def test_un_docker_host_vacio_solo_ocurre_si_se_pide_expresamente(self):
        # Vacio significa "usa el cliente del entorno" y salta el socket-proxy.
        # Es una renuncia consciente que usan las pruebas de integracion: nunca
        # debe ser el resultado de olvidar la variable.
        assert load_settings(VALID_ENV).docker_host == "tcp://127.0.0.1:2375"
        assert load_settings(VALID_ENV | {"DOCKER_HOST": ""}).docker_host == ""

    def test_rechaza_un_puerto_no_numerico(self):
        with pytest.raises(ConfigError, match="entero"):
            load_settings(VALID_ENV | {"BIND_PORT": "nueve mil"})


def write_apps(tmp_path, body: str):
    path = tmp_path / "apps.yml"
    path.write_text(body, encoding="utf-8")
    return path


MINIMA = """
apps:
  - name: mi-api
    repo: Higerotech/Mi-API
    project_dir: /srv/apps/mi-api
    image: ghcr.io/higerotech/mi-api
"""


DOS_APPS = """
apps:
  - name: mi-api
    repo: higerotech/monorepo
    project_dir: /srv/apps/mi-api
    image: ghcr.io/higerotech/monorepo/api
    workflow: build-api
  - name: mi-web
    repo: Higerotech/Monorepo
    project_dir: /srv/apps/mi-web
    image: ghcr.io/higerotech/monorepo/web
    workflow: build-web
"""


class TestApps:
    def test_indexa_por_nombre_y_normaliza_el_repo_a_minusculas(self, tmp_path):
        # GitHub no distingue mayusculas en full_name; el inventario tampoco debe.
        apps = load_apps(write_apps(tmp_path, MINIMA))
        assert apps["mi-api"].repo == "higerotech/mi-api"

    def test_valores_por_defecto_seguros(self, tmp_path):
        app = load_apps(write_apps(tmp_path, MINIMA))["mi-api"]
        assert app.branch == "main"
        assert app.event == "workflow_run"
        assert app.rollback is True

    def test_calcula_el_tag_desde_el_sha(self, tmp_path):
        app = load_apps(write_apps(tmp_path, MINIMA))["mi-api"]
        assert app.tag_for("1a2b3c4d5e6f7890") == "sha-1a2b3c4"

    def test_falla_si_falta_un_campo_obligatorio(self, tmp_path):
        body = "apps:\n  - name: mi-api\n    repo: a/b\n"
        with pytest.raises(ConfigError, match="project_dir"):
            load_apps(write_apps(tmp_path, body))

    def test_rechaza_un_evento_no_soportado(self, tmp_path):
        body = MINIMA + "    event: pull_request\n"
        with pytest.raises(ConfigError, match="no soportado"):
            load_apps(write_apps(tmp_path, body))

    def test_rechaza_la_misma_app_dos_veces(self, tmp_path):
        # El nombre es el proyecto compose, el fichero de estado y la cola.
        with pytest.raises(ConfigError, match="dos veces"):
            load_apps(write_apps(tmp_path, MINIMA + MINIMA.replace("apps:", "")))

    def test_un_repo_puede_declarar_varias_apps_con_workflows_distintos(self, tmp_path):
        # ADR-0010: un monorepo con una imagen por servicio.
        apps = load_apps(write_apps(tmp_path, DOS_APPS))
        assert sorted(apps) == ["mi-api", "mi-web"]
        assert {app.repo for app in apps.values()} == {"higerotech/monorepo"}

    def test_rechaza_dos_apps_del_mismo_repo_con_el_mismo_workflow(self, tmp_path):
        # Un evento no puede corresponder a dos apps: se desplegaria una al azar.
        body = DOS_APPS.replace("workflow: build-web", "workflow: build-api")
        with pytest.raises(ConfigError, match="workflow distinto"):
            load_apps(write_apps(tmp_path, body))

    def test_rechaza_varias_apps_en_un_repo_si_alguna_no_nombra_workflow(self, tmp_path):
        # Sin `workflow`, la app aceptaria tambien los eventos de su vecina.
        body = DOS_APPS.replace("    workflow: build-web\n", "")
        with pytest.raises(ConfigError, match="definir `workflow`"):
            load_apps(write_apps(tmp_path, body))

    def test_rechaza_varias_apps_en_un_repo_si_alguna_escucha_push(self, tmp_path):
        body = DOS_APPS.replace("workflow: build-web", "event: push")
        with pytest.raises(ConfigError, match="workflow_run"):
            load_apps(write_apps(tmp_path, body))

    def test_falla_si_el_inventario_esta_vacio(self, tmp_path):
        with pytest.raises(ConfigError, match="lista no vacia"):
            load_apps(write_apps(tmp_path, "apps: []"))

    def test_falla_si_el_inventario_no_existe(self, tmp_path):
        with pytest.raises(ConfigError, match="No existe"):
            load_apps(tmp_path / "no-esta.yml")
