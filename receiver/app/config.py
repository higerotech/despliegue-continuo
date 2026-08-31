"""Carga de configuracion: ajustes del receptor (env) e inventario de apps (YAML)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(RuntimeError):
    """La configuracion es invalida y el receptor no debe arrancar."""


@dataclass(frozen=True)
class AppConfig:
    """Una aplicacion desplegable. Se declara en config/apps.yml."""

    name: str
    repo: str
    project_dir: Path
    image: str
    branch: str = "main"
    event: str = "workflow_run"
    workflow: str | None = None
    compose_file: str = "docker-compose.yml"
    tag_template: str = "sha-{short_sha}"
    services: list[str] = field(default_factory=list)
    health_url: str | None = None
    health_timeout: int = 90
    health_interval: float = 3.0
    rollback: bool = True
    command_timeout: int = 600

    def tag_for(self, sha: str) -> str:
        return self.tag_template.format(sha=sha, short_sha=sha[:7])

    @property
    def compose_path(self) -> Path:
        return self.project_dir / self.compose_file


@dataclass(frozen=True)
class Settings:
    """Ajustes del proceso receptor. Todo viene del entorno."""

    webhook_secret: str
    apps_file: Path
    state_dir: Path
    log_dir: Path
    host: str = "127.0.0.1"
    port: int = 9000
    docker_host: str = "tcp://127.0.0.1:2375"  # socket-proxy, no el socket crudo
    max_body_bytes: int = 1_048_576
    delivery_cache_size: int = 1024
    history_size: int = 50


def _env_int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un entero, recibido {raw!r}") from exc


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)

    secret = env.get("WEBHOOK_SECRET", "").strip()
    if not secret:
        raise ConfigError(
            "WEBHOOK_SECRET no esta definido. Genera uno con "
            "`openssl rand -hex 32` y registralo tambien en GitHub."
        )
    if len(secret) < 32:
        raise ConfigError("WEBHOOK_SECRET debe tener al menos 32 caracteres.")

    return Settings(
        webhook_secret=secret,
        apps_file=Path(env.get("APPS_FILE", "/etc/cd-receiver/apps.yml")),
        state_dir=Path(env.get("STATE_DIR", "/var/lib/cd-receiver")),
        log_dir=Path(env.get("LOG_DIR", "/var/log/cd-receiver")),
        host=env.get("BIND_HOST", "127.0.0.1"),
        port=_env_int(env, "BIND_PORT", 9000),
        docker_host=env.get("DOCKER_HOST", "tcp://127.0.0.1:2375"),
        max_body_bytes=_env_int(env, "MAX_BODY_BYTES", 1_048_576),
        delivery_cache_size=_env_int(env, "DELIVERY_CACHE_SIZE", 1024),
        history_size=_env_int(env, "HISTORY_SIZE", 50),
    )


_REQUIRED_APP_KEYS = ("name", "repo", "project_dir", "image")
_ALLOWED_EVENTS = ("push", "workflow_run")


def load_apps(path: Path) -> dict[str, AppConfig]:
    """Lee apps.yml y devuelve las apps indexadas por `repo` en minusculas.

    Solo se despliega lo que aparece aqui: el inventario ES la allowlist.
    """
    if not path.exists():
        raise ConfigError(f"No existe el inventario de apps: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("apps")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path} debe contener una lista no vacia bajo la clave `apps`.")

    apps: dict[str, AppConfig] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigError(f"apps[{index}] debe ser un mapa.")
        missing = [key for key in _REQUIRED_APP_KEYS if not entry.get(key)]
        if missing:
            raise ConfigError(f"apps[{index}] no define: {', '.join(missing)}")

        event = str(entry.get("event", "workflow_run"))
        if event not in _ALLOWED_EVENTS:
            raise ConfigError(
                f"apps[{index}].event={event!r} no soportado; usa uno de {_ALLOWED_EVENTS}."
            )

        app = AppConfig(
            name=str(entry["name"]),
            repo=str(entry["repo"]).lower(),
            project_dir=Path(str(entry["project_dir"])).expanduser(),
            image=str(entry["image"]),
            branch=str(entry.get("branch", "main")),
            event=event,
            workflow=entry.get("workflow"),
            compose_file=str(entry.get("compose_file", "docker-compose.yml")),
            tag_template=str(entry.get("tag_template", "sha-{short_sha}")),
            services=[str(item) for item in entry.get("services", [])],
            health_url=entry.get("health_url"),
            health_timeout=int(entry.get("health_timeout", 90)),
            health_interval=float(entry.get("health_interval", 3.0)),
            rollback=bool(entry.get("rollback", True)),
            command_timeout=int(entry.get("command_timeout", 600)),
        )
        if app.repo in apps:
            raise ConfigError(f"El repo {app.repo} esta declarado dos veces en {path}.")
        apps[app.repo] = app

    return apps
