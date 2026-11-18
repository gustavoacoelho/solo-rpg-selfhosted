"""Rotas HTTP e streaming SSE."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Cookie, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import quote

from . import agent, auth, canon, context, mirror
from .config import ConfigError, load
from .db import Database
from .registro import Cronometro, Registro
from .trava import Ocupado, Trava

try:
    cfg = load()
except ConfigError as exc:
    raise SystemExit(f"\n[configuração inválida]\n{exc}\n") from exc

mirror.ensure_scaffold(cfg.campaign_dir, cfg.mesa_path, cfg.canon_path, cfg.character_name)

db = Database(cfg.db_path)
CAMPAIGN_ID = db.ensure_campaign(cfg.campaign_title)

app = FastAPI(title="Mestre de RPG")
STATIC = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Gravado quando o mestre tenta escrever fora da allowlist. Aparece na interface.
log = logging.getLogger("mesa")

# Todo retorno do modelo é anotado aqui. Fica no volume da campanha de propósito:
# o log do container some no `docker compose up --build`, que foi como a
# evidência dos primeiros incidentes se perdeu.
registro = Registro(cfg.campaign_dir / "log-modelo.jsonl")

_denials: list[dict] = []

# Cota e ocupação da janela vêm do SDK durante o turno, e o jogador quer vê-las
# entre turnos. Ficam em memória e num arquivo, porque um restart do container
# não deveria zerar a leitura de uma janela de sete dias.
_USO_PATH = cfg.campaign_dir / "uso.json"
_uso: dict = {"cota": {}, "contexto": None, "ultimo_turno": None}


def _carregar_uso() -> None:
    try:
        _uso.update(json.loads(_USO_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def _salvar_uso() -> None:
    """Grava por arquivo temporário: um restart no meio de um write deixaria
    um JSON truncado, e aí a leitura seguinte perderia tudo."""
    try:
        tmp = _USO_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_uso, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_USO_PATH)
    except OSError:
        pass


def _registrar_cota(evento: dict) -> None:
    tipo = evento.get("limit_type") or "desconhecido"
    _uso["cota"][tipo] = {
        "utilization": evento.get("utilization"),
        "status": evento.get("status"),
        "resets_at": evento.get("resets_at"),
        "overage_status": evento.get("overage_status"),
        "visto_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _salvar_uso()


def _registrar_contexto(evento: dict) -> None:
    ctx = evento.get("contexto")
    if ctx:
        _uso["contexto"] = ctx
    _uso["ultimo_turno"] = {
        "tokens_in": evento.get("tokens_in"),
        "tokens_out": evento.get("tokens_out"),
        "custo_usd": evento.get("cost_usd"),
        "em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _salvar_uso()


_carregar_uso()

# A consolidação reescreve o CANON.md que o turno lê para montar o prompt. Com
# ela virando automática, as duas coisas podem se cruzar — duas abas abertas, ou
# o automático disparando junto de um clique. Quem chega depois leva 409 e tenta
# de novo, em vez de o mestre ler meio arquivo.
_trava = Trava()

# O turno em andamento, para poder ser cancelado. A trava garante que só existe
# um por vez, então um único slot basta. Guarda o que a limpeza precisa: a
# tarefa, as duas linhas gravadas no banco e o texto a devolver ao jogador.
_turno_em_curso: dict | None = None


def _reservar(quem: str) -> int:
    try:
        return _trava.adquirir(quem)
    except Ocupado as exc:
        raise HTTPException(
            status_code=409,
            detail=f"já há {exc.quem} em andamento; aguarde terminar",
        ) from exc


def _on_denied(tool: str, path: str, motivo: str) -> None:
    _denials.append({"tool": tool, "path": path, "motivo": motivo, "at": time.time()})


def _require_auth(token: str | None) -> None:
    if not auth.valid(token, cfg.session_secret):
        raise HTTPException(status_code=401, detail="não autenticado")


def _sse(evento: dict) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


# Intervalo do batimento. A Cloudflare derruba conexão ociosa por volta de 100
# segundos; 15 dá margem de sobra sem gerar tráfego relevante.
_BATIMENTO = 15.0
_PING = ": batimento" + chr(10) + chr(10)


def _anotar(reg: Registro, fluxo: str, idx: int | None, relogio: Cronometro, evento: dict) -> None:
    """Anota os eventos que dizem alguma coisa sobre o que o modelo fez.

    O texto da narração fica de fora: já está no banco e no espelho. O que
    interessa aqui é o que o banco não guarda — qual ferramenta foi chamada, por
    que a execução parou, quanto custou, e o que o modelo disse ao falhar.
    """
    tipo = evento.get("type")
    if tipo == "text" or tipo == "thinking":
        return
    if tipo == "tool":
        reg.anotar(fluxo, "ferramenta", idx=idx, nome=evento.get("name"),
                   em_s=relogio.s())
    elif tipo == "ratelimit":
        reg.anotar(fluxo, "cota", idx=idx, limite=evento.get("limit_type"),
                   status=evento.get("status"), uso=evento.get("utilization"),
                   renova=evento.get("resets_at"))
    elif tipo == "done":
        ctx = evento.get("contexto") or {}
        reg.anotar(fluxo, "fim", idx=idx, is_error=evento.get("is_error"),
                   terminal_reason=evento.get("terminal_reason"),
                   stop_reason=evento.get("stop_reason"),
                   tokens_in=evento.get("tokens_in"), tokens_out=evento.get("tokens_out"),
                   custo_usd=evento.get("cost_usd"), janela_pct=ctx.get("pct"),
                   duracao_s=relogio.s())
    elif tipo == "error":
        reg.anotar(fluxo, "erro", idx=idx, erro=evento.get("message"),
                   duracao_s=relogio.s())


async def _com_batimento(fonte):
    """Repassa os eventos da fonte e sinaliza os silêncios com `None`.

    A consolidação passa minutos gerando o CANON.md inteiro num único bloco, sem
    emitir evento nenhum nesse intervalo. Sem batimento a conexão morre de ócio
    no meio da escrita — foi assim que a consolidação falhou depois de ganhar o
    Write. Uma linha de comentário SSE é ignorada pelo cliente e mantém o
    caminho aberto.
    """
    fila: asyncio.Queue = asyncio.Queue()
    FIM = object()

    async def bombear() -> None:
        try:
            async for item in fonte:
                await fila.put(item)
        except Exception as exc:  # noqa: BLE001 — vira evento para o cliente
            await fila.put({"type": "error", "message": str(exc)})
        finally:
            await fila.put(FIM)

    tarefa = asyncio.create_task(bombear())
    try:
        while True:
            try:
                item = await asyncio.wait_for(fila.get(), timeout=_BATIMENTO)
            except asyncio.TimeoutError:
                yield None
                continue
            if item is FIM:
                return
            yield item
    finally:
        tarefa.cancel()


# --------------------------------------------------------------- páginas

def _versionar_assets(html: str) -> str:
    """Acrescenta ?v=<hash> às referências de /static/.

    Sem isto, um proxy no caminho (a Cloudflare, aqui) continua servindo o JS
    antigo depois de um deploy: o index.html novo carrega um app.js velho, que
    procura elementos que não existem mais e morre na primeira linha.
    Com o hash na URL, cada deploy gera um endereço novo e o cache não alcança.
    """

    def troca(m: re.Match) -> str:
        arquivo = m.group(1)
        caminho = STATIC / arquivo
        if not caminho.exists():
            return m.group(0)
        digest = hashlib.md5(caminho.read_bytes()).hexdigest()[:10]
        return f"/static/{arquivo}?v={digest}"

    return re.sub(r"/static/([A-Za-z0-9_.-]+)", troca, html)


@app.get("/")
async def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        _versionar_assets(html),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# --------------------------------------------------------------- sessão

@app.post("/api/login")
async def login(request: Request):
    corpo = await request.json()
    if not auth.password_matches(corpo.get("password", ""), cfg.app_password):
        raise HTTPException(status_code=401, detail="senha incorreta")
    resposta = JSONResponse({"ok": True})
    resposta.set_cookie(
        auth.COOKIE_NAME,
        auth.issue(cfg.session_secret),
        max_age=auth.MAX_AGE,
        httponly=True,
        samesite="strict",
    )
    return resposta


@app.post("/api/logout")
async def logout():
    resposta = JSONResponse({"ok": True})
    resposta.delete_cookie(auth.COOKIE_NAME)
    return resposta


# --------------------------------------------------------------- estado

@app.get("/api/state")
async def state(mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    orfao = db.find_orphan_partial(CAMPAIGN_ID)
    tin, tout = db.usage_totals(CAMPAIGN_ID)
    return {
        "turns": db.count_turns(CAMPAIGN_ID),
        "tokens_in": tin,
        "tokens_out": tout,
        "window": cfg.context_window_turns,
        "model": cfg.model,
        "character": cfg.character_name,
        "title": cfg.campaign_title,
        # max(0) porque remover turnos pode deixar o marcador de consolidação
        # apontando para um idx que não existe mais, o que daria negativo.
        "pending_consolidation": max(
            0, db.count_turns(CAMPAIGN_ID) - 1 - db.last_consolidated_idx(CAMPAIGN_ID)
        ),
        "orphan": {"id": orfao.id, "idx": orfao.idx, "content": orfao.content}
        if orfao
        else None,
        "denials": _denials[-5:],
        "uso": _uso,
        "auto_consolidate_at": cfg.auto_consolidate_at,
        "trava": _trava.estado(),
        "cancelavel": _turno_em_curso is not None,
    }


@app.get("/api/log")
async def ler_log(
    limite: int = 60,
    falhas: bool = False,
    mesa_sessao: str | None = Cookie(default=None),
):
    """Últimos retornos do modelo, do mais novo para o mais antigo."""
    _require_auth(mesa_sessao)
    return {"eventos": registro.recentes(min(limite, 300), apenas_falhas=falhas)}


@app.get("/api/history")
async def history(limit: int = 30, mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    turns = db.recent_turns(CAMPAIGN_ID, limit)
    return {
        "turns": [
            {"idx": t.idx, "role": t.role, "content": t.content} for t in turns
        ]
    }


@app.post("/api/discard-partial")
async def discard_partial(mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    orfao = db.find_orphan_partial(CAMPAIGN_ID)
    if orfao:
        db.discard_turn(orfao.id)
    return {"ok": True}


@app.post("/api/cancelar-turno")
async def cancelar_turno(mesa_sessao: str | None = Cookie(default=None)):
    """Interrompe o turno em andamento e devolve o texto do jogador.

    Existe porque esperar o mestre terminar para só então apagar a rodada
    desperdiça a resposta inteira — e a ação errada já foi vista por ele.

    A limpeza é feita aqui, e não no `except CancelledError` da tarefa: durante
    um cancelamento não dá para confiar em `await`, e uma limpeza pela metade
    deixaria exatamente o lixo que este endpoint existe para evitar. Esperamos a
    tarefa morrer e só então mexemos no banco.
    """
    _require_auth(mesa_sessao)
    em_curso = _turno_em_curso
    if em_curso is None:
        raise HTTPException(status_code=409, detail="não há turno em andamento")

    tarefa = em_curso["tarefa"]
    tarefa.cancel()
    try:
        await tarefa
    except (asyncio.CancelledError, Exception):  # noqa: B014 - qualquer desfecho serve
        pass

    # Corrida real: a resposta pode ter chegado entre o clique e este ponto.
    # Nesse caso a narração existe e apagá-la em silêncio seria perda de dado —
    # o jogador decide pelo botão "apagar".
    linha = db._conn.execute(
        "SELECT status, length(content) AS n FROM turn WHERE id = ?",
        (em_curso["assistant_id"],),
    ).fetchone()
    if linha and linha["status"] == "complete" and linha["n"] > 0:
        registro.anotar("turno", "cancelamento_tardio", idx=em_curso["idx"] + 1)
        return {"ok": False, "motivo": "concluido"}

    apagar = [em_curso["assistant_id"]]
    if not em_curso["retry"]:
        # Numa repetição o turno do jogador já existia antes; apagá-lo aqui
        # removeria uma ação que ele não acabou de escrever.
        apagar.append(em_curso["user_turn_id"])
    db.delete_turns(apagar)
    mirror.rebuild(db, CAMPAIGN_ID, cfg.history_dir)
    registro.anotar("turno", "cancelado_limpo", idx=em_curso["idx"] + 1,
                    linhas_removidas=len(apagar))
    return {"ok": True, "texto": em_curso["acao"], "removidos": len(apagar)}


@app.post("/api/desfazer-rodada")
async def desfazer_rodada(mesa_sessao: str | None = Cookie(default=None)):
    """Apaga a última rodada e devolve o texto do jogador.

    Serve aos dois botões da interface: "refazer" reenvia o texto devolvido na
    hora, "apagar" só o repõe na caixa de ação. É a mesma operação — a diferença
    está no que o front faz com a resposta.

    Restrito à ponta do histórico de propósito. Apagar no meio exigiria
    reindexar e regenerar tudo, e para isso existe `scripts/limpar_campanha.py`,
    que roda com backup.
    """
    _require_auth(mesa_sessao)

    if db.find_orphan_partial(CAMPAIGN_ID):
        raise HTTPException(
            status_code=409,
            detail="há um turno pela metade; descarte-o antes",
        )

    par = db.last_exchange(CAMPAIGN_ID)
    if not par:
        raise HTTPException(
            status_code=409,
            detail="não há uma rodada completa na ponta do histórico",
        )
    jogador, mestre = par

    # Rede de segurança: nada sai do banco sem deixar cópia. É barato, e torna
    # um botão destrutivo em um botão reversível na unha.
    lixeira = cfg.campaign_dir / "lixeira.jsonl"
    with lixeira.open("a", encoding="utf-8") as f:
        for turno in par:
            f.write(json.dumps({
                "removido_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "idx": turno.idx,
                "role": turno.role,
                "content": turno.content,
            }, ensure_ascii=False) + chr(10))

    db.delete_turns([jogador.id, mestre.id])
    # Sem o espelho regenerado, um Grep do mestre ainda acha o texto apagado.
    mirror.rebuild(db, CAMPAIGN_ID, cfg.history_dir)

    return {"ok": True, "texto": jogador.content, "removidos": 2}


# --------------------------------------------------------------- jogo

# Uma ação de RPG longa dele bate em ~1.100 caracteres. O teto é folgado de
# propósito, e existe para barrar o acidente: a página inteira selecionada e
# colada na caixa já entrou como turno de 144 mil caracteres, envenenando a
# janela de contexto com a conversa toda travestida de ação do jogador.
TAMANHO_MAX_ACAO = 8000


@app.post("/api/turn")
async def turn(request: Request, mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)

    # A reserva vem ANTES de tocar no banco. Na primeira versão ela ficava depois
    # dos INSERTs, e cada recusa deixava a ação do jogador gravada com um turno
    # parcial órfão pendurado — foi assim que o histórico da campanha quebrou.
    # Enquanto a tarefa não existe para liberar a trava, qualquer saída daqui
    # precisa soltá-la na mão.
    token = _reservar("um turno")
    try:
        return await _executar_turno(request, token)
    except BaseException:
        _trava.soltar(token)
        raise


async def _executar_turno(request: Request, token: int):
    corpo = await request.json()
    acao = (corpo.get("text") or "").strip()
    # Retentativa depois de uma falha: reaproveita a última ação do jogador
    # em vez de criar um turno duplicado.
    if corpo.get("retry"):
        anteriores = db.recent_turns(CAMPAIGN_ID, 4)
        ultima = next((t for t in reversed(anteriores) if t.role == "user"), None)
        if not ultima:
            raise HTTPException(status_code=400, detail="não há ação para repetir")
        acao = ultima.content
        user_turn = ultima
    else:
        if not acao:
            raise HTTPException(status_code=400, detail="ação vazia")
        if len(acao) > TAMANHO_MAX_ACAO:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"ação com {len(acao)} caracteres, acima do limite de "
                    f"{TAMANHO_MAX_ACAO}. Isso costuma ser a página inteira "
                    "colada por engano — confira a caixa antes de enviar."
                ),
            )
        # A ordem aqui é o projeto inteiro: grava e commita a ação do jogador
        # ANTES de falar com o modelo. Se tudo explodir depois, isto sobrevive.
        db.insert_turn(CAMPAIGN_ID, "user", acao, status="complete")
        user_turn = db.recent_turns(CAMPAIGN_ID, 1)[0]
        mirror.append_turn(cfg.history_dir, user_turn)

    prompt = context.build_turn_prompt(cfg, db, CAMPAIGN_ID, acao)
    system = context.build_system_prompt(cfg)
    options = agent.build_options(cfg.campaign_dir, system, cfg.model, _on_denied,
                                  max_turns=cfg.max_turns_turno)

    fila: asyncio.Queue = asyncio.Queue()
    assistant_id = db.insert_turn(CAMPAIGN_ID, "assistant", "", status="partial")

    async def produzir() -> None:
        """Consome o agente e grava. Independente da conexão do cliente:
        se o jogador fechar a aba, a resposta continua sendo salva."""
        buffer: list[str] = []
        ultimo_flush = time.monotonic()
        descartado = False
        relogio = Cronometro()
        registro.anotar("turno", "inicio", idx=user_turn.idx + 1,
                        prompt_chars=len(prompt), modelo=cfg.model)
        # O tamanho real do prompt enviado. É outra coisa que o total do
        # `model_usage`, que soma a entrada de TODAS as chamadas do laço
        # agêntico — por isso aquele número passava de 100% da janela.
        _uso["prompt"] = {
            "chars": len(prompt) + len(system),
            "tokens": (len(prompt) + len(system)) // context.CHARS_POR_TOKEN,
        }
        _salvar_uso()
        try:
            async for evento in agent.stream_turn(prompt, options):
                _anotar(registro, "turno", user_turn.idx + 1, relogio, evento)
                if evento["type"] == "text":
                    buffer.append(evento["text"])
                    agora = time.monotonic()
                    if agora - ultimo_flush > 2.0:
                        db.update_partial(assistant_id, "".join(buffer))
                        ultimo_flush = agora

                elif evento["type"] == "ratelimit":
                    _registrar_cota(evento)

                elif evento["type"] == "done":
                    _registrar_contexto(evento)
                    # Turno que terminou em erro (auth, cota, falha de rede) não
                    # é narração: descartamos para não sujar a campanha com o
                    # texto da exceção. A ação do jogador continua salva, e ele
                    # pode reenviar por /api/retry sem redigitar.
                    if evento.get("is_error"):
                        db.discard_turn(assistant_id)
                        descartado = True
                        evento["retryable"] = True
                    else:
                        texto = "".join(buffer)
                        db.finalize_turn(
                            assistant_id,
                            texto,
                            evento.get("terminal_reason"),
                            evento.get("tokens_in"),
                            evento.get("tokens_out"),
                        )
                        final = next(
                            (t for t in db.recent_turns(CAMPAIGN_ID, 2) if t.id == assistant_id),
                            None,
                        )
                        if final:
                            mirror.append_turn(cfg.history_dir, final)

                elif evento["type"] == "error" and not descartado:
                    # Guarda o que chegou até aqui como parcial, para o jogador
                    # decidir depois se aproveita ou descarta.
                    if buffer:
                        db.update_partial(assistant_id, "".join(buffer))
                    else:
                        db.discard_turn(assistant_id)
                        descartado = True
                    evento["retryable"] = True

                await fila.put(evento)
        except Exception as exc:  # noqa: BLE001
            registro.anotar("turno", "excecao", idx=user_turn.idx + 1,
                            erro=f"{type(exc).__name__}: {exc}", duracao_s=relogio.s())
            if not descartado:
                if buffer:
                    db.update_partial(assistant_id, "".join(buffer))
                else:
                    db.discard_turn(assistant_id)
            await fila.put({"type": "error", "message": str(exc), "retryable": True})
        except asyncio.CancelledError:
            # O jogador cancelou. A limpeza do banco é feita pelo endpoint, que
            # espera esta tarefa terminar antes de apagar as linhas — aqui só
            # avisamos o cliente. put_nowait porque a fila é ilimitada e um
            # await depois do cancelamento pode levantar de novo.
            registro.anotar("turno", "cancelado", idx=user_turn.idx + 1,
                            duracao_s=relogio.s(), chars_ate_agora=len("".join(buffer)))
            fila.put_nowait({"type": "cancelled"})
            raise
        finally:
            fila.put_nowait(None)

    tarefa = asyncio.create_task(produzir())

    def _encerrar(_):
        global _turno_em_curso
        _turno_em_curso = None
        _trava.soltar(token)

    tarefa.add_done_callback(_encerrar)
    global _turno_em_curso
    _turno_em_curso = {
        "tarefa": tarefa,
        "assistant_id": assistant_id,
        "user_turn_id": user_turn.id,
        "acao": acao,
        "idx": user_turn.idx,
        "retry": bool(corpo.get("retry")),
    }

    async def emitir():
        yield _sse({"type": "start", "turn_idx": user_turn.idx + 1})
        try:
            while True:
                try:
                    evento = await asyncio.wait_for(fila.get(), timeout=_BATIMENTO)
                except asyncio.TimeoutError:
                    yield _PING
                    continue
                if evento is None:
                    break
                yield _sse(evento)
        finally:
            # Cliente pode ter ido embora; a tarefa segue até gravar tudo.
            if not tarefa.done():
                pass

    return StreamingResponse(
        emitir(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/consolidate")
async def consolidate(mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)

    token = _reservar("uma consolidação")
    relogio = Cronometro()
    registro.anotar("consolidacao", "inicio",
                    pendentes=max(0, db.count_turns(CAMPAIGN_ID) - 1
                                  - db.last_consolidated_idx(CAMPAIGN_ID)),
                    canone_chars=len(cfg.canon_path.read_text(encoding="utf-8"))
                    if cfg.canon_path.exists() else 0)

    async def emitir():
        try:
            async for evento in _com_batimento(canon.consolidate(cfg, db, CAMPAIGN_ID)):
                if evento is None:
                    yield _PING
                    continue
                _anotar(registro, "consolidacao", None, relogio, evento)
                yield _sse(evento)
        finally:
            # Se o cliente recarregar a página, este finally pode nunca rodar.
            # A trava tem prazo justamente para esse caso.
            _trava.soltar(token)

    return StreamingResponse(
        emitir(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------- documentos

# Rótulos amigáveis; qualquer outro documento entra com o próprio nome.
_ROTULOS = {
    "CANON.md": "Cânone",
    "MESA.md": "Regras da mesa",
    "referencia/aparencia.md": "Aparência",
    "referencia/itens.md": "Itens (mecânica)",
}


def _documentos() -> dict[str, Path]:
    """Allowlist de documentos editáveis, montada por varredura.

    Só o que está aqui pode ser lido ou gravado pela interface — é o que
    impede um path traversal de alcançar o resto do disco.
    """
    encontrados: dict[str, Path] = {}
    for p in sorted(cfg.campaign_dir.glob("*.md")):
        encontrados[p.name] = p
    for p in sorted((cfg.campaign_dir / "referencia").glob("*.md")):
        encontrados[f"referencia/{p.name}"] = p
    return encontrados


def _resolver(rel: str) -> Path:
    doc = _documentos().get(rel)
    if doc is None:
        raise HTTPException(status_code=404, detail="documento não encontrado")
    return doc


@app.get("/api/docs")
async def list_docs(mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    itens = []
    for rel, p in _documentos().items():
        itens.append(
            {
                "path": rel,
                "label": _ROTULOS.get(rel)
                or rel.replace("referencia/", "").replace("ficha-", "Ficha: ").removesuffix(".md"),
                "size": p.stat().st_size,
                # Fichas completas são enormes e servem só de consulta do mestre.
                "readonly": rel.endswith("-ficha-completa.md"),
            }
        )
    return {"docs": itens}


@app.get("/api/doc")
async def get_doc(path: str, mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    doc = _resolver(path)
    return {"path": path, "content": doc.read_text(encoding="utf-8")}


@app.put("/api/doc")
async def put_doc(request: Request, mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    corpo = await request.json()
    doc = _resolver(corpo.get("path", ""))
    conteudo = corpo.get("content")
    if not isinstance(conteudo, str):
        raise HTTPException(status_code=400, detail="conteúdo ausente")
    # Guarda a versão anterior: uma edição desastrada não deve custar o cânone.
    doc.with_suffix(doc.suffix + ".bak").write_text(
        doc.read_text(encoding="utf-8"), encoding="utf-8"
    )
    doc.write_text(conteudo, encoding="utf-8")
    return {"ok": True, "size": len(conteudo)}


# --------------------------------------------------------------- retratos

# As imagens-âncora de cada personagem: o cânone visual que mantém a aparência
# estável entre gerações. Ficam em disco, no volume, e entram no backup.
RETRATOS = cfg.campaign_dir / "retratos"
EXTENSOES_OK = {".png", ".jpg", ".jpeg", ".webp"}
TAMANHO_MAX = 12 * 1024 * 1024  # 12 MB

_GALERIAS = list(cfg.galleries)  # PORTRAIT_GALLERIES no .env
_SLUGS = {s for s, _ in _GALERIAS}


def _pasta_retrato(slug: str) -> Path:
    if slug not in _SLUGS:
        raise HTTPException(status_code=404, detail="galeria desconhecida")
    p = RETRATOS / slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def _nome_seguro(nome: str) -> str:
    """Só o nome do arquivo, sem diretório, sem caracteres de escape."""
    base = Path(nome).name
    limpo = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80]
    if not limpo or limpo.startswith("."):
        raise HTTPException(status_code=400, detail="nome de arquivo inválido")
    return limpo


@app.get("/api/retratos")
async def list_retratos(mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    galerias = []
    for slug, label in _GALERIAS:
        pasta = RETRATOS / slug
        imagens = []
        if pasta.is_dir():
            for f in sorted(pasta.iterdir()):
                if f.suffix.lower() in EXTENSOES_OK:
                    imagens.append(
                        {
                            "nome": f.name,
                            "url": f"/api/retrato?slug={slug}&nome={quote(f.name)}",
                            "size": f.stat().st_size,
                        }
                    )
        galerias.append({"slug": slug, "label": label, "imagens": imagens})
    return {"galerias": galerias}


@app.get("/api/retrato")
async def get_retrato(slug: str, nome: str, mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    caminho = _pasta_retrato(slug) / _nome_seguro(nome)
    if not caminho.is_file():
        raise HTTPException(status_code=404, detail="imagem não encontrada")
    return FileResponse(caminho, headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/retrato")
async def upload_retrato(
    slug: str = Form(...),
    arquivo: UploadFile = File(...),
    mesa_sessao: str | None = Cookie(default=None),
):
    _require_auth(mesa_sessao)
    pasta = _pasta_retrato(slug)

    nome = _nome_seguro(arquivo.filename or "imagem.png")
    if Path(nome).suffix.lower() not in EXTENSOES_OK:
        raise HTTPException(
            status_code=400,
            detail=f"formato não aceito; use {', '.join(sorted(EXTENSOES_OK))}",
        )

    dados = await arquivo.read()
    if len(dados) > TAMANHO_MAX:
        raise HTTPException(status_code=413, detail="imagem acima de 12 MB")
    if not dados:
        raise HTTPException(status_code=400, detail="arquivo vazio")

    # Não sobrescreve silenciosamente: um upload repetido vira -2, -3...
    destino = pasta / nome
    if destino.exists():
        raiz, ext = destino.stem, destino.suffix
        n = 2
        while (pasta / f"{raiz}-{n}{ext}").exists():
            n += 1
        destino = pasta / f"{raiz}-{n}{ext}"

    destino.write_bytes(dados)
    return {"ok": True, "nome": destino.name, "size": len(dados)}


@app.delete("/api/retrato")
async def delete_retrato(slug: str, nome: str, mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    caminho = _pasta_retrato(slug) / _nome_seguro(nome)
    if caminho.is_file():
        caminho.unlink()
    return {"ok": True}


# Mantido para compatibilidade com versões anteriores do front.
@app.get("/api/canon")
async def get_canon(mesa_sessao: str | None = Cookie(default=None)):
    _require_auth(mesa_sessao)
    texto = cfg.canon_path.read_text(encoding="utf-8") if cfg.canon_path.exists() else ""
    return {"canon": texto}
