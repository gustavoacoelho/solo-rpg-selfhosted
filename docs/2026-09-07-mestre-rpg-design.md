# Mestre de RPG — spec de design

> **Nota da versão pública:** este é o documento original do projeto, escrito quando ele rodava com o token da assinatura Claude do autor (`CLAUDE_CODE_OAUTH_TOKEN`). A versão publicada aqui usa `ANTHROPIC_API_KEY`, porque a Anthropic não permite que apps de terceiros ofereçam o login ou os limites do claude.ai. O resto do desenho continua valendo.

**Data:** 2026-09-07
**Autor:** Gustavo (design colaborativo)
**Status:** aprovado

## Problema

Uma campanha solo de D&D 5.5e vinha sendo jogada na interface web do Gemini. Em 06/09/2026 a conversa dessincronizou e apagou ~3h de sessão (18 turnos, entre 00:05 e 03:30). Os turnos foram recuperados depois pela Atividade dos apps do Google, mas o episódio expôs o problema real: **a persistência da campanha estava na mão de um terceiro, sem garantia e sem backup.**

Este projeto move a campanha para uma VPS controlada pelo jogador, com um mestre baseado em Claude, onde a durabilidade é uma propriedade do sistema e não uma esperança.

## Objetivos

1. **Nunca perder uma sessão.** No pior caso, perde-se o turno em andamento — nunca o histórico.
2. **Continuar a campanha existente**, com os 304 turnos importados, incluindo os 18 recuperados.
3. **Rodar sob a assinatura Claude do jogador**, sem fatura de API.
4. **O cânone é do jogador**, em arquivos de texto legíveis, portáveis para fora do app.

## Não-objetivos (v1)

- Geração de imagens. A arquitetura fica preparada (tabela `asset`), a integração fica para depois.
- Multiusuário, contas, papéis. **O app é single-user por decisão de projeto e de política** (ver Restrições).
- Edição de turnos passados, ficha interativa, rolador de dados no cliente. O mestre rola e expõe, como já é a convenção da mesa.
- Busca vetorial / embeddings. Busca textual resolve neste corpus (ver Decisões).

## Restrições

### Política de uso da assinatura

A Anthropic permite que o Agent SDK e apps próprios consumam os limites da assinatura do usuário. A mesma documentação proíbe **oferecer login ou limites do claude.ai a terceiros**, inclusive em produtos construídos sobre o Agent SDK.

Consequência de projeto, não negociável: **este app é de uso pessoal, single-user, protegido por senha.** Não deve ser aberto a outros jogadores nem distribuído com a credencial embutida. Abrir para um grupo exigiria migrar para API key com cobrança por uso.

### Autenticação

`CLAUDE_CODE_OAUTH_TOKEN`, gerado com `claude setup-token` numa máquina com navegador e copiado para a VPS. Validade de 1 ano; precisa ser regerado.

`ANTHROPIC_API_KEY` **não pode existir** no ambiente do processo — a presença das duas credenciais causa conflito.

### Cota

Consumo sai dos limites da assinatura (janela de 5h + semanal). Isso torna o tamanho do contexto uma decisão de projeto, não um detalhe: mandar a campanha inteira a cada turno inviabilizaria sessões longas.

## Arquitetura

```
┌──────────────┐   SSE    ┌─────────────────┐         ┌──────────────┐
│  Navegador   │◄────────►│  FastAPI        │────────►│ Agent SDK    │
│  HTML+JS     │  fetch   │  (uvicorn)      │         │ (assinatura) │
└──────────────┘          └────────┬────────┘         └──────┬───────┘
                                   │                         │
                          ┌────────▼────────┐         ┌──────▼───────┐
                          │  campanha.db    │         │ /srv/campanha│
                          │  (SQLite/WAL)   │────────►│  *.md        │
                          │  fonte de verdade│ espelha│  (leitura do │
                          └─────────────────┘         │   mestre)    │
                                                      └──────────────┘
```

Backend Python + FastAPI, servindo API e front. Front em HTML/JS puro, sem build e sem `node_modules` na VPS. Streaming por SSE.

### Componentes

| Módulo | Responsabilidade | Depende de |
|---|---|---|
| `app/config.py` | Configuração via ambiente; valida credencial e ausência de `ANTHROPIC_API_KEY` | — |
| `app/db.py` | Schema, transações, gravação de turnos | sqlite3 |
| `app/mirror.py` | Espelha turnos do banco para `historico/*.md` | db |
| `app/context.py` | Monta o prompt do turno (cânone + janela recente) | db |
| `app/agent.py` | Wrapper do Agent SDK: opções, permissões, streaming | claude-agent-sdk |
| `app/canon.py` | Consolidação do `CANON.md` ao fim da sessão | agent, db |
| `app/auth.py` | Senha única + cookie de sessão | — |
| `app/main.py` | Rotas HTTP e SSE | todos |
| `scripts/import_campanha.py` | Importa o histórico do Gemini para o banco | db, mirror |

Cada módulo tem uma responsabilidade e é testável isolado. `main.py` não contém lógica de domínio.

## Durabilidade — o requisito central

A ordem das operações é o coração do projeto:

1. Ação do jogador chega → `INSERT` do turno `user` → **COMMIT** → só então chama o modelo.
2. Resposta chega em stream → texto acumulado → `UPDATE` do turno `assistant` com `status='partial'` a cada ~2s ou 500 caracteres.
3. Fim do stream → `UPDATE` final com `status='complete'`, `terminal_reason`, uso de tokens → COMMIT → espelha para `.md`.

