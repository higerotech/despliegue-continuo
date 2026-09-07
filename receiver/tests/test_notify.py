"""El aviso de despliegue — deuda DS-03.

Dos propiedades importan mas que el envio en si:

* **Que solo suene cuando importa.** Un canal que avisa de cada despliegue
  correcto se acaba silenciando, y entonces tampoco suena cuando falla.
* **Que un fallo al notificar no toque el despliegue.** El aviso es
  observabilidad; que el canal este caido no puede convertirse en un incidente.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app.deployer import DeployResult
from app.notify import SIEMPRE, SOLO_FALLOS, Notificador

URL = "https://avisos.example/hook"


def resultado(ok=True, rolled_back=False, previous_tag=None, error="") -> DeployResult:
    return DeployResult(
        app="mi-api", sha="1a2b3c4d" * 5, tag="sha-1a2b3c4", ok=ok,
        rolled_back=rolled_back, previous_tag=previous_tag, seconds=12.34, error=error,
    )


class Espia:
    """Sustituye a httpx.AsyncClient y registra lo enviado."""

    def __init__(self, status=200, excepcion=None):
        self.status = status
        self.excepcion = excepcion
        self.enviado: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json):
        if self.excepcion:
            raise self.excepcion
        self.enviado.append({"url": url, "json": json})
        return httpx.Response(self.status, request=httpx.Request("POST", url))


@pytest.fixture
def espia(monkeypatch):
    doble = Espia()
    monkeypatch.setattr("app.notify.httpx.AsyncClient", doble)
    return doble


class TestCuandoAvisa:
    async def test_sin_url_no_avisa_de_nada(self, espia):
        assert await Notificador("").enviar(resultado(ok=False)) is False
        assert espia.enviado == []

    async def test_por_defecto_calla_ante_un_despliegue_correcto(self, espia):
        assert await Notificador(URL).enviar(resultado(ok=True)) is False
        assert espia.enviado == []

    async def test_por_defecto_avisa_de_un_fallo(self, espia):
        assert await Notificador(URL).enviar(resultado(ok=False)) is True
        assert len(espia.enviado) == 1

    async def test_por_defecto_avisa_de_un_rollback(self, espia):
        """Un rollback deja el servicio en pie, pero el despliegue no entro:
        el operador tiene que enterarse igual."""
        r = resultado(ok=False, rolled_back=True, previous_tag="sha-anterior")
        assert await Notificador(URL).enviar(r) is True

    async def test_con_always_avisa_tambien_de_los_correctos(self, espia):
        assert await Notificador(URL, cuando=SIEMPRE).enviar(resultado(ok=True)) is True

    def test_un_modo_desconocido_cae_en_el_conservador(self):
        # Una errata en la configuracion no debe convertirse en spam.
        assert Notificador(URL, cuando="loquesea").interesa(resultado(ok=True)) is False
        assert Notificador(URL, cuando="loquesea").interesa(resultado(ok=False)) is True


class TestResumen:
    def test_el_correcto_dice_tag_y_duracion(self):
        texto = Notificador(URL).resumen(resultado(ok=True))
        assert "sha-1a2b3c4" in texto and "12.3s" in texto

    def test_el_rollback_dice_a_donde_volvio(self):
        texto = Notificador(URL).resumen(
            resultado(ok=False, rolled_back=True, previous_tag="sha-buena")
        )
        assert "sha-buena" in texto
        assert "en pie" in texto, "debe dejar claro que el servicio sigue vivo"

    def test_el_fallo_sin_rollback_avisa_de_que_puede_estar_caido(self):
        texto = Notificador(URL).resumen(resultado(ok=False, rolled_back=False))
        assert "NO se pudo revertir" in texto
        assert "caido" in texto


class TestCuerpo:
    async def test_lleva_text_y_content_para_slack_y_discord(self, espia):
        await Notificador(URL).enviar(resultado(ok=False), "d-1")
        cuerpo = espia.enviado[0]["json"]
        assert cuerpo["text"] == cuerpo["content"]

    async def test_lleva_los_campos_estructurados(self, espia):
        r = resultado(ok=False, rolled_back=True, previous_tag="sha-vieja", error="boom")
        await Notificador(URL).enviar(r, "d-42")
        cuerpo = espia.enviado[0]["json"]

        assert cuerpo["app"] == "mi-api"
        assert cuerpo["tag"] == "sha-1a2b3c4"
        assert cuerpo["previous_tag"] == "sha-vieja"
        assert cuerpo["rolled_back"] is True
        assert cuerpo["delivery_id"] == "d-42"
        assert cuerpo["error"] == "boom"
        assert cuerpo["host"]

    async def test_el_error_se_recorta(self, espia):
        await Notificador(URL).enviar(resultado(ok=False, error="x" * 5000))
        assert len(espia.enviado[0]["json"]["error"]) <= 600


class TestNuncaRompeElDespliegue:
    """Lo mas importante del modulo: el aviso no puede tumbar nada."""

    @pytest.mark.parametrize("fallo", [
        httpx.ConnectError("dns roto"),
        httpx.ReadTimeout("sin respuesta"),
        httpx.HTTPError("generico"),
        RuntimeError("algo inesperado"),
    ])
    async def test_un_canal_caido_no_propaga_la_excepcion(self, monkeypatch, fallo):
        monkeypatch.setattr("app.notify.httpx.AsyncClient", Espia(excepcion=fallo))
        assert await Notificador(URL).enviar(resultado(ok=False)) is False

    async def test_una_respuesta_de_error_no_propaga(self, monkeypatch):
        monkeypatch.setattr("app.notify.httpx.AsyncClient", Espia(status=500))
        assert await Notificador(URL).enviar(resultado(ok=False)) is False


class _Receptor(BaseHTTPRequestHandler):
    """Canal de avisos de mentira, pero con socket de verdad."""

    recibido: list[dict] = []

    def do_POST(self):
        cuerpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Receptor.recibido.append(json.loads(cuerpo))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def canal_real():
    _Receptor.recibido.clear()
    servidor = HTTPServer(("127.0.0.1", 0), _Receptor)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_port}/hook", _Receptor.recibido
    servidor.shutdown()
    servidor.server_close()


async def test_el_aviso_llega_de_verdad_por_http(canal_real):
    """Las demas pruebas usan un doble; esta ejercita httpx contra un socket.

    Sin ella, un error en la construccion de la peticion (cabeceras,
    serializacion, uso de la API de httpx) pasaria inadvertido.
    """
    url, recibido = canal_real
    enviado = await Notificador(url).enviar(
        resultado(ok=False, rolled_back=True, previous_tag="sha-buena"), "d-real"
    )

    assert enviado is True
    assert len(recibido) == 1
    cuerpo = recibido[0]
    assert cuerpo["app"] == "mi-api"
    assert cuerpo["previous_tag"] == "sha-buena"
    assert cuerpo["delivery_id"] == "d-real"
    assert "sha-buena" in cuerpo["text"]


async def test_un_canal_que_no_existe_no_rompe_nada():
    """Puerto cerrado: el caso mas probable en produccion cuando el canal cae."""
    assert await Notificador("http://127.0.0.1:9/hook", timeout=1.0).enviar(
        resultado(ok=False)
    ) is False
