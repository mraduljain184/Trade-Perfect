"""Admin: users, system configuration, holidays (endpoints 46 PUT, 48-50).

Every change is audited, and anything the engine reads (config, holidays) clears the engine
adapter's cache so the next trade is scored with the new values.
"""
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_password
from app.engine import EngineConfig, EngineError
from app.models import Holiday, Role, SystemConfig, User
from app.schemas.admin import AdminUserOut, ConfigItemOut, HolidayCreate, HolidayOut, UserCreate, UserUpdate
from app.schemas.ml import HybridConfigIn
from app.services import audit_service, config_service, engine_adapter

logger = logging.getLogger("app.admin")

_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
# keys that go to the engine: checked by building an EngineConfig from the would-be values
ENGINE_KEYS = set(engine_adapter.ENGINE_CONFIG_KEYS)


# ------------------------------------------------------------------ users (48)
def _user_out(user: User, role: Role) -> AdminUserOut:
    return AdminUserOut(id=user.id, username=user.username, full_name=user.full_name, email=user.email, role=role.code,
                        role_name=role.name, is_active=user.is_active, created_at=user.created_at,
                        last_login_at=user.last_login_at)


def _role(db: Session, code: str) -> Role:
    role = db.scalar(select(Role).where(Role.code == code))
    if role is None:
        raise BadRequestError(f"Unknown role '{code}'")
    return role


def list_users(db: Session) -> list[AdminUserOut]:
    rows = db.execute(select(User, Role).join(Role, Role.id == User.role_id).order_by(User.username)).all()
    return [_user_out(user, role) for user, role in rows]


def _check_unique(db: Session, username: str | None, email: str | None, ignore_id: int | None = None) -> None:
    if username and db.scalar(select(User.id).where(func.lower(User.username) == username, User.id != (ignore_id or 0))):
        raise ConflictError(f"Username '{username}' is already taken")
    if email and db.scalar(select(User.id).where(func.lower(User.email) == email, User.id != (ignore_id or 0))):
        raise ConflictError(f"E-mail '{email}' is already used by another user")


def create_user(db: Session, body: UserCreate, actor: User, ip_address: str | None) -> AdminUserOut:
    role = _role(db, body.role)
    _check_unique(db, body.username, body.email)
    user = User(username=body.username, full_name=body.full_name, email=body.email,
                password_hash=hash_password(body.password), role_id=role.id, is_active=body.is_active)
    db.add(user)
    db.flush()
    db.refresh(user)
    audit_service.log(db, "USER_CREATED", "USER", actor=actor, entity_id=user.id, ip_address=ip_address,
                      after_state={"username": user.username, "fullName": user.full_name, "email": user.email,
                                   "role": role.code, "isActive": user.is_active})
    db.commit()
    logger.info("User %s (%s) created by %s", user.username, role.code, actor.username)
    return _user_out(user, role)


