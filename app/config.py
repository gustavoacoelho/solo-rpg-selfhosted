"""Configuração lida do ambiente, validada na importação.

Falha barulhenta é proposital: subir com credencial errada só descobre o
problema no meio de uma sessão de jogo, que é o pior momento possível.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Configuração inválida — o app não deve subir."""


@dataclass(frozen=True)
class Config:
    campaign_dir: Path
    db_path: Path
    canon_path: Path
    mesa_path: Path
    history_dir: Path
    app_password: str
    session_secret: str
    context_window_turns: int
    auto_consolidate_at: int
    max_turns_turno: int
    model: str
    character_name: str
    campaign_title: str
    galleries: tuple[tuple[str, str], ...]

    @property
    def sheet_glob(self) -> str:
        return "ficha-*.md"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} não está definido. Copie .env.example para .env e preencha."
        )
    return value


def load() -> Config:
    # O Agent SDK lê ANTHROPIC_API_KEY sozinho; aqui só conferimos que existe,
    # para falhar agora e não no primeiro turno.
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise ConfigError(
            "ANTHROPIC_API_KEY não está definido.\n"
            "Crie uma chave em https://console.anthropic.com e coloque no .env."
        )

    character = os.environ.get("CHARACTER_NAME", "").strip() or "Aventureiro"

    campaign_dir = Path(os.environ.get("CAMPAIGN_DIR", "./campanha")).expanduser().resolve()
    campaign_dir.mkdir(parents=True, exist_ok=True)
    history_dir = campaign_dir / "historico"
    history_dir.mkdir(exist_ok=True)

    try:
        window = int(os.environ.get("CONTEXT_WINDOW_TURNS", "15"))
    except ValueError as exc:
        raise ConfigError("CONTEXT_WINDOW_TURNS precisa ser um número inteiro.") from exc
    if window < 2:
        raise ConfigError("CONTEXT_WINDOW_TURNS precisa ser pelo menos 2.")

    # Consolidar sozinho quando o acúmulo alcança a janela: a partir daí existe
    # campanha que saiu do contexto sem estar no cânone, e o mestre nem sabe que
    # ela existe para ir buscar. Zero desliga o automático.
    try:
        auto = int(os.environ.get("AUTO_CONSOLIDATE_AT", str(window)))
    except ValueError as exc:
        raise ConfigError("AUTO_CONSOLIDATE_AT precisa ser um número inteiro.") from exc
    if auto < 0:
        raise ConfigError("AUTO_CONSOLIDATE_AT não pode ser negativo.")

    # Teto de idas e voltas de ferramenta num turno. Existe para uma busca
    # malsucedida não virar laço infinito consumindo cota — não para limitar
    # trabalho legítimo. Com 12, um pedido de "acerte as fichas" gastava sete
    # chamadas só localizando os arquivos e morria no meio das edições, com
    # parte delas já gravada em disco.
    try:
        teto = int(os.environ.get("MAX_TURNS_TURNO", "30"))
    except ValueError as exc:
        raise ConfigError("MAX_TURNS_TURNO precisa ser um número inteiro.") from exc
    if teto < 4:
        raise ConfigError("MAX_TURNS_TURNO precisa ser pelo menos 4.")

    return Config(
        campaign_dir=campaign_dir,
        db_path=campaign_dir / "campanha.db",
        canon_path=campaign_dir / "CANON.md",
        mesa_path=campaign_dir / "MESA.md",
        history_dir=history_dir,
        app_password=_require("APP_PASSWORD"),
        session_secret=os.environ.get("SESSION_SECRET", "").strip() or secrets.token_hex(32),
        context_window_turns=window,
        auto_consolidate_at=auto,
        max_turns_turno=teto,
        model=os.environ.get("MODEL", "opus").strip() or "opus",
        character_name=character,
        campaign_title=os.environ.get("CAMPAIGN_TITLE", "").strip() or f"Campanha de {character}",
        galleries=_galleries(os.environ.get("PORTRAIT_GALLERIES", ""), character),
    )


def _slug(texto: str) -> str:
    import unicodedata

    base = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return "".join(c if c.isalnum() else "-" for c in base.lower()).strip("-") or "galeria"


def _galleries(raw: str, character: str) -> tuple[tuple[str, str], ...]:
    """Galerias de retratos: "Nome, Outro Nome" vira (slug, rótulo).

    Sem configuração, uma galeria para o personagem e outra para cenas. A
    lista é fixa de propósito: o slug vira nome de pasta, então só entra o que
    foi declarado aqui.
    """
    nomes = [n.strip() for n in raw.split(",") if n.strip()] or [character]
    itens = [(_slug(n), n) for n in nomes]
    if "cenas" not in {s for s, _ in itens}:
        itens.append(("cenas", "Cenas e locais"))
    return tuple(itens)
