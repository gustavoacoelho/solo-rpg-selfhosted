"""Wrapper do Claude Agent SDK.

Duas responsabilidades: traduzir o stream do SDK em eventos simples que a
camada HTTP sabe transmitir, e conter o mestre dentro do diretório da campanha.

Contenção em três camadas, porque um NPC não precisa de shell:
  1. cwd fixo no diretório da campanha
  2. allowlist de ferramentas (sem Bash, sem Write, sem rede)
  3. can_use_tool, que rejeita escrita fora de CANON.md e das fichas
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    query,
)

# Leitura e busca são auto-aprovadas: são inofensivas dentro do cwd.
#
# Edit fica DE FORA desta lista de propósito. Uma entrada em allowed_tools
# auto-aprova a ferramenta antes de o can_use_tool ser consultado — colocar
# "Edit" aqui anularia silenciosamente toda a trava de escrita. Fora da lista,
# a chamada cai no callback, que decide arquivo por arquivo.
ALLOWED_TOOLS = ["Read", "Grep", "Glob"]
DISALLOWED_TOOLS = ["Bash", "Write", "WebFetch", "WebSearch", "NotebookEdit", "Task"]

# Na consolidação o mestre reescreve o CANON.md inteiro. Fazendo isso só com
# Edit, cada seção vira uma ida e volta e ele estoura o limite de turnos no meio
# da reescrita, deixando o arquivo pela metade. Com Write ele grava o arquivo
# todo numa chamada — que é o que a instrução sempre pediu.
#
# Write sai daqui, mas NÃO entra em ALLOWED_TOOLS: uma entrada lá auto-aprova a
# ferramenta antes de o can_use_tool ser consultado, e a trava de caminho seria
# anulada em silêncio. Fora das duas listas, a chamada cai no callback.
DISALLOWED_COM_ESCRITA = [f for f in DISALLOWED_TOOLS if f != "Write"]

# Ferramentas que gravam em disco e passam pela checagem de caminho.
FERRAMENTAS_DE_ESCRITA = {"Edit", "Write"}

_TOOL_LABEL = {
    "Grep": "procurando no histórico",
    "Read": "lendo o histórico",
    "Glob": "listando arquivos",
    "Edit": "editando",
    "Write": "reescrevendo",
}


def _writable_targets(campaign_dir: Path) -> set[Path]:
    alvos = {(campaign_dir / "CANON.md").resolve()}
    for ficha in campaign_dir.glob("ficha-*.md"):
        alvos.add(ficha.resolve())
    return alvos


def _rotular(nome: str, entrada: Any) -> str:
    """Rótulo para a interface, com o arquivo quando houver um.

    Sem o nome do arquivo, cinco edições em fichas diferentes apareciam todas
    como "atualizando o cânone" — o jogador não tinha como saber o que o mestre
    estava mexendo, e nem eu, ao investigar depois.
    """
    base = _TOOL_LABEL.get(nome, nome)
    if nome in FERRAMENTAS_DE_ESCRITA and isinstance(entrada, dict):
        alvo = entrada.get("file_path") or entrada.get("path") or ""
        if alvo:
            return f"{base}: {Path(alvo).name}"
    return base


def make_permission_callback(campaign_dir: Path, on_denied=None):
    """Nega escrita fora do cânone e das fichas, mesmo com Edit/Write liberados."""
    campaign_dir = campaign_dir.resolve()

    async def can_use_tool(tool_name: str, input_data: dict[str, Any], context: Any = None):
        if tool_name not in FERRAMENTAS_DE_ESCRITA:
            return PermissionResultAllow(updated_input=input_data)

        raw = input_data.get("file_path") or input_data.get("path") or ""
        try:
            alvo = Path(raw).resolve() if os.path.isabs(raw) else (campaign_dir / raw).resolve()
        except (OSError, ValueError):
            alvo = None

        if alvo is None:
            motivo = "caminho inválido"
        elif campaign_dir not in alvo.parents:
            motivo = "fora do diretório da campanha"
        elif alvo not in _writable_targets(campaign_dir):
            motivo = "só CANON.md e as fichas podem ser editados"
        else:
            return PermissionResultAllow(updated_input=input_data)

        if on_denied:
            on_denied(tool_name, str(raw), motivo)
        return PermissionResultDeny(
            message=(
                f"Escrita negada em '{raw}': {motivo}. Você pode ler e pesquisar "
                "qualquer arquivo da campanha, mas só escrever em CANON.md e nas fichas."
            )
        )

    return can_use_tool


def build_options(
    campaign_dir: Path,
    system_prompt: str,
    model: str,
    on_denied=None,
    max_turns: int = 12,
    permitir_escrita: bool = False,
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=str(campaign_dir),
        model=model,
        allowed_tools=ALLOWED_TOOLS,
        # Durante a narração o mestre não escreve nada. Só a consolidação pede
        # Write, e mesmo lá o callback continua decidindo arquivo por arquivo.
        disallowed_tools=DISALLOWED_COM_ESCRITA if permitir_escrita else DISALLOWED_TOOLS,
        permission_mode="default",
        can_use_tool=make_permission_callback(campaign_dir, on_denied),
        # Não herda skills, comandos nem configurações do Claude Code da máquina;
        # este agente é só o mestre, com o que está definido aqui.
        setting_sources=[],
        # Teto de idas e voltas de ferramenta num único turno, para uma busca
        # malsucedida não virar um loop que consome a cota.
        max_turns=max_turns,
    )


async def stream_turn(
    prompt: str, options: ClaudeAgentOptions
) -> AsyncIterator[dict[str, Any]]:
    """Roda um turno e emite eventos normalizados.

    Eventos: {"type": "text"|"tool"|"thinking"|"ratelimit"|"done"|"error", ...}
    """
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield {"type": "text", "text": block.text}
                    elif isinstance(block, ToolUseBlock):
                        yield {
                            "type": "tool",
                            "name": block.name,
                            "label": _rotular(block.name, block.input),
                        }
                    elif isinstance(block, ThinkingBlock):
                        yield {"type": "thinking"}

            elif isinstance(message, RateLimitEvent):
                info = message.rate_limit_info
                # O limite de uso pode ter várias janelas simultâneas — five_hour,
                # seven_day, seven_day_opus/sonnet — e o SDK emite um evento
                # para cada. Sem carregar o rate_limit_type, todas se
                # sobrescrevem e sobra um número só, sem dizer de quê.
                yield {
                    "type": "ratelimit",
                    "limit_type": getattr(info, "rate_limit_type", None),
                    "status": getattr(info, "status", None),
                    "utilization": getattr(info, "utilization", None),
                    "resets_at": _iso(getattr(info, "resets_at", None)),
                    "overage_status": getattr(info, "overage_status", None),
                }

            elif isinstance(message, ResultMessage):
                tin, tout = _extract_usage(message.usage)
                yield {
                    "type": "done",
                    "terminal_reason": message.terminal_reason,
                    "stop_reason": message.stop_reason,
                    "is_error": message.is_error,
                    "cost_usd": message.total_cost_usd,
                    "tokens_in": tin,
                    "tokens_out": tout,
                    "contexto": _extract_context(message),
                }

    except Exception as exc:  # noqa: BLE001 - vira evento para o cliente
        yield {"type": "error", "message": _humanize(exc)}


def _extract_context(message: ResultMessage) -> dict[str, Any] | None:
    """Quanto da janela de contexto o turno ocupou.

    `model_usage` traz um registro por modelo com o tamanho da janela e o
    detalhe do cache. É a única fonte do "X de Y tokens" — a contagem que
    guardamos por turno no banco não sabe qual é o teto.
    """
    uso = getattr(message, "model_usage", None) or {}
    if not uso:
        return None
    modelo, dados = max(
        uso.items(),
        key=lambda kv: _num(kv[1], "inputTokens") + _num(kv[1], "cacheReadInputTokens"),
    )
    entrada = _num(dados, "inputTokens")
    cache_lido = _num(dados, "cacheReadInputTokens")
    cache_criado = _num(dados, "cacheCreationInputTokens")
    janela = _num(dados, "contextWindow")
    usado = entrada + cache_lido + cache_criado
    return {
        "modelo": modelo,
        "usado": usado,
        "janela": janela or None,
        "pct": round(usado / janela, 4) if janela else None,
        "cache_lido": cache_lido,
        "cache_criado": cache_criado,
        "entrada": entrada,
        "saida": _num(dados, "outputTokens"),
        "custo_usd": dados.get("costUSD") if isinstance(dados, dict) else getattr(dados, "costUSD", None),
    }


def _num(dados: Any, campo: str) -> int:
    valor = dados.get(campo) if isinstance(dados, dict) else getattr(dados, campo, 0)
    return int(valor or 0)


def _iso(valor: Any) -> str | None:
    """Normaliza o reset da cota para ISO 8601.

    O SDK entrega epoch em segundos; o front precisa de algo que o Date()
    do JavaScript aceite.
    """
    if valor is None:
        return None
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    if isinstance(valor, (int, float)):
        from datetime import datetime, timezone

        return datetime.fromtimestamp(valor, tz=timezone.utc).isoformat()
    return str(valor)


def _extract_usage(usage: Any) -> tuple[int | None, int | None]:
    """Uso de tokens. O formato varia entre versões do SDK."""
    if usage is None:
        return None, None
    if isinstance(usage, dict):
        entrada = usage.get("input_tokens")
        saida = usage.get("output_tokens")
        cache = (usage.get("cache_read_input_tokens") or 0) + (
            usage.get("cache_creation_input_tokens") or 0
        )
        if entrada is not None:
            entrada += cache
        return entrada, saida
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


def _humanize(exc: Exception) -> str:
    texto = str(exc)
    baixo = texto.lower()
    if "rate limit" in baixo or "usage limit" in baixo or "quota" in baixo:
        return (
            "Limite de uso da API atingido. Seu turno foi salvo — "
            "espere um pouco e continue de onde parou. Detalhe: " + texto
        )
    if "unauthorized" in baixo or "authentication" in baixo or "401" in texto:
        return (
            "Falha de autenticação. Confira a ANTHROPIC_API_KEY no .env. "
            "Detalhe: " + texto
        )
    if "not found" in baixo and "claude" in baixo:
        return (
            "O executável do Claude Code não foi encontrado. Instale com "
            "'npm install -g @anthropic-ai/claude-code' na VPS. Detalhe: " + texto
        )
    return texto
