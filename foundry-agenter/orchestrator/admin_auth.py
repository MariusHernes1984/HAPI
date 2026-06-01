"""
Admin auth — HTTPBasic password gate.

Krever ADMIN_PASSWORD env-var (Azure Key Vault i prod).
Bruker secrets.compare_digest mot timing-attacks.
Brukernavn er fritt valgt og brukes kun i audit-logg.
"""

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_security = HTTPBasic(realm="HAPI Admin")


def verify_admin(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    """FastAPI dependency: returnerer brukernavn ved gyldig auth, ellers 401."""
    expected = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not expected:
        # Fail-closed: hvis passord ikke er satt, gi tydelig 503.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_PASSWORD er ikke konfigurert på serveren.",
        )
    # Constant-time sammenligning på begge felt for å unngå timing-leakage
    user_ok = bool(credentials.username)  # ikke-tomt brukernavn
    pass_ok = secrets.compare_digest(credentials.password.encode("utf-8"), expected.encode("utf-8"))
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ugyldig brukernavn eller passord",
            headers={"WWW-Authenticate": 'Basic realm="HAPI Admin"'},
        )
    return credentials.username
