#!/usr/bin/env python3
"""Importa uma campanha já jogada em outro chat para o banco e o espelho.

Uso:
    python -m scripts.import_campanha dados/turnos.json --canon dados/cronica.txt

O JSON é uma lista de pares [papel, texto] em ordem cronológica, onde papel é
"JOGADOR" ou "MESTRE". Qualquer papel diferente de "MESTRE" conta como jogador.

Idempotente por recusa: se o banco já tiver turnos, o script para e pede
--force, para não duplicar a campanha inteira por engano.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A importação não fala com o modelo, então basta um ambiente mínimo para
# a config aceitar carregar.
os.environ.setdefault("ANTHROPIC_API_KEY", "importacao")
os.environ.setdefault("APP_PASSWORD", "importacao")

from app import mirror  # noqa: E402
from app.config import load  # noqa: E402
from app.db import Database  # noqa: E402

PAPEL = {"MESTRE": "assistant"}  # o resto é "user"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", type=Path, help="arquivo com os turnos")
    ap.add_argument("--canon", type=Path, help="texto para semear o CANON.md")
    ap.add_argument("--force", action="store_true", help="importa mesmo com o banco populado")
    args = ap.parse_args()

    cfg = load()
    mirror.ensure_scaffold(cfg.campaign_dir, cfg.mesa_path, cfg.canon_path, cfg.character_name)
    db = Database(cfg.db_path)
    campaign_id = db.ensure_campaign(cfg.campaign_title)

    existentes = db.count_turns(campaign_id)
    if existentes and not args.force:
        print(f"O banco já tem {existentes} turnos. Use --force se quiser importar mesmo assim.")
        return 1

    dados = json.loads(args.json_path.read_text(encoding="utf-8"))

    importados = ignorados = 0
    for entrada in dados:
        if not isinstance(entrada, (list, tuple)) or len(entrada) != 2:
            ignorados += 1
            continue
        papel_bruto, texto = entrada
        papel = PAPEL.get(str(papel_bruto).strip().upper(), "user")
        texto = (texto or "").strip()
        if not papel:
            ignorados += 1
            continue
        if not texto:
            # Respostas só com imagem podem vir sem texto. Guardamos um
            # marcador para não quebrar a alternância nem apagar o registro
            # de que houve uma ilustração ali.
            if papel != "assistant":
                ignorados += 1
                continue
            texto = "*(o mestre gerou uma imagem neste turno; a arte não veio no histórico)*"
        db.insert_turn(campaign_id, papel, texto, status="complete")
        importados += 1

    n = mirror.rebuild(db, campaign_id, cfg.history_dir)

    if args.canon and args.canon.exists():
        cfg.canon_path.write_text(
            "# Cânone da campanha\n\n"
            "*Semeado a partir da crônica compilada da campanha original. "
            "Será reescrito na próxima consolidação.*\n\n"
            + args.canon.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        print(f"CANON.md semeado com {args.canon}")

    # A crônica cobre a campanha até aqui, então marcamos como consolidada:
    # a próxima consolidação processa só o que for jogado a partir de agora.
    if importados:
        db.record_consolidation(campaign_id, db.next_idx(campaign_id) - 1)

    print(f"Importados: {importados} turnos ({ignorados} ignorados)")
    print(f"Espelhados: {n} turnos em {cfg.history_dir}")
    print(f"Banco: {cfg.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
