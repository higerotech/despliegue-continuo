"""Ejecucion de un despliegue: pull de la imagen, arranque, healthcheck y rollback."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import AppConfig, Settings


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    detail: str = ""


@dataclass
class DeployResult:
    app: str
    sha: str
    tag: str
    ok: bool
    rolled_back: bool = False
    started_at: str = ""
    seconds: float = 0.0
    steps: list[Step] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "app": self.app,
            "sha": self.sha,
            "tag": self.tag,
            "ok": self.ok,
            "rolled_back": self.rolled_back,
            "started_at": self.started_at,
            "seconds": round(self.seconds, 2),
            "error": self.error,
            "steps": [
                {"name": s.name, "ok": s.ok, "seconds": round(s.seconds, 2), "detail": s.detail}
                for s in self.steps
            ],
        }


class CommandError(RuntimeError):
    def __init__(self, command: str, code: int, output: str) -> None:
        super().__init__(f"{command} fallo con codigo {code}")
        self.command = command
        self.code = code
        self.output = output


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


class Deployer:
    """Despliega una app y deja rastro en disco de que version esta viva."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        settings.log_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- estado

    def _state_file(self, app: AppConfig) -> Path:
        return self._settings.state_dir / f"{app.name}.json"

    def read_state(self, app: AppConfig) -> dict:
        path = self._state_file(app)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_state(self, app: AppConfig, current_tag: str, previous_tag: str | None) -> None:
        payload = {
            "app": app.name,
            "current_tag": current_tag,
            "previous_tag": previous_tag,
            "updated_at": _now(),
        }
        path = self._state_file(app)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)

    # -------------------------------------------------------------- comandos

    async def _run(self, app: AppConfig, args: list[str], tag: str) -> str:
        """Ejecuta un comando en el directorio de la app con IMAGE_TAG inyectado.

        Se usa exec (lista de argumentos, sin shell) para que nada de lo que
        llega en el webhook pueda convertirse en un comando.
        """
        env = os.environ.copy()
        env["IMAGE_TAG"] = tag
        env["IMAGE"] = app.image
        # El cliente habla con el socket-proxy, nunca con /var/run/docker.sock:
        # asi el proceso no necesita pertenecer al grupo docker (ADR-0005).
        env["DOCKER_HOST"] = self._settings.docker_host

        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(app.project_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=app.command_timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise CommandError(
                " ".join(args), -1, f"timeout tras {app.command_timeout}s"
            ) from None

        output = (stdout or b"").decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise CommandError(" ".join(args), process.returncode or -1, output)
        return output

    def _compose(self, app: AppConfig, *args: str) -> list[str]:
        return ["docker", "compose", "-f", str(app.compose_path), "-p", app.name, *args]

    async def _bring_up(self, app: AppConfig, tag: str) -> None:
        await self._run(app, self._compose(app, "pull", *app.services), tag)
        await self._run(app, self._compose(app, "up", "-d", "--remove-orphans"), tag)

    # ----------------------------------------------------------- healthcheck

    async def _wait_healthy(self, app: AppConfig) -> str:
        if not app.health_url:
            return "sin health_url configurada, se omite"

        deadline = time.monotonic() + app.health_timeout
        last_error = "sin intentos"
        async with httpx.AsyncClient(timeout=5.0) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(app.health_url)
                    if response.status_code < 400:
                        return f"{app.health_url} respondio {response.status_code}"
                    last_error = f"HTTP {response.status_code}"
                except httpx.HTTPError as exc:
                    last_error = type(exc).__name__
                await asyncio.sleep(app.health_interval)

        raise RuntimeError(f"healthcheck agotado tras {app.health_timeout}s ({last_error})")

    # ------------------------------------------------------------ despliegue

    async def deploy(self, app: AppConfig, sha: str) -> DeployResult:
        tag = app.tag_for(sha)
        previous_tag = self.read_state(app).get("current_tag")

        result = DeployResult(app=app.name, sha=sha, tag=tag, ok=False, started_at=_now())
        started = time.monotonic()

        async def step(name: str, coro) -> None:
            begin = time.monotonic()
            try:
                detail = await coro
            except Exception:
                result.steps.append(Step(name, False, time.monotonic() - begin))
                raise
            result.steps.append(
                Step(name, True, time.monotonic() - begin, str(detail or "")[:400])
            )

        try:
            if not app.compose_path.exists():
                raise RuntimeError(f"no existe {app.compose_path}")

            await step(f"desplegar {tag}", self._bring_up(app, tag))
            await step("healthcheck", self._wait_healthy(app))

            result.ok = True
            self._write_state(app, current_tag=tag, previous_tag=previous_tag)

        except Exception as exc:
            result.error = self._describe(exc)
            if app.rollback and previous_tag and previous_tag != tag:
                result.rolled_back = await self._rollback(app, previous_tag, result)

        result.seconds = time.monotonic() - started
        self._append_log(result)
        return result

    async def _rollback(self, app: AppConfig, previous_tag: str, result: DeployResult) -> bool:
        begin = time.monotonic()
        name = f"rollback a {previous_tag}"
        try:
            await self._bring_up(app, previous_tag)
        except Exception as exc:
            result.steps.append(
                Step(name, False, time.monotonic() - begin, self._describe(exc))
            )
            return False
        result.steps.append(
            Step(name, True, time.monotonic() - begin, "servicio restaurado a la version anterior")
        )
        return True

    @staticmethod
    def _describe(exc: Exception) -> str:
        if isinstance(exc, CommandError):
            return f"{exc}\n{_tail(exc.output)}"
        return f"{type(exc).__name__}: {exc}"

    def _append_log(self, result: DeployResult) -> None:
        path = self._settings.log_dir / f"{result.app}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.as_dict(), ensure_ascii=False) + "\n")
