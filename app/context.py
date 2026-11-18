"""Montagem do contexto de cada turno.

Estratégia: em vez de reenviar a campanha inteira (~200k tokens) toda jogada,
o mestre recebe o cânone destilado mais uma janela de turnos recentes, e vai
buscar no histórico em disco quando precisar de um detalhe antigo.

Trocamos "carregar 200 mil por precaução" por "buscar 2 mil quando precisa".
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .db import Database, Turn

# Estimativa grosseira só para o medidor da interface. Português costuma dar
# ~3,5 caracteres por token; usamos 4 para não subestimar o consumo.
CHARS_POR_TOKEN = 4


def build_system_prompt(cfg: Config) -> str:
    """As regras da mesa. Estático entre turnos, então o cache atua sobre ele."""
    mesa = cfg.mesa_path.read_text(encoding="utf-8") if cfg.mesa_path.exists() else ""
    fichas = sorted(cfg.campaign_dir.glob(cfg.sheet_glob))
    partes = [
        "Você é o mestre de uma campanha solo de D&D 5.5e, conduzida em português.",
        "",
        mesa,
    ]
    for ficha in fichas:
        partes.append(f"\n---\n\n# {ficha.stem}\n\n{ficha.read_text(encoding='utf-8')}")
    return "\n".join(partes)


def build_turn_prompt(cfg: Config, db: Database, campaign_id: int, acao: str) -> str:
    """Cânone + janela recente + a ação do jogador."""
    canon = cfg.canon_path.read_text(encoding="utf-8") if cfg.canon_path.exists() else ""

    # A ação atual já foi gravada (é o que garante a durabilidade), então ela
    # é o último turno da janela. Removemos daqui para não mandá-la duas vezes:
    # uma no histórico recente e outra no bloco <acao_do_jogador>.
    recentes = db.recent_turns(campaign_id, cfg.context_window_turns + 1)
    if recentes and recentes[-1].role == "user":
        recentes = recentes[:-1]
    recentes = recentes[-cfg.context_window_turns :]

    partes = [
        "<canone>",
        canon.strip(),
        "</canone>",
        "",
    ]

    if recentes:
        partes += ["<turnos_recentes>", _render(recentes), "</turnos_recentes>", ""]

    total = db.count_turns(campaign_id)
    if total > len(recentes):
        antigos = total - len(recentes)
        partes += [
            f"<nota>Os {antigos} turnos anteriores a estes não estão neste contexto, "
            f"mas estão em disco em historico/*.md. Se precisar de um detalhe antigo "
            f"(uma fala exata, um valor, um nome), use Grep para localizar e Read para "
            f"ler o trecho — não invente e não diga que não tem acesso.</nota>",
            "",
        ]

    partes += ["<acao_do_jogador>", acao.strip(), "</acao_do_jogador>"]
    return "\n".join(partes)


def _render(turns: list[Turn]) -> str:
    linhas = []
    for t in turns:
        quem = "JOGADOR" if t.role == "user" else "MESTRE"
        linhas.append(f"[{t.idx}] {quem}:\n{t.content.strip()}")
    return "\n\n".join(linhas)


def estimate_tokens(*textos: str) -> int:
    return sum(len(t) for t in textos) // CHARS_POR_TOKEN
