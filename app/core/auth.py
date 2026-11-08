from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import verify_password
from app.db.database import get_db
from app.models import User


router = APIRouter()
bearer = HTTPBearer(auto_error=False)
TOKEN_LIFETIME = timedelta(hours=1)
TOKEN_ISSUER = "enterprise-rag"
DB = Annotated[Session, Depends(get_db)]


class LoginRequest(BaseModel):
    username: Annotated[str, Field(min_length=1, max_length=255)]
    password: SecretStr


def signing_key() -> str:
    secret = settings.auth_token_secret.get_secret_value()
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(503, "登录服务未配置")
    return secret


def unauthorized() -> HTTPException:
    return HTTPException(401, "登录凭证无效", headers={"WWW-Authenticate": "Bearer"})


@router.post("/auth/login")
def login(payload: LoginRequest, db: DB):
    secret = signing_key()
    user = db.scalar(select(User).where(User.username == payload.username))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password.get_secret_value(), user.password_hash)
    ):
        raise unauthorized()

    expires_at = datetime.now(timezone.utc) + TOKEN_LIFETIME
    token = jwt.encode(
        {"sub": user.id, "exp": expires_at, "iss": TOKEN_ISSUER}, secret, algorithm="HS256"
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": int(TOKEN_LIFETIME.total_seconds()),
    }


def authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: DB,
) -> User:
    if credentials is None:
        raise unauthorized()
    secret = signing_key()
    try:
        claims = jwt.decode(
            credentials.credentials, secret, algorithms=["HS256"], issuer=TOKEN_ISSUER,
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.InvalidTokenError:
        raise unauthorized() from None
    user = db.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise unauthorized()
    return user
