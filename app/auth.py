"""Senha única e cookie de sessão assinado.

Sem cadastro, sem recuperação, sem papéis: é um app de uma pessoa. A senha
existe porque a instância fica exposta na internet carregando a chave de API
do dono, e cada turno custa dinheiro.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

COOKIE_NAME = "mesa_sessao"
MAX_AGE = 60 * 60 * 24 * 30  # 30 dias


def issue(secret: str) -> str:
    expira = int(time.time()) + MAX_AGE
    corpo = str(expira)
    return f"{corpo}.{_sign(corpo, secret)}"


def valid(token: str | None, secret: str) -> bool:
    if not token or "." not in token:
        return False
    corpo, assinatura = token.rsplit(".", 1)
    if not hmac.compare_digest(assinatura, _sign(corpo, secret)):
        return False
    try:
        return int(corpo) > int(time.time())
    except ValueError:
        return False


def password_matches(given: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(given.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def _sign(corpo: str, secret: str) -> str:
    return hmac.new(secret.encode(), corpo.encode(), hashlib.sha256).hexdigest()


def new_secret() -> str:
    return secrets.token_hex(32)