Falha em qualquer ponto deixa o banco consistente. Um turno `partial` órfão é detectado na abertura e o jogador escolhe retomar ou descartar. O rollback silencioso que motivou o projeto é estruturalmente impossível: nada é apagado, só acrescentado.

SQLite em modo WAL, `synchronous=FULL`. Um arquivo — backup é `cp`.

### Schema

```sql
campaign(id, name, created_at)
turn(id, campaign_id, idx, role, content, status, terminal_reason,
     tokens_in, tokens_out, created_at, updated_at)
session(id, campaign_id, started_at, ended_at, consolidated_at)
asset(id, turn_id, path, prompt, created_at)   -- v1: criada, não usada
```

`turn.idx` é a ordem canônica e monotônica. `status ∈ {partial, complete, discarded}`.

## Contexto — cânone vivo + busca sob demanda

Em vez de reenviar a campanha (~200k tokens) todo turno, o mestre recebe um contexto enxuto e **vai buscar o que faltar**:

**Sempre no contexto (~15–20k tokens):**
- `MESA.md` — regras da mesa, no system prompt. Estático, cacheável.
- `CANON.md` — estado vivo: personagens, locais, itens, situação atual.
- Últimos 15 turnos na íntegra.

**Sob demanda, por ferramenta:**
- `historico/*.md` — a campanha completa em disco. Quando precisa de um detalhe antigo ("o que a Nemeia falou sobre os chifres?"), o mestre usa Grep/Read e traz só o trecho.

Isso troca "carregar 200k por precaução" por "buscar 2k quando precisa". O prefixo (system + cânone) é estável entre turnos, então o cache do Claude Code atua sobre ele.

### Consolidação do cânone

Ao encerrar a sessão (ou sob comando manual), um passo separado relê os turnos novos e atualiza o `CANON.md`. Fora do fluxo de jogo, para não gastar cota nem quebrar o ritmo. É a única escrita que o mestre faz.

## Segurança

O mestre tem ferramentas de arquivo numa VPS. Contenção em três camadas:

1. **`cwd` fixo** em `/srv/campanha`. Nada fora disso é alcançável.
2. **Allowlist de ferramentas:** `Read`, `Grep`, `Glob`, `Edit`. Sem `Bash`, sem `Write`, sem `WebFetch`, sem `WebSearch`.
3. **Callback `can_use_tool`:** mesmo com `Edit` liberado, só passa se o alvo for `CANON.md` ou `ficha-*.md`. Qualquer outro caminho é negado e registrado.

Um NPC não precisa de shell.

Acesso ao app: senha única em `APP_PASSWORD`, cookie de sessão `HttpOnly`/`SameSite=Strict`. Sem cadastro, sem recuperação — é um app de uma pessoa. HTTPS terminado por Caddy.

O token OAuth vive só no ambiente do processo, nunca chega ao front, nunca é logado.

## Fluxo de dados de um turno

```
POST /api/turn {texto}
  → auth
  → db.insert_turn(user) + COMMIT
  → context.build() lê CANON.md + últimos 15 turnos do banco
  → agent.stream() com options restritas
  → SSE: token | tool_use | done
      ├─ a cada ~2s: db.update_partial()
      └─ fim: db.finalize() + mirror.append()
```

Se o cliente desconectar, o servidor **continua** consumindo o stream até o fim e grava. A resposta não se perde por o jogador ter fechado a aba.

## Erros

| Falha | Comportamento |
|---|---|
| Cota da assinatura esgotada | Erro explícito na tela com o horário de reset. Turno do jogador já está salvo. |
| Token OAuth expirado/inválido | Erro claro na inicialização, com o comando de regeneração. |
| Stream interrompido no meio | Turno fica `partial`; ao reabrir, o jogador retoma ou descarta. |
| `ANTHROPIC_API_KEY` presente | Recusa subir, com explicação. Falha barulhenta, não silenciosa. |
| Mestre tenta editar fora da allowlist | Ferramenta negada, evento registrado, jogo continua. |

## Testes

- `db.py`: sequência de `idx`, transição de status, recuperação de `partial`.
- `context.py`: janela correta, cânone presente, limite de tamanho.
- `agent.py`: `can_use_tool` aprova `CANON.md` e nega `/etc/passwd`, `../`, `historico/*`.
- `import_campanha.py`: 304 turnos, ordem preservada, alternância user/assistant.
- Fumaça: subir, autenticar, um turno de ponta a ponta contra a API real.

## Decisões e alternativas descartadas

| Decisão | Alternativa | Por quê |
|---|---|---|
| Agent SDK com assinatura | API key paga | O jogador já paga a assinatura; sem fatura nova. Custo: consome cota, e a política pode mudar. |
| Sem fallback para API key | Dois backends | Pedido explícito. Simplifica; se a política mudar, é uma refatoração pequena e localizada. |
| Cânone vivo + grep | Campanha inteira no contexto | Cota. 200k/turno esgota a janela de 5h no meio da sessão. |
| Cânone vivo + grep | RAG com embeddings | Banco vetorial e pipeline de indexação para um corpus de 600k caracteres onde nomes próprios (Kiyomi, Vanthampur) favorecem busca literal. |
| SQLite | Postgres | Um jogador, um arquivo, backup por `cp`. |
| HTML/JS puro | React/Svelte | Sem build e sem toolchain de node na VPS. |
| `query()` por turno | Sessão viva do SDK | Stateless sobrevive a restart; o contexto fica sob controle explícito em vez de crescer sozinho. |

## Portabilidade

O cânone e o histórico são Markdown em disco. Se o app for abandonado, trocar de modelo ou voltar para outra ferramenta, a campanha vai junto, legível, sem exportador.
