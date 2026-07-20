"""Authentication and user management service."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from database.models import User, UserRole
from services.audit_service import log_action
from utils.security import hash_password, verify_password
from utils.validators import is_valid_email


class AuthError(Exception):
    pass


def authenticate(db: Session, username: str, password: str) -> User:
    """Validate credentials and return the User row, or raise AuthError."""
    user = db.query(User).filter(User.username == username.strip()).first()
    if not user or not user.is_active:
        log_action(db, "LOGIN_FAILED", username=username, details="Unknown or inactive user")
        raise AuthError("Invalid username or password.")

    if not verify_password(password, user.password_hash):
        log_action(db, "LOGIN_FAILED", user_id=user.id, username=username, details="Bad password")
        raise AuthError("Invalid username or password.")

    user.last_login = datetime.utcnow()
    log_action(db, "LOGIN", user_id=user.id, username=user.username)
    return user


def create_user(
    db: Session,
    username: str,
    email: str,
    password: str,
    role: str,
    created_by: str | None = None,
) -> User:
    if db.query(User).filter(User.username == username).first():
        raise ValueError(f"Username '{username}' already exists.")
    if not is_valid_email(email):
        raise ValueError("Invalid email address.")
    if role not in (UserRole.ADMIN.value, UserRole.OPERATOR.value):
        raise ValueError("Role must be Admin or Operator.")

    user = User(
        username=username.strip(),
        email=email.strip(),
        password_hash=hash_password(password),
        role=UserRole(role),
        is_active=True,
    )
    db.add(user)
    db.flush()
    log_action(db, "CREATE_USER", username=created_by, entity_type="User", entity_id=user.id,
               details=f"Created user {username} ({role})")
    return user


def list_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.username).all()


def set_user_active(db: Session, user_id: int, is_active: bool, changed_by: str | None = None) -> None:
    user = db.query(User).get(user_id)
    if not user:
        raise ValueError("User not found.")
    user.is_active = is_active
    log_action(db, "EDIT_USER", username=changed_by, entity_type="User", entity_id=user_id,
               details=f"Set is_active={is_active}")


def change_password(db: Session, user_id: int, new_password: str, changed_by: str | None = None) -> None:
    user = db.query(User).get(user_id)
    if not user:
        raise ValueError("User not found.")
    user.password_hash = hash_password(new_password)
    log_action(db, "CHANGE_PASSWORD", username=changed_by, entity_type="User", entity_id=user_id)
