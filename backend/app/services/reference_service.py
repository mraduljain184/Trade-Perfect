"""Read-only lookups for dropdowns (endpoints 4-7)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BadRequestError
from app.models import Account, Counterparty, Role, Symbol, User


def list_symbols(db: Session) -> list[Symbol]:
    return list(db.scalars(select(Symbol).where(Symbol.is_active.is_(True)).order_by(Symbol.symbol)))


def list_counterparties(db: Session) -> list[Counterparty]:
    return list(db.scalars(select(Counterparty).where(Counterparty.is_active.is_(True)).order_by(Counterparty.name)))


def list_accounts(db: Session, active: bool | None) -> list[Account]:
    stmt = select(Account).order_by(Account.account_id)
    if active is not None:
        stmt = stmt.where(Account.is_active.is_(active))
    return list(db.scalars(stmt))


def list_users(db: Session, role_code: str | None) -> list[dict]:
    stmt = (select(User.id, User.username, User.full_name, Role.code.label("role"))
            .join(Role, Role.id == User.role_id).where(User.is_active.is_(True)).order_by(User.full_name))
    if role_code:
        role_code = role_code.strip().upper()
        if db.scalar(select(Role.id).where(Role.code == role_code)) is None:
            raise BadRequestError(f"Unknown role '{role_code}'")
        stmt = stmt.where(Role.code == role_code)
    return [dict(row._mapping) for row in db.execute(stmt)]
