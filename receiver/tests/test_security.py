"""La firma HMAC es la unica prueba de origen que tenemos: se prueba a fondo."""

from __future__ import annotations

import pytest

from app.security import DeliveryCache, SignatureError, compute_signature, verify_signature

SECRET = "a" * 40
BODY = b'{"ref":"refs/heads/main"}'


def test_acepta_una_firma_valida():
    verify_signature(SECRET, BODY, compute_signature(SECRET, BODY))


def test_rechaza_cuerpo_manipulado():
    signature = compute_signature(SECRET, BODY)
    with pytest.raises(SignatureError):
        verify_signature(SECRET, BODY + b" ", signature)


def test_rechaza_secreto_distinto():
    with pytest.raises(SignatureError):
        verify_signature(SECRET, BODY, compute_signature("b" * 40, BODY))


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha256=",
        "deadbeef",
        compute_signature(SECRET, BODY).removeprefix("sha256="),
        compute_signature(SECRET, BODY).replace("sha256", "sha1"),
    ],
)
def test_rechaza_cabeceras_malformadas(header):
    with pytest.raises(SignatureError):
        verify_signature(SECRET, BODY, header)


def test_tolera_espacios_alrededor_de_la_firma():
    verify_signature(SECRET, BODY, compute_signature(SECRET, BODY) + "\n")


class TestDeliveryCache:
    def test_detecta_la_reentrega(self):
        cache = DeliveryCache(capacity=4)
        assert cache.seen_before("d1") is False
        assert cache.seen_before("d1") is True

    def test_ids_distintos_no_colisionan(self):
        cache = DeliveryCache(capacity=4)
        assert cache.seen_before("d1") is False
        assert cache.seen_before("d2") is False

    def test_expulsa_los_mas_antiguos_al_llenarse(self):
        cache = DeliveryCache(capacity=2)
        cache.seen_before("d1")
        cache.seen_before("d2")
        cache.seen_before("d3")
        assert len(cache) == 2
        assert cache.seen_before("d1") is False

    def test_un_id_vacio_nunca_se_considera_repetido(self):
        cache = DeliveryCache()
        assert cache.seen_before(None) is False
        assert cache.seen_before(None) is False
        assert len(cache) == 0