def update_user(db: Session, user_id: int, body: UserUpdate, actor: User, ip_address: str | None) -> AdminUserOut:
    row = db.execute(select(User, Role).join(Role, Role.id == User.role_id).where(User.id == user_id)).first()
    if row is None:
        raise NotFoundError(f"User {user_id} not found")
    user, role = row
    changes = body.model_dump(exclude_unset=True)
    before = {"fullName": user.full_name, "email": user.email, "role": role.code, "isActive": user.is_active}
    new_role = role
    if changes.get("role") and changes["role"] != role.code:
        if "ROLE_ASSIGN" not in actor.permission_codes:
            raise ForbiddenError("Permission required: ROLE_ASSIGN")
        new_role = _role(db, changes["role"])
    if user.id == actor.id and (changes.get("is_active") is False or new_role.id != role.id):
        raise ConflictError("You cannot deactivate yourself or change your own role")
    if changes.get("email"):
        _check_unique(db, None, changes["email"], ignore_id=user.id)
        user.email = changes["email"]
    if changes.get("full_name"):
        user.full_name = changes["full_name"]
    if changes.get("is_active") is not None:
        user.is_active = changes["is_active"]
    if changes.get("password"):
        user.password_hash = hash_password(changes["password"])
    user.role_id, user.updated_at = new_role.id, datetime.now(timezone.utc)
    after = {"fullName": user.full_name, "email": user.email, "role": new_role.code, "isActive": user.is_active}
    changed = sorted(k for k in after if after[k] != before[k]) + (["password"] if changes.get("password") else [])
    if not changed:
        raise BadRequestError("Nothing was changed")
    entries = [dict(action="USER_UPDATED", entity_type="USER", actor=actor, entity_id=user.id, ip_address=ip_address,
                    before_state=before, after_state=after, details={"username": user.username, "changed": changed})]
    if new_role.id != role.id:
        entries.append(dict(action="ROLE_CHANGED", entity_type="USER", actor=actor, entity_id=user.id,
                            before_state={"role": role.code}, after_state={"role": new_role.code},
                            details={"username": user.username}))
    audit_service.log_many(db, entries)
    db.commit()
    logger.info("User %s updated by %s (%s)", user.username, actor.username, ", ".join(changed))
    return _user_out(user, new_role)


# ------------------------------------------------------------------ system configuration (46 PUT, 49)
def _config_out(row: SystemConfig) -> ConfigItemOut:
    return ConfigItemOut(key=row.key, value=row.value, description=row.description, editable_by=row.editable_by,
                         updated_at=row.updated_at)


def list_config(db: Session) -> list[ConfigItemOut]:
    return [_config_out(row) for row in db.scalars(select(SystemConfig).order_by(SystemConfig.key))]


def get_config(db: Session, key: str) -> ConfigItemOut:
    row = db.get(SystemConfig, key)
    if row is None:
        raise NotFoundError(f"Configuration key '{key}' not found")
    return _config_out(row)


