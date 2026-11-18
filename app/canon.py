"""Consolidação do cânone.

Roda fora do fluxo de jogo — ao encerrar a sessão ou sob comando. O mestre
relê os turnos desde a última consolidação e reescreve CANON.md. É a única
escrita que ele faz, e a única vez que a ferramenta Edit é exercida.
"""

from __future__ import annotations

from typing import AsyncIterator

from . import agent, context
from .config import Config
from .db import Database

INSTRUCAO = """Encerre a sessão atualizando o cânone da campanha.

Leia CANON.md (já está no seu contexto) e os turnos novos abaixo. Depois use a
ferramenta **Write** para gravar CANON.md **inteiro, de uma vez só**.

Use Write, não Edit. Remendar seção por seção com Edit gasta uma ida e volta por
trecho, estoura o limite de turnos no meio da reescrita e deixa o arquivo pela
metade — foi o que aconteceu nas últimas tentativas. Monte o texto final
completo e grave uma vez.

O que o cânone precisa refletir depois da sua edição:

- **Personagens** — quem faz parte do grupo, o que cada um sabe e carrega,
  NPCs relevantes e onde estão.
- **Locais** — lugares estabelecidos e o estado atual de cada um.
- **Facções** — quem são os antagonistas e o que sabem sobre o grupo.
- **Itens** — artefatos, recursos, dinheiro do grupo.
- **Situação atual** — exatamente onde a história parou, com detalhe suficiente
  para retomar sem reler o histórico.
- **Ganchos em aberto** — fios soltos que ainda podem ser puxados.

Regras da edição:

- Escreva o arquivo inteiro, não um apêndice. O cânone é um retrato do presente,
  não um diário — informação superada sai.
- Mantenha o que continua verdadeiro, mesmo que não tenha sido citado nos turnos
  novos. Não apague o passado da campanha por ele não ter aparecido nesta sessão.
- Seja específico: nomes, valores, quantias. Um cânone vago não serve para nada.
- Não narre. Isto é documento de referência, não cena.

## Orçamento de tamanho — leia antes de escrever

O cânone precisa caber em cerca de **30.000 caracteres**. Ele cresce a cada
consolidação, e chegou a mais que o dobro disso: agora ele ocupa mais espaço no
contexto do mestre do que os turnos recentes da campanha. **Toda consolidação é
também uma poda.**

Se o arquivo estiver acima do orçamento, corte nesta ordem:

1. **Cenas antigas já resolvidas viram uma linha.** O histórico completo está em
   disco e você pode buscá-lo com Grep quando precisar — o cânone é o índice,
   não o arquivo.
2. **NPCs que saíram de cena** e não têm retorno previsto saem, ou viram uma
   menção de uma linha no local onde ficaram.
3. **Detalhe de rolagem e de combate passado não fica.** Só o resultado que
   ainda muda alguma coisa.
4. **Texto repetido entre seções** fica em uma só.

**Nunca corte:** o estado atual, as fichas e itens dos personagens, as decisões
de regra firmadas na mesa, e os ganchos em aberto.

Ao terminar a edição, responda em uma ou duas frases o que mudou, e informe o
tamanho final do arquivo.
"""


async def consolidate(cfg: Config, db: Database, campaign_id: int) -> AsyncIterator[dict]:
    """Atualiza CANON.md a partir dos turnos ainda não consolidados."""
    desde = db.last_consolidated_idx(campaign_id)
    novos = db.turns_since(campaign_id, desde)

    if not novos:
        yield {"type": "error", "message": "Nada novo para consolidar desde a última vez."}
        return

    canon = cfg.canon_path.read_text(encoding="utf-8") if cfg.canon_path.exists() else ""
    corpo = "\n\n".join(
        f"[{t.idx}] {'JOGADOR' if t.role == 'user' else 'MESTRE'}:\n{t.content.strip()}"
        for t in novos
    )

    prompt = "\n".join(
        [
            INSTRUCAO,
            "",
            "<canone_atual>",
            canon.strip(),
            "</canone_atual>",
            "",
            f"<turnos_novos quantidade='{len(novos)}'>",
            corpo,
            "</turnos_novos>",
        ]
    )

    options = agent.build_options(
        campaign_dir=cfg.campaign_dir,
        system_prompt=context.build_system_prompt(cfg),
        model=cfg.model,
        # O cânone passou de 25 KB, e o mestre o reescreve por Edits sucessivos:
        # cada seção é uma chamada. Com 8 ele estourava o limite no meio da
        # reescrita, deixando o arquivo pela metade e ainda assim marcando a
        # consolidação como feita. 30 dá folga para ler, buscar e reescrever.
        max_turns=30,
        # Libera Write só aqui. O callback de permissão continua conferindo o
        # caminho: segue valendo só CANON.md e as fichas.
        permitir_escrita=True,
    )

    ok = False
    async for evento in agent.stream_turn(prompt, options):
        # `done` também chega quando a execução termina EM ERRO — estouro de
        # max_turns, cota, queda. Marcar a consolidação como concluída nesses
        # casos zera o contador de pendentes sobre um cânone incompleto, e a
        # sessão seguinte começa achando que está tudo registrado.
        if evento["type"] == "done" and not evento.get("is_error"):
            ok = True
        yield evento

    if ok:
        db.record_consolidation(campaign_id, novos[-1].idx)
        yield {"type": "consolidated", "turns": len(novos), "up_to": novos[-1].idx}
