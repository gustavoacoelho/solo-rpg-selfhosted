"""O botão de desfazer só pode agir na ponta, e não pode perder texto.

Estes testes existem porque a operação é destrutiva: um engano aqui apaga
narração de verdade. Cobrem as três coisas que importam — que o par devolvido
é mesmo o último, que a recusa acontece quando a ponta não é uma rodada
completa, e que o idx continua contíguo depois da remoção.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402


def montar(tmp_path: Path, papeis: list[str]) -> tuple[Database, int]:
    db = Database(tmp_path / "t.db")
    cid = db.ensure_campaign()
    for i, papel in enumerate(papeis):
        db.insert_turn(cid, papel, f"texto {i}")
    return db, cid


def test_devolve_o_ultimo_par(tmp_path):
    db, cid = montar(tmp_path, ["user", "assistant", "user", "assistant"])
    par = db.last_exchange(cid)
    assert [t.idx for t in par] == [2, 3]
    assert [t.role for t in par] == ["user", "assistant"]
    assert par[0].content == "texto 2"


def test_recusa_quando_a_ponta_e_do_jogador(tmp_path):
    db, cid = montar(tmp_path, ["user", "assistant", "user"])
    assert db.last_exchange(cid) == []


def test_recusa_com_historico_curto(tmp_path):
    db, cid = montar(tmp_path, ["user"])
    assert db.last_exchange(cid) == []


def test_recusa_quando_ha_parcial_pendurado(tmp_path):
    """Um turno parcial ocupa o idx seguinte; a rodada anterior deixa de ser a
    ponta e não pode mais ser apagada por baixo dele."""
    db, cid = montar(tmp_path, ["user", "assistant"])
    db.insert_turn(cid, "user", "em andamento", status="partial")
    assert db.last_exchange(cid) == []


def test_remocao_mantem_idx_contiguo(tmp_path):
    db, cid = montar(tmp_path, ["user", "assistant", "user", "assistant"])
    par = db.last_exchange(cid)
    db.delete_turns([t.id for t in par])

    restantes = list(db.all_turns(cid))
    assert [t.idx for t in restantes] == [0, 1]
    # O próximo turno tem que reaproveitar o idx liberado, senão abre buraco.
    assert db.next_idx(cid) == 2


def test_nao_apaga_alem_da_ponta(tmp_path):
    """Duas remoções seguidas descem uma rodada de cada vez, nunca mais."""
    db, cid = montar(tmp_path, ["user", "assistant", "user", "assistant"])
    for esperado in ([2, 3], [0, 1]):
        par = db.last_exchange(cid)
        assert [t.idx for t in par] == esperado
        db.delete_turns([t.id for t in par])
    assert list(db.all_turns(cid)) == []
    assert db.last_exchange(cid) == []