def _whole_number(value: Any, low: int, high: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BadRequestError(f"{name} must be a whole number between {low:,} and {high:,}")


def _validate(db: Session, key: str, value: Any) -> None:
    """Refuse values that would break scoring, uploads or drift checks."""
    if key in ENGINE_KEYS:
        if value is None:
            raise BadRequestError(f"{key} cannot be empty")
        rows = {k: v for k, v in db.execute(select(SystemConfig.key, SystemConfig.value)).all() if k in ENGINE_KEYS}
        rows[key] = value
        try:
            EngineConfig.from_system_config(rows)
        except (EngineError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise BadRequestError(f"Invalid value for {key}: {exc}") from exc
        if key == "settlement_far_days":
            _whole_number(value, 1, 365, key)
    elif key == "business_date_override":
        if value is not None:
            try:
                if not isinstance(value, str):
                    raise ValueError("not text")
                date.fromisoformat(value)
            except ValueError:
                raise BadRequestError("business_date_override must be a date like 2026-09-28, or null for the real date") from None
    elif key == "batch_size":
        _whole_number(value, 1, 10_000, key)
    elif key == "max_upload_rows":
        _whole_number(value, 1, 1_000_000, key)
    elif key == "drift_window_days":
        _whole_number(value, 1, 365, key)
    elif key == "psi_threshold":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise BadRequestError("psi_threshold must be a number above 0 and at most 1")
    elif key.endswith("_recipients"):
        if not isinstance(value, list) or any(not isinstance(a, str) or not _EMAIL.match(a.strip()) for a in value):
            raise BadRequestError(f"{key} must be a list of e-mail addresses")


def set_config(db: Session, key: str, value: Any, actor: User, ip_address: str | None, commit: bool = True) -> ConfigItemOut:
    row = db.get(SystemConfig, key)
    if row is None:
        raise NotFoundError(f"Configuration key '{key}' not found")
    role = actor.role.code
    if role != "SYS_ADMIN" and role != row.editable_by:
        raise ForbiddenError(f"'{key}' can only be changed by {row.editable_by} or SYS_ADMIN")
    _validate(db, key, value)
    before = row.value
    row.value, row.updated_by, row.updated_at = value, actor.id, datetime.now(timezone.utc)
    audit_service.log(db, "CONFIG_CHANGED", "CONFIG", actor=actor, entity_id=key, ip_address=ip_address,
                      before_state={"value": before}, after_state={"value": value})
    if commit:
        db.commit()
        engine_adapter.invalidate_cache()
        logger.info("Config %s changed by %s", key, actor.username)
    return _config_out(row)


def update_hybrid_config(db: Session, body: HybridConfigIn, actor: User, ip_address: str | None) -> dict[str, float]:
    """PUT /ml/config: alpha + beta must be 1 (within 0.001) and 0 < pass < exception < 1."""
    current = config_service.get_hybrid_config(db)
    new = {**current, **body.model_dump(exclude_none=True)}
    if body.alpha is not None and body.beta is None:
        new["beta"] = round(1 - body.alpha, 6)        # moving one slider moves the other
    elif body.beta is not None and body.alpha is None:
        new["alpha"] = round(1 - body.beta, 6)
    if abs(new["alpha"] + new["beta"] - 1) > 0.001:
        raise BadRequestError(f"alpha + beta must equal 1 (got {new['alpha']} + {new['beta']})")
    new["beta"] = round(1 - new["alpha"], 6)          # store an exact pair
    if not 0 < new["pass_threshold"] < new["exception_threshold"] < 1:
        raise BadRequestError("Need 0 < passThreshold < exceptionThreshold < 1")
    wanted = {
        "hybrid_weights": {"alpha": new["alpha"], "beta": new["beta"]},
        "classification_thresholds": {"pass": new["pass_threshold"], "exception": new["exception_threshold"]},
        "psi_threshold": new["psi_threshold"],
    }
    changed = False
    for key, value in wanted.items():
        if db.get(SystemConfig, key).value != value:
            set_config(db, key, value, actor, ip_address, commit=False)
            changed = True
    if not changed:
        raise BadRequestError("Nothing was changed")
    db.commit()
    engine_adapter.invalidate_cache()
    logger.info("Hybrid config changed by %s: %s", actor.username, wanted)
    return config_service.get_hybrid_config(db)


# ------------------------------------------------------------------ holidays (50)
def _holiday_out(row: Holiday) -> HolidayOut:
    return HolidayOut(date=row.holiday_date, market=row.market, description=row.description)


def list_holidays(db: Session) -> list[HolidayOut]:
    return [_holiday_out(row) for row in db.scalars(select(Holiday).order_by(Holiday.holiday_date, Holiday.market))]


def add_holiday(db: Session, body: HolidayCreate, actor: User, ip_address: str | None) -> HolidayOut:
    if db.get(Holiday, (body.date, body.market)) is not None:
        raise ConflictError(f"{body.date} is already a holiday for {body.market}")
    row = Holiday(holiday_date=body.date, market=body.market, description=body.description)
    db.add(row)
    audit_service.log(db, "CONFIG_CHANGED", "CONFIG", actor=actor, entity_id=f"holiday:{body.date}", ip_address=ip_address,
                      after_state={"date": body.date, "market": body.market, "description": body.description})
    db.commit()
    engine_adapter.invalidate_cache()
    return _holiday_out(row)


def delete_holiday(db: Session, day: date, market: str, actor: User, ip_address: str | None) -> None:
    row = db.get(Holiday, (day, market.strip().upper()))
    if row is None:
        raise NotFoundError(f"{day} is not a holiday for {market}")
    audit_service.log(db, "CONFIG_CHANGED", "CONFIG", actor=actor, entity_id=f"holiday:{day}", ip_address=ip_address,
                      before_state={"date": day, "market": row.market, "description": row.description})
    db.delete(row)
    db.commit()
    engine_adapter.invalidate_cache()
