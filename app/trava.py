"""Serialização das chamadas ao modelo.

Um turno e uma consolidação não podem rodar ao mesmo tempo: a consolidação
reescreve o CANON.md que o turno lê para montar o prompt. Com a consolidação
automática, esse cruzamento deixou de ser hipotético.

Duas propriedades que a primeira versão não tinha, e cuja falta quebrou o
histórico da campanha:

- **A trava expira.** Ela era liberada no `finally` de um gerador SSE. Quando o
  cliente recarrega a página no meio de uma consolidação, o gerador pode nunca
  ser finalizado, e a trava ficava presa até o container reiniciar — deixando
  todo turno seguinte com 409. Uma execução real não passa de alguns minutos.

- **Só o dono solta.** A liberação carrega um token. Sem isso, a liberação
  atrasada de uma execução que já expirou derrubaria a trava de quem assumiu
  depois.
"""

from __future__ import annotations

import time

LIMITE_PADRAO = 900.0  # 15 min: acima de qualquer turno ou consolidação real


class Ocupado(Exception):
    """Já há uma execução em andamento."""

    def __init__(self, quem: str, ha_quanto_tempo: float):
        self.quem = quem
        self.ha_quanto_tempo = ha_quanto_tempo
        super().__init__(f"já há {quem} em andamento")


class Trava:
    def __init__(self, limite: float = LIMITE_PADRAO):
        self._limite = limite
        self._quem: str | None = None
        self._desde = 0.0
        self._token = 0

    def adquirir(self, quem: str, agora: float | None = None) -> int:
        agora = time.monotonic() if agora is None else agora
        if self._quem is not None and agora - self._desde < self._limite:
            raise Ocupado(self._quem, agora - self._desde)
        # Passou do limite: a execução anterior sumiu sem liberar. O token novo
        # invalida a liberação atrasada dela, se ainda vier.
        self._token += 1
        self._quem = quem
        self._desde = agora
        return self._token

    def soltar(self, token: int) -> bool:
        """Devolve True se este dono ainda era o detentor da trava."""
        if token != self._token or self._quem is None:
            return False
        self._quem = None
        return True

    def estado(self, agora: float | None = None) -> dict:
        agora = time.monotonic() if agora is None else agora
        if self._quem is None:
            return {"ocupada": False}
        return {
            "ocupada": True,
            "quem": self._quem,
            "ha_segundos": round(agora - self._desde, 1),
            "expira_em": round(self._limite - (agora - self._desde), 1),
        }
