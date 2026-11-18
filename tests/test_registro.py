"""O registro precisa sobreviver ao que o derrubaria justamente na hora errada.

Ele existe porque três incidentes seguidos precisaram de um print de tela do
jogador para serem diagnosticados. Um log que levanta exceção no meio de um
turno, ou que enche o disco, seria pior que a ausência dele.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.registro import Registro  # noqa: E402


def test_grava_e_le_de_volta(tmp_path):
    r = Registro(tmp_path / "log.jsonl")
    r.anotar("turno", "done", idx=433, is_error=False, terminal_reason="completed")
    r.anotar("turno", "erro", idx=434, erro="cota estourada")

    recentes = r.recentes()
    assert [x["tipo"] for x in recentes] == ["erro", "done"]  # mais novo primeiro
    assert recentes[0]["erro"] == "cota estourada"
    assert recentes[1]["terminal_reason"] == "completed"


def test_campos_nulos_nao_poluem_a_linha(tmp_path):
    r = Registro(tmp_path / "log.jsonl")
    r.anotar("turno", "done", idx=1, stop_reason=None, custo_usd=None)
    linha = json.loads((tmp_path / "log.jsonl").read_text(encoding="utf-8").strip())
    assert "stop_reason" not in linha and "custo_usd" not in linha
    assert linha["idx"] == 1


def test_filtra_so_as_falhas(tmp_path):
    r = Registro(tmp_path / "log.jsonl")
    r.anotar("turno", "done", idx=1, is_error=False)
    r.anotar("turno", "done", idx=2, is_error=True, terminal_reason="max_turns")
    r.anotar("turno", "erro", idx=3, erro="rede caiu")

    falhas = r.recentes(apenas_falhas=True)
    assert [x["idx"] for x in falhas] == [3, 2]


def test_nunca_levanta_quando_o_disco_recusa(tmp_path):
    """Um log que derruba o turno é pior que log nenhum."""
    r = Registro(tmp_path / "nao" / "existe" / "log.jsonl")
    r.anotar("turno", "done", idx=1)          # diretório inexistente
    assert r.recentes() == []                  # leitura também não levanta


def test_gira_ao_passar_do_limite(tmp_path):
    caminho = tmp_path / "log.jsonl"
    r = Registro(caminho, limite=200)
    for i in range(20):
        r.anotar("turno", "done", idx=i, erro="x" * 40)
    assert caminho.exists()
    assert caminho.with_suffix(".jsonl.1").exists()
    # o arquivo corrente recomeçou, então é bem menor que o total escrito
    assert caminho.stat().st_size < 20 * 60


def test_linha_corrompida_nao_derruba_a_leitura(tmp_path):
    caminho = tmp_path / "log.jsonl"
    r = Registro(caminho)
    r.anotar("turno", "done", idx=1)
    with caminho.open("a", encoding="utf-8") as f:
        f.write("{isso nao e json\n")
    r.anotar("turno", "done", idx=2)

    assert [x["idx"] for x in r.recentes()] == [2, 1]
