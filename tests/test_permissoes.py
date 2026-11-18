import asyncio, sys, os, tempfile
sys.path.insert(0, '.')
from pathlib import Path
from app.agent import make_permission_callback

base = Path(tempfile.mkdtemp(prefix='campanha-teste-')).resolve()
negados = []
cb = make_permission_callback(base, lambda t,p,m: negados.append((p,m)))

CASOS = [
    ("Edit", "CANON.md",                    True,  "canone: permitido"),
    ("Edit", str(base/"CANON.md"),          True,  "canone absoluto: permitido"),
    ("Edit", "ficha-heroi.md",             True,  "ficha: permitido"),
    ("Edit", "historico/000-turnos-0000-0039.md", False, "historico: NEGADO"),
    ("Edit", "MESA.md",                     False, "regras da mesa: NEGADO"),
    ("Edit", "../fora.md",                  False, "fora do dir: NEGADO"),
    ("Edit", "/etc/passwd",                 False, "path absoluto externo: NEGADO"),
    ("Edit", "../../.ssh/authorized_keys",  False, "escapada por ..: NEGADO"),
    ("Read", "historico/qualquer.md",       True,  "leitura livre"),
    ("Grep", "",                            True,  "busca livre"),
]

async def main():
    (base/"ficha-heroi.md").touch()
    falhas = 0
    for tool, path, esperado, desc in CASOS:
        r = await cb(tool, {"file_path": path} if path else {}, None)
        permitido = getattr(r, "behavior", None) == "allow"
        ok = permitido == esperado
        falhas += not ok
        print(f"  {'PASS' if ok else 'FALHA'}  {desc}")
    print()
    print(f"negados registrados: {len(negados)}")
    print("resultado:", "TODOS OS CASOS PASSARAM" if not falhas else f"{falhas} FALHA(S)")
    return falhas

sys.exit(asyncio.run(main()))
