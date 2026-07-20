"""
JWT auth for the API. Single-tenant/local-desktop shaped: a short-lived
bearer token issued at login, validated on every other request. Reuses the
existing `services.auth_service.authenticate` (bcrypt password check)
against the same `users` table Streamlit uses — one set of accounts for
both frontends.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings
from database.db import get_db
from services.auth_service import AuthError, authenticate

ALGORITHM = "HS256"
TOKEN_TTL_HOURS = 12

_security = HTTPBearer(auto_error=False)


def create_token(user) -> str:
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role.value,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def login(username: str, password: str) -> dict:
    with get_db() as db:
        try:
            user = authenticate(db, username, password)
        except AuthError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        token = create_token(user)
        return {
            "token": token,
            "user": {"id": user.id, "username": user.username, "email": user.email, "role": user.role.value},
        }


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(_security)) -> dict:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc
    return {"id": int(payload["sub"]), "username": payload["username"], "role": payload["role"]}


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "Admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user
