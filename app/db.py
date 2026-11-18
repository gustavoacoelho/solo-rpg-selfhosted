"""Persistência dos turnos. Esta é a fonte de verdade da campanha.

A regra que sustenta o projeto: o turno do jogador é gravado e commitado
ANTES de qualquer chamada ao modelo, e a resposta é gravada incrementalmente
enquanto chega. Nada é apagado, só acrescentado — o que torna impossível o
rollback silencioso que motivou este app.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

Role = Literal["user", "assistant"]
Status = Literal["partial", "complete", "discarded"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turn (
    id              INTEGER PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaign(id),
    idx             INTEGER NOT NULL,
    role            TEXT    NOT NULL CHECK (role IN ('user','assistant')),
    content         TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'complete'
                            CHECK (status IN ('partial','complete','discarded')),
    terminal_reason TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    UNIQUE (campaign_id, idx)
);

CREATE INDEX IF NOT EXISTS turn_by_campaign ON turn (campaign_id, idx);

CREATE TABLE IF NOT EXISTS session (
    id              INTEGER PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaign(id),
    started_at      REAL NOT NULL,
    ended_at        REAL,
    consolidated_at REAL,
    last_turn_idx   INTEGER
);

-- Criada no v1, usada quando a geração de imagem entrar.
CREATE TABLE IF NOT EXISTS asset (
    id         INTEGER PRIMARY KEY,
    turn_id    INTEGER NOT NULL REFERENCES turn(id),
    path       TEXT NOT NULL,
    prompt     TEXT,
    created_at REAL NOT NULL
);
"""


