"""Un payload solo se convierte en despliegue si cumple todas las condiciones."""

from __future__ import annotations

import pytest

from app.events import IgnoredEvent, parse_event

REPO = {"repository": {"full_name": "Higerotech/mi-api"}}


def workflow_run(**overrides):
    run = {
        "head_branch": "main",
        "head_sha": "1234567890abcdef1234567890abcdef12345678",
        "conclusion": "success",
        "name": "build",
    }
    run.update(overrides.pop("run", {}))
    return {"action": overrides.pop("action", "completed"), "workflow_run": run, **REPO}


class TestWorkflowRun:
    def test_extrae_la_intencion_de_un_run_correcto(self):
        intent = parse_event("workflow_run", workflow_run())
        assert intent.repo == "higerotech/mi-api"  # normalizado a minusculas
        assert intent.branch == "main"
        assert intent.sha.startswith("1234567")
        assert intent.workflow == "build"

    @pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", None])
    def test_no_despliega_si_el_workflow_no_tuvo_exito(self, conclusion):
        with pytest.raises(IgnoredEvent):
            parse_event("workflow_run", workflow_run(run={"conclusion": conclusion}))

    def test_no_despliega_si_el_run_solo_ha_empezado(self):
        with pytest.raises(IgnoredEvent):
            parse_event("workflow_run", workflow_run(action="requested"))


class TestPush:
    def test_extrae_la_rama_y_el_commit(self):
        intent = parse_event(
            "push", {"ref": "refs/heads/main", "after": "a" * 40, "deleted": False, **REPO}
        )
        assert intent.branch == "main"
        assert intent.event == "push"

    def test_ignora_las_etiquetas(self):
        with pytest.raises(IgnoredEvent):
            parse_event("push", {"ref": "refs/tags/v1.0.0", "after": "a" * 40, **REPO})

    def test_ignora_el_borrado_de_una_rama(self):
        with pytest.raises(IgnoredEvent):
            parse_event(
                "push", {"ref": "refs/heads/tmp", "after": "0" * 40, "deleted": True, **REPO}
            )


@pytest.mark.parametrize("event", ["ping", "issues", "star", "pull_request", ""])
def test_los_eventos_no_manejados_se_ignoran_sin_error(event):
    with pytest.raises(IgnoredEvent):
        parse_event(event, REPO)


def test_un_payload_sin_repositorio_se_ignora():
    with pytest.raises(IgnoredEvent):
        parse_event("push", {"ref": "refs/heads/main", "after": "a" * 40})
