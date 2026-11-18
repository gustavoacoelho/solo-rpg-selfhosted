# Plano de implementação

> **Nota da versão pública:** este é o documento original do projeto, escrito quando ele rodava com o token da assinatura Claude do autor (`CLAUDE_CODE_OAUTH_TOKEN`). A versão publicada aqui usa `ANTHROPIC_API_KEY`, porque a Anthropic não permite que apps de terceiros ofereçam o login ou os limites do claude.ai. O resto do desenho continua valendo.

Spec: `2026-09-07-mestre-rpg-design.md`

Ordem escolhida para que cada etapa seja verificável isolada, e para que a durabilidade (o requisito central) exista antes de qualquer coisa depender dela.

## Etapa 1 — Fundação e configuração

- `requirements.txt`, `.env.example`, `.gitignore`
- `app/config.py`: lê ambiente, valida `CLAUDE_CODE_OAUTH_TOKEN` presente e `ANTHROPIC_API_KEY` ausente, resolve caminhos
- **Verificação:** importar o módulo com ambiente inválido falha com mensagem clara

## Etapa 2 — Persistência

- `app/db.py`: schema, WAL, `insert_turn`, `update_partial`, `finalize_turn`, `recent_turns`, `find_orphan_partial`
- **Verificação:** `pytest tests/test_db.py` — sequência de `idx`, transições de status, recuperação de parcial

## Etapa 3 — Espelhamento

- `app/mirror.py`: append de turnos para `historico/NN-nome.md`, criação de `MESA.md` e `CANON.md` iniciais
- **Verificação:** gravar 3 turnos e conferir o Markdown resultante

## Etapa 4 — Importação da campanha

- `scripts/import_campanha.py`: lê `chat2.json` + os 18 turnos recuperados, popula banco e espelho
- **Verificação:** 304 turnos, ordem preservada, alternância correta, os turnos recuperados no lugar certo

## Etapa 5 — Contexto

- `app/context.py`: monta system prompt (MESA.md) e o prompt do turno (CANON.md + janela + ação)
- **Verificação:** tamanho dentro do orçamento; cânone e janela presentes

## Etapa 6 — Agente

- `app/agent.py`: `ClaudeAgentOptions` com `cwd`, allowlist, `can_use_tool`; streaming de blocos
- **Verificação:** `can_use_tool` aprova `CANON.md`, nega `/etc/passwd`, `../fora.md` e `historico/01.md`

## Etapa 7 — HTTP e SSE

- `app/auth.py`, `app/main.py`, rotas: `/`, `/api/login`, `/api/turn` (SSE), `/api/history`, `/api/consolidate`
- **Verificação:** fumaça de ponta a ponta com um turno real

## Etapa 8 — Front

- `static/index.html`, `app.js`, `style.css`: timeline, composer, indicador de streaming, aviso de turno parcial
- **Verificação:** jogar um turno no navegador

## Etapa 9 — Consolidação do cânone

- `app/canon.py`: relê turnos desde a última consolidação e atualiza `CANON.md`
- **Verificação:** rodar após alguns turnos e conferir o diff do cânone

## Etapa 10 — Deploy

- `run.sh`, unit systemd de exemplo, `Caddyfile` de exemplo, README com o passo a passo do `setup-token`
- **Verificação:** subir na VPS e jogar um turno

## Riscos

| Risco | Mitigação |
|---|---|
| API do Agent SDK diferente do documentado | Etapa 6 é isolada; um smoke test cedo revela antes de o resto depender |
| Cota estourar em sessão longa | Medidor de consumo na tela desde a etapa 7; janela de contexto ajustável por env |
| Política da Anthropic mudar | Camada `agent.py` isolada; trocar para API key é mudança localizada |
