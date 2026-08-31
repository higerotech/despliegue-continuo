"""Validacion de la autenticidad de los webhooks de GitHub.

El unico control que realmente prueba el origen es la firma HMAC-SHA256 que
GitHub calcula sobre el cuerpo crudo con el secreto compartido. Detras de un
tunel (Cloudflare / Tailscale) la IP de origen es la del tunel, no la de
GitHub, asi que una allowlist de IPs aqui no aportaria nada: ese filtro, si se
quiere, va en el WAF de Cloudflare.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import OrderedDict

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"


class SignatureError(Exception):
    """La peticion no viene firmada por GitHub con nuestro secreto."""


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, header: str | None) -> None:
    """Lanza SignatureError si la firma no coincide. No devuelve nada."""
    if not header:
        raise SignatureError(f"falta la cabecera {SIGNATURE_HEADER}")

    scheme, separator, provided = header.partition("=")
    if scheme != "sha256" or not separator or not provided:
        raise SignatureError("formato de firma no reconocido")

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided.strip()):
        raise SignatureError("firma invalida")


class DeliveryCache:
    """Recuerda los ultimos delivery IDs para ignorar reentregas de GitHub.

    GitHub reintenta las entregas fallidas y el operador puede reenviarlas a
    mano desde la UI. Sin esto, un reintento vuelve a desplegar.
    """

    def __init__(self, capacity: int = 1024) -> None:
        if capacity < 1:
            raise ValueError("capacity debe ser >= 1")
        self._capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()

    def seen_before(self, delivery_id: str | None) -> bool:
        """Registra el id y devuelve True si ya se habia procesado."""
        if not delivery_id:
            return False
        if delivery_id in self._seen:
            self._seen.move_to_end(delivery_id)
            return True
        self._seen[delivery_id] = None
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return False

    def __len__(self) -> int:
        return len(self._seen)
