"""Aviso del resultado de un despliegue a un canal externo — deuda DS-03.

El receptor responde a GitHub *antes* de desplegar (ADR-0008), asi que el
resultado no viaja de vuelta: en la UI de GitHub la entrega figura correcta
aunque el despliegue haya fallado y revertido. Hasta ahora la unica forma de
enterarse era consultar /status o el .jsonl.

Tres decisiones que dan forma a este modulo:

1. **Por defecto solo avisa de lo que sale mal.** Un canal que suena en cada
   despliegue correcto se acaba silenciando, y entonces tampoco suena cuando
   importa. `NOTIFY_ON=always` existe para quien prefiera lo contrario.
2. **Un fallo al notificar nunca afecta al despliegue.** El aviso es
   observabilidad, no parte de la operacion: si el canal esta caido se registra
   un warning y se sigue.
3. **El destino es un webhook generico.** El cuerpo lleva `text` y `content`
   ademas de los campos estructurados, que es lo que esperan Slack, Discord,
   ntfy y la mayoria de relays, de modo que funciona sin adaptadores.
"""

from __future__ import annotations

import logging
import socket

import httpx

from .deployer import DeployResult

logger = logging.getLogger("cd.notify")

SIEMPRE = "always"
SOLO_FALLOS = "failure"
_MODOS = (SOLO_FALLOS, SIEMPRE)

_MAX_ERROR = 600


class Notificador:
    """Envia el resultado de un despliegue a un webhook. Nunca lanza."""

    def __init__(self, url: str, cuando: str = SOLO_FALLOS, timeout: float = 5.0) -> None:
        self._url = (url or "").strip()
        self._cuando = cuando if cuando in _MODOS else SOLO_FALLOS
        self._timeout = timeout
        self._host = socket.gethostname()

    @property
    def habilitado(self) -> bool:
        return bool(self._url)

    def interesa(self, resultado: DeployResult) -> bool:
        """Un despliegue correcto solo se anuncia si se pidio expresamente."""
        if not self.habilitado:
            return False
        if self._cuando == SIEMPRE:
            return True
        return not resultado.ok or resultado.rolled_back

    def resumen(self, resultado: DeployResult) -> str:
        """Una linea que un humano entienda sin abrir nada mas."""
        if resultado.ok:
            return (
                f"✅ {resultado.app}: desplegado {resultado.tag} "
                f"en {resultado.seconds:.1f}s"
            )
        if resultado.rolled_back:
            anterior = resultado.previous_tag or "la version anterior"
            return (
                f"⚠️ {resultado.app}: {resultado.tag} fallo y se revirtio a "
                f"{anterior}. El servicio esta en pie, pero el despliegue no entro."
            )
        return (
            f"🚨 {resultado.app}: {resultado.tag} fallo y NO se pudo revertir. "
            f"El servicio puede estar caido."
        )

    def _cuerpo(self, resultado: DeployResult, delivery_id: str) -> dict:
        texto = self.resumen(resultado)
        return {
            # `text` lo entienden Slack y ntfy; `content`, Discord. Llevar ambos
            # evita necesitar un adaptador por destino.
            "text": texto,
            "content": texto,
            "host": self._host,
            "app": resultado.app,
            "sha": resultado.sha,
            "tag": resultado.tag,
            "previous_tag": resultado.previous_tag,
            "ok": resultado.ok,
            "rolled_back": resultado.rolled_back,
            "seconds": round(resultado.seconds, 2),
            "error": resultado.error[:_MAX_ERROR],
            "delivery_id": delivery_id,
        }

    async def enviar(self, resultado: DeployResult, delivery_id: str = "") -> bool:
        """Devuelve True si se envio. No propaga errores nunca."""
        if not self.interesa(resultado):
            return False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                respuesta = await client.post(self._url, json=self._cuerpo(resultado, delivery_id))
            if respuesta.status_code >= 400:
                logger.warning(
                    "el canal de avisos respondio %s al notificar %s",
                    respuesta.status_code, resultado.app,
                )
                return False
            return True
        except Exception as exc:
            # Deliberadamente amplio: ni un DNS roto ni un TLS invalido pueden
            # convertirse en un fallo de despliegue.
            logger.warning("no se pudo notificar el despliegue de %s: %s",
                           resultado.app, type(exc).__name__)
            return False
