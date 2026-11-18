"""A trava serializa turno e consolidação — e não pode prender a campanha.

Estes testes existem por causa de um incidente real: a trava era liberada no
`finally` de um gerador SSE, o jogador recarregou a página durante uma
consolidação, e ela ficou presa. A partir dali todo turno levou 409 — e, como a
checagem vinha DEPOIS dos INSERTs, cada 409 ainda gravava a ação do jogador e
deixava um turno parcial órfão.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.trava import Ocupado, Trava  # noqa: E402


def test_recusa_enquanto_ocupada():
    t = Trava()
    t.adquirir("um turno", agora=0)
    with pytest.raises(Ocupado) as exc:
        t.adquirir("uma consolidação", agora=1)
    assert exc.value.quem == "um turno"


def test_libera_e_permite_o_proximo():
    t = Trava()
    token = t.adquirir("um turno", agora=0)
    assert t.soltar(token) is True
    t.adquirir("uma consolidação", agora=1)  # não levanta


def test_expira_quando_o_dono_some():
    """O caso do incidente: o cliente SSE caiu e ninguém liberou."""
    t = Trava(limite=900)
    t.adquirir("uma consolidação", agora=0)
    with pytest.raises(Ocupado):
        t.adquirir("um turno", agora=899)
    t.adquirir("um turno", agora=901)  # passou do limite: assume


def test_liberacao_atrasada_nao_derruba_o_dono_novo():
    """Sem o token, a consolidação fantasma soltaria a trava do turno novo."""
    t = Trava(limite=900)
    fantasma = t.adquirir("uma consolidação", agora=0)
    novo = t.adquirir("um turno", agora=901)
    assert t.soltar(fantasma) is False
    assert t.estado(agora=902)["ocupada"] is True
    assert t.soltar(novo) is True
    assert t.estado(agora=903)["ocupada"] is False


def test_soltar_duas_vezes_nao_libera_o_seguinte():
    t = Trava()
    token = t.adquirir("um turno", agora=0)
    assert t.soltar(token) is True
    outro = t.adquirir("uma consolidação", agora=1)
    assert t.soltar(token) is False
    assert t.estado(agora=2)["quem"] == "uma consolidação"
    assert t.soltar(outro) is True


def test_estado_descreve_quem_segura():
    t = Trava(limite=900)
    t.adquirir("uma consolidação", agora=100)
    e = t.estado(agora=130)
    assert e == {"ocupada": True, "quem": "uma consolidação", "ha_segundos": 30.0, "expira_em": 870.0}