@dataclass
class Turn:
    id: int
    idx: int
    role: Role
    content: str
    status: Status
    tokens_in: int | None = None
    tokens_out: int | None = None


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL para leitura concorrente; FULL porque perder um turno por causa
        # de um cache de disco seria exatamente o bug que viemos consertar.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---------------- campanha ----------------

    def ensure_campaign(self, name: str = "Campanha solo") -> int:
        row = self._conn.execute("SELECT id FROM campaign ORDER BY id LIMIT 1").fetchone()
        if row:
            return row["id"]
        cur = self._conn.execute(
            "INSERT INTO campaign (name, created_at) VALUES (?, ?)", (name, time.time())
        )
        self._conn.commit()
        return cur.lastrowid

    def next_idx(self, campaign_id: int) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(idx), -1) AS m FROM turn WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return row["m"] + 1

    # ---------------- escrita ----------------

    def insert_turn(
        self,
        campaign_id: int,
        role: Role,
        content: str,
        status: Status = "complete",
    ) -> int:
        """Grava um turno e commita imediatamente."""
        now = time.time()
        idx = self.next_idx(campaign_id)
        cur = self._conn.execute(
            "INSERT INTO turn (campaign_id, idx, role, content, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (campaign_id, idx, role, content, status, now, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_partial(self, turn_id: int, content: str) -> None:
        """Atualiza o texto de um turno em andamento. Chamado durante o stream."""
        self._conn.execute(
            "UPDATE turn SET content = ?, updated_at = ? WHERE id = ?",
            (content, time.time(), turn_id),
        )
        self._conn.commit()

    def finalize_turn(
        self,
        turn_id: int,
        content: str,
        terminal_reason: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE turn SET content = ?, status = 'complete', terminal_reason = ?,"
            " tokens_in = ?, tokens_out = ?, updated_at = ? WHERE id = ?",
            (content, terminal_reason, tokens_in, tokens_out, time.time(), turn_id),
        )
        self._conn.commit()

    def last_exchange(self, campaign_id: int) -> list[Turn]:
        """A última rodada completa: o turno do jogador e a resposta do mestre.

        Devolve os dois em ordem cronológica, ou lista vazia se a ponta do
        histórico não for esse par — o que acontece se houver um turno parcial
        pendurado, e nesse caso quem resolve é o descarte de órfão.
        """
        rows = self._conn.execute(
            "SELECT * FROM turn WHERE campaign_id = ? AND status = 'complete'"
            " ORDER BY idx DESC LIMIT 2",
            (campaign_id,),
        ).fetchall()
        if len(rows) < 2:
            return []
        ultimo, penultimo = _row_to_turn(rows[0]), _row_to_turn(rows[1])
        if ultimo.role != "assistant" or penultimo.role != "user":
            return []
        # Só a ponta pode ser apagada: assim o idx continua contíguo sem
        # reindexar, e a montagem do contexto e o espelho não percebem.
        if ultimo.idx != self.next_idx(campaign_id) - 1:
            return []
        return [penultimo, ultimo]

    def delete_turns(self, turn_ids: list[int]) -> None:
        """Remoção definitiva. Só usada na ponta do histórico."""
        marcas = ",".join("?" * len(turn_ids))
        self._conn.execute(f"DELETE FROM turn WHERE id IN ({marcas})", turn_ids)
        self._conn.commit()

    def discard_turn(self, turn_id: int) -> None:
        self._conn.execute(
            "UPDATE turn SET status = 'discarded', updated_at = ? WHERE id = ?",
            (time.time(), turn_id),
        )
        self._conn.commit()

    # ---------------- leitura ----------------

    def recent_turns(self, campaign_id: int, limit: int) -> list[Turn]:
        """Últimos N turnos completos, em ordem cronológica."""
        rows = self._conn.execute(
            "SELECT * FROM turn WHERE campaign_id = ? AND status = 'complete'"
            " ORDER BY idx DESC LIMIT ?",
            (campaign_id, limit),
        ).fetchall()
        return [_row_to_turn(r) for r in reversed(rows)]

    def turns_since(self, campaign_id: int, idx: int) -> list[Turn]:
        rows = self._conn.execute(
            "SELECT * FROM turn WHERE campaign_id = ? AND idx > ? AND status = 'complete'"
            " ORDER BY idx",
            (campaign_id, idx),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    def all_turns(self, campaign_id: int) -> Iterator[Turn]:
        for row in self._conn.execute(
            "SELECT * FROM turn WHERE campaign_id = ? AND status = 'complete' ORDER BY idx",
            (campaign_id,),
        ):
            yield _row_to_turn(row)

    def find_orphan_partial(self, campaign_id: int) -> Turn | None:
        """Turno que ficou pela metade — queda no meio do stream."""
        row = self._conn.execute(
            "SELECT * FROM turn WHERE campaign_id = ? AND status = 'partial'"
            " ORDER BY idx DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return _row_to_turn(row) if row else None

    def usage_totals(self, campaign_id: int) -> tuple[int, int]:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(tokens_in),0) AS i, COALESCE(SUM(tokens_out),0) AS o"
            " FROM turn WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return row["i"], row["o"]

    def count_turns(self, campaign_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM turn WHERE campaign_id = ? AND status = 'complete'",
            (campaign_id,),
        ).fetchone()
        return row["c"]

    # ---------------- sessões ----------------

    def last_consolidated_idx(self, campaign_id: int) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(last_turn_idx), -1) AS m FROM session"
            " WHERE campaign_id = ? AND consolidated_at IS NOT NULL",
            (campaign_id,),
        ).fetchone()
        return row["m"]

    def record_consolidation(self, campaign_id: int, last_turn_idx: int) -> None:
        now = time.time()
        self._conn.execute(
            "INSERT INTO session (campaign_id, started_at, ended_at, consolidated_at, last_turn_idx)"
            " VALUES (?, ?, ?, ?, ?)",
            (campaign_id, now, now, now, last_turn_idx),
        )
        self._conn.commit()


def _row_to_turn(row: sqlite3.Row) -> Turn:
    return Turn(
        id=row["id"],
        idx=row["idx"],
        role=row["role"],
        content=row["content"],
        status=row["status"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
    )
