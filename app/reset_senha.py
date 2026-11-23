"""Gera uma senha nova para o usuário do painel (troca obrigatória no próximo acesso).

Uso: python -m app.reset_senha [email]
"""
import secrets
import sys

from . import auth, db
from .config import settings


def main() -> None:
    auth.init()
    email = (sys.argv[1] if len(sys.argv) > 1 else settings.admin_email).lower()
    if not db.one("SELECT 1 FROM users WHERE email=?", (email,)):
        auth.ensure_admin(email, "")
    pw = secrets.token_urlsafe(9)
    db.execute("UPDATE users SET pw=?, must_change=1 WHERE email=?", (auth.hash_pw(pw), email))
    db.execute("DELETE FROM sessions WHERE email=?", (email,))
    print(f"Nova senha temporária para {email}: {pw}")


if __name__ == "__main__":
    main()
