"""Espelha o banco em Markdown legível — a camada que o mestre pesquisa.

O banco é a fonte de verdade; estes arquivos existem para duas coisas:
o mestre fazer grep neles quando precisa de um detalhe antigo, e a campanha
continuar legível caso este app deixe de existir.
"""

from __future__ import annotations

from pathlib import Path

from .db import Database, Turn

TURNS_PER_FILE = 40

MESA_DEFAULT = """# Regras desta mesa

Convenções acordadas com o jogador. Valem sobre qualquer instinto contrário.

- **O jogador controla exclusivamente {personagem}.** Nunca decida ações, falas ou
  pensamentos dele. Descreva o mundo, os NPCs e as consequências; o resto é dele.
- **Só o que está entre aspas duplas foi falado em voz alta.** O restante é ação
  e pensamento, inacessível aos outros personagens. Nenhum NPC reage a algo que
  o jogador apenas pensou.
- **Role os dados você mesmo e exponha o resultado** no texto: perícia, CD,
  modificador, total. O jogador não rola.
- **Não termine as respostas oferecendo opções.** Nada de "o que você faz: A, B
  ou C", e nada de disfarçar isso na boca de um NPC. Deixe a narrativa correr e
  pare onde a cena naturalmente pede a ação do jogador.
- **Sistema:** D&D 5.5e (atualização de 2024).
- **Idioma:** português do Brasil. Ao citar um local, item ou criatura cujo nome
  canônico é em inglês, ponha o original entre colchetes na primeira menção —
  Forte da Vela [Candlekeep].

## Consultando o passado

O cânone atual chega a você a cada turno. A campanha **completa** está em
`historico/*.md`. Quando precisar de um detalhe antigo — uma fala exata, um
valor, o nome de um NPC secundário — use Grep para achar e Read para ler só o
trecho. Não invente quando puder verificar.

## Mantendo o cânone

`CANON.md` é o estado vivo da campanha. Você só o edita quando o jogador pedir
consolidação, nunca durante a narração.
"""

CANON_DEFAULT = """# Cânone da campanha

*(Ainda vazio. Rode a consolidação depois da primeira sessão, ou importe uma
campanha existente com `scripts/import_campanha.py`.)*

## Personagens

## Locais

## Facções

## Itens

## Situação atual
"""


def ensure_scaffold(campaign_dir: Path, mesa_path: Path, canon_path: Path,
                    personagem: str = "o personagem") -> None:
    """Cria MESA.md e CANON.md se ainda não existirem. Nunca sobrescreve."""
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "historico").mkdir(exist_ok=True)
    if not mesa_path.exists():
        mesa_path.write_text(MESA_DEFAULT.replace("{personagem}", personagem), encoding="utf-8")
    if not canon_path.exists():
        canon_path.write_text(CANON_DEFAULT, encoding="utf-8")


def _file_for(history_dir: Path, idx: int) -> Path:
    bloco = idx // TURNS_PER_FILE
    inicio = bloco * TURNS_PER_FILE
    fim = inicio + TURNS_PER_FILE - 1
    return history_dir / f"{bloco:03d}-turnos-{inicio:04d}-{fim:04d}.md"


def append_turn(history_dir: Path, turn: Turn) -> None:
    """Acrescenta um turno ao arquivo do bloco correspondente."""
    path = _file_for(history_dir, turn.idx)
    header = "JOGADOR" if turn.role == "user" else "MESTRE"
    bloco = f"\n\n## [{turn.idx}] {header}\n\n{turn.content.strip()}\n"
    if not path.exists():
        path.write_text(f"# Turnos {turn.idx}–{turn.idx + TURNS_PER_FILE - 1}\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(bloco)
        fh.flush()


def rebuild(db: Database, campaign_id: int, history_dir: Path) -> int:
    """Reescreve todo o espelho a partir do banco. Usado na importação."""
    for old in history_dir.glob("*.md"):
        old.unlink()
    n = 0
    for turn in db.all_turns(campaign_id):
        append_turn(history_dir, turn)
        n += 1
    return n
