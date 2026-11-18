"""Registro dos retornos do modelo, em disco.

Até aqui o desfecho de cada chamada só existia dentro do stream SSE: quando o
jogador dizia "deu erro", não havia nada no servidor para consultar, e a causa
tinha que ser deduzida de um print de tela.

O log do container não resolve: `docker compose up --build` recria o container e
leva os logs junto — foi assim que a evidência dos dois primeiros incidentes se
perdeu. Por isso o arquivo mora no **volume da campanha**: sobrevive a rebuild,
entra no backup e é um JSONL que dá para filtrar com grep.

Não guarda o texto da narração: isso já está no banco e no espelho. Guarda o que
o banco não guarda — qual ferramenta foi chamada, quanto custou, por que parou,
e o que o modelo disse quando falhou.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

LIMITE_PADRAO = 5_000_000  # ~5 MB; cada linha tem algumas centenas de bytes


class Registro:
    def __init__(self, caminho: Path, limite: int = LIMITE_PADRAO):
        self.caminho = caminho
        self.limite = limite

    def anotar(self, fluxo: str, tipo: str, **campos) -> None:
        """Grava uma linha. Nunca levanta: log que derruba o turno é pior que
        log ausente."""
        linha = {
            "em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fluxo": fluxo,
            "tipo": tipo,
        }
        linha.update({k: v for k, v in campos.items() if v is not None})
        try:
            self._girar()
            with self.caminho.open("a", encoding="utf-8") as f:
                f.write(json.dumps(linha, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _girar(self) -> None:
        """Ao passar do limite, guarda uma geração anterior e recomeça."""
        try:
            if self.caminho.exists() and self.caminho.stat().st_size > self.limite:
                anterior = self.caminho.with_suffix(self.caminho.suffix + ".1")
                os.replace(self.caminho, anterior)
        except OSError:
            pass

    def recentes(self, limite: int = 50, apenas_falhas: bool = False) -> list[dict]:
        """Últimas linhas, da mais nova para a mais antiga."""
        try:
            linhas = self.caminho.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        saida: list[dict] = []
        for bruta in reversed(linhas):
            try:
                item = json.loads(bruta)
            except ValueError:
                continue
            if apenas_falhas and not (item.get("erro") or item.get("is_error")):
                continue
            saida.append(item)
            if len(saida) >= limite:
                break
        return saida


class Cronometro:
    """Mede a duração de um fluxo para o registro."""

    def __init__(self):
        self.inicio = time.monotonic()

    def s(self) -> float:
        return round(time.monotonic() - self.inicio, 1)
