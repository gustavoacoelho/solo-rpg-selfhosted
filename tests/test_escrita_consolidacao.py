"""A consolidacao ganhou Write; a trava de caminho tem que valer igual.

Write foi liberado porque reescrever o CANON.md so com Edit estoura o limite de
turnos no meio e deixa o arquivo pela metade. O risco de trocar Edit por Write e
que ele substitui o arquivo inteiro - entao a checagem de caminho passa a ser a
unica coisa entre o mestre e um arquivo destruido.

O detalhe que estes testes protegem: Write NAO pode entrar em allowed_tools. Uma
entrada la auto-aprova a ferramenta antes de o can_use_tool rodar, e a trava
seria anulada em silencio - foi exatamente o bug que o Edit ja causou uma vez.

Depende do claude_agent_sdk, entao roda dentro do container:
    docker exec -w /app mesa python tests/test_escrita_consolidacao.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent  # noqa: E402

CASOS = [
    ("CANON.md", True, "canone"),
    ("ficha-heroi.md", True, "ficha"),
    ("MESA.md", False, "regras da mesa: NEGADO"),
    ("historico/000.md", False, "historico: NEGADO"),
    ("../fora.md", False, "fora do diretorio: NEGADO"),
    ("/etc/passwd", False, "absoluto externo: NEGADO"),
    ("../../.ssh/authorized_keys", False, "escapada por ..: NEGADO"),
]


async def main() -> int:
    base = Path(tempfile.mkdtemp())
    (base / "CANON.md").write_text("canone", encoding="utf-8")
    (base / "MESA.md").write_text("regras", encoding="utf-8")
    (base / "ficha-heroi.md").write_text("ficha", encoding="utf-8")
    (base / "historico").mkdir()
    (base / "historico" / "000.md").write_text("turnos", encoding="utf-8")

    cb = agent.make_permission_callback(base)
    falhas = 0

    for ferramenta in ("Edit", "Write"):
        for caminho, esperado, desc in CASOS:
            r = await cb(ferramenta, {"file_path": caminho}, None)
            permitido = getattr(r, "behavior", None) == "allow"
            ok = permitido == esperado
            falhas += not ok
            print("  %-4s %-5s %-30s %s" % ("ok" if ok else "FALHA", ferramenta, caminho, desc))

    for ferramenta, caminho in (("Read", "historico/000.md"), ("Grep", "x")):
        r = await cb(ferramenta, {"file_path": caminho}, None)
        ok = getattr(r, "behavior", None) == "allow"
        falhas += not ok
        print("  %-4s %-5s leitura livre" % ("ok" if ok else "FALHA", ferramenta))

    checagens = [
        ("Write fora de allowed_tools", "Write" not in agent.ALLOWED_TOOLS),
        ("Edit fora de allowed_tools", "Edit" not in agent.ALLOWED_TOOLS),
        ("narracao proibe Write", "Write" in agent.DISALLOWED_TOOLS),
        ("consolidacao libera Write", "Write" not in agent.DISALLOWED_COM_ESCRITA),
    ]
    for f in ("Bash", "WebFetch", "WebSearch", "Task"):
        checagens.append((f"{f} proibido nos dois modos",
                          f in agent.DISALLOWED_TOOLS and f in agent.DISALLOWED_COM_ESCRITA))
    for desc, ok in checagens:
        falhas += not ok
        print("  %-4s %s" % ("ok" if ok else "FALHA", desc))

    print()
    print("FALHAS:", falhas)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
