"""Load demo/reference data into the database.  Safe to run many times (no duplicates).

    python scripts/seed.py              # add whatever is missing, refresh tiers/stats
    python scripts/seed.py --if-empty   # do nothing if the DB was already seeded (used at container start)
    python scripts/seed.py --reset      # WIPE ALL DATA (trades, queue, audit, users...) and seed again

Run from the backend/ folder, after `alembic upgrade head`.
With Docker:  docker compose exec backend python scripts/seed.py
"""
import argparse
import csv
import logging
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import delete, func, select, text, update  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import (  # noqa: E402
    Account, Counterparty, HistoricalTrade, Holiday, Permission, Role, RolePermission, Symbol, SystemConfig, User,
)
from scripts import seed_data  # noqa: E402

logger = logging.getLogger("seed")

HISTORICAL_FILE = "Historical_Trade_Dataset_50K.csv"
ALL_TABLES = [
    "chat_messages", "email_outbox", "notifications", "system_config", "audit_logs", "repair_queue",
    "drift_checks", "retraining_events", "model_evaluations", "ml_predictions", "rule_evaluations",
    "trade_versions", "trades", "model_versions", "upload_batches", "historical_trades", "holidays",
    "accounts", "symbols", "counterparties", "users", "role_permissions", "permissions", "roles",
]


# ---------------------------------------------------------------- file helpers
def resolve_data_dir(configured: str) -> Path:
    candidates = [Path(configured), BACKEND_DIR / configured, BACKEND_DIR.parent / configured, BACKEND_DIR.parent / "data"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise SystemExit(f"ERROR: data folder not found. Tried: {', '.join(str(c) for c in candidates)}. Set DATA_DIR in .env.")


def find_file(data_dir: Path, file_name: str, required: bool = True) -> Path | None:
    """Find a file anywhere under data_dir (so data/x.csv and data/Ref_Data/x.csv both work)."""
    matches = sorted(p for p in data_dir.rglob("*") if p.is_file() and p.name.lower() == file_name.lower())
    if matches:
        return matches[0]
    if required:
        raise SystemExit(f"ERROR: {file_name} not found anywhere under {data_dir}")
    return None


def read_csv(path: Path) -> list[dict[str, str]]:
    """utf-8-sig strips the BOM the reference files start with; cells and headers are trimmed."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = []
        for raw in csv.DictReader(handle):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            if any(row.values()):
                rows.append(row)
        return rows


def parse_date(value: str) -> date:
    value = value.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"bad date {value!r}")


def parse_bool(value: str) -> bool:
    value = value.strip().upper()
    if value in ("YES", "Y", "TRUE", "1"):
        return True
    if value in ("NO", "N", "FALSE", "0"):
        return False
    raise ValueError(f"bad boolean {value!r}")


# ---------------------------------------------------------------- seed steps
def reset_database(db: Session) -> None:
    logger.warning("--reset: deleting ALL data")
    # audit_logs is protected by triggers; switch them off just for this wipe.
    db.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER USER"))
    db.execute(text(f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE"))
    db.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER USER"))
    db.execute(text("ALTER SEQUENCE trade_id_seq RESTART"))
    db.execute(text("ALTER SEQUENCE repair_queue_seq RESTART"))


def seed_roles(db: Session, data_dir: Path) -> None:
    from_file = {}
    path = find_file(data_dir, "UserRoles.csv", required=False)
    if path:
        for row in read_csv(path):
            from_file[int(row["Id"])] = (row["Role"], row["Primary Function"], row["Key Access"])
    else:
        logger.warning("UserRoles.csv not found - using built-in role names")
    for role_id, code in seed_data.ROLE_CODES.items():
        name, function, access = from_file.get(role_id, seed_data.ROLE_FALLBACK[role_id])
        values = {"code": code, "name": name, "primary_function": function, "key_access": access}
        db.execute(pg_insert(Role).values(id=role_id, **values).on_conflict_do_update(index_elements=["id"], set_=values))


def seed_permissions(db: Session) -> None:
    for code, description, _ in seed_data.PERMISSIONS:
        db.execute(pg_insert(Permission).values(code=code, description=description)
                   .on_conflict_do_update(index_elements=["code"], set_={"description": description}))
    ids = dict(db.execute(select(Permission.code, Permission.id)).all())
    # The matrix in seed_data.py is the source of truth: rebuild the links every run.
    db.execute(delete(RolePermission))
    db.execute(pg_insert(RolePermission), [
        {"role_id": role_id, "permission_id": ids[code], "scope": scope}
        for code, _, grants in seed_data.PERMISSIONS for role_id, scope in grants.items()
    ])


def seed_users(db: Session, password: str, email_domain: str) -> int:
    wanted = [(name.lower().replace(" ", "."), name, seed_data.T) for name in seed_data.TRADER_NAMES]
    wanted += seed_data.STAFF_USERS
    existing = set(db.scalars(select(User.username)).all())
    created = 0
    for username, full_name, role_id in wanted:
        if username in existing:
            continue  # never overwrite an existing user's password
        db.add(User(username=username, full_name=full_name, email=f"{username}@{email_domain}",
                    password_hash=hash_password(password), role_id=role_id))
        created += 1
    db.flush()
    return created


def seed_reference(db: Session, data_dir: Path) -> None:
    symbols = [{"symbol": r["Symbol"]} for r in read_csv(find_file(data_dir, "Symbol_DataSet.csv")) if r.get("Symbol")]
    parties = [{"name": r["Counterparty"]} for r in read_csv(find_file(data_dir, "Counterparty_Dataset.csv")) if r.get("Counterparty")]
    accounts = [{"account_id": r["Account ID"], "is_active": parse_bool(r["Active"])}
                for r in read_csv(find_file(data_dir, "Account_MasterData.csv")) if r.get("Account ID")]
    db.execute(pg_insert(Symbol).on_conflict_do_nothing(index_elements=["symbol"]), symbols)
    db.execute(pg_insert(Counterparty).on_conflict_do_nothing(index_elements=["name"]), parties)
    db.execute(pg_insert(Account).on_conflict_do_nothing(index_elements=["account_id"]), accounts)
    logger.info("reference files: %d symbols, %d counterparties, %d accounts", len(symbols), len(parties), len(accounts))


def seed_historical(db: Session, path: Path) -> None:
    if db.scalar(select(func.count()).select_from(HistoricalTrade)):
        logger.info("historical_trades already loaded - skipped")
        return
    started = time.perf_counter()
    loaded, skipped, seen = 0, 0, set()
    columns = ("trade_id, trade_type, symbol, quantity, price, counterparty, trade_date, settlement_date, "
               "account_id, ssi_matched, currency_matched, trader_name, settlement_failed")
    cursor = db.connection().connection.driver_connection.cursor()
    with path.open(newline="", encoding="utf-8-sig") as handle, \
            cursor.copy(f"COPY historical_trades ({columns}) FROM STDIN") as copy:
        for line_no, raw in enumerate(csv.DictReader(handle), start=2):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            try:
                trade_id = row["Trade ID"]
                if not trade_id or trade_id in seen:
                    raise ValueError("empty or duplicate Trade ID")
                record = (
                    trade_id, row["Trade Type"].upper(), row["Symbol"], int(row["Quantity"]), row["Price"],
                    row["Counterparty"], parse_date(row["Trade Date"]), parse_date(row["Settlement Date"]),
                    row["Account ID"], parse_bool(row["SSI Matched"]), parse_bool(row["Currency Matched"]),
                    row["Trader Name"], parse_bool(row["Settlement Failed"]),
                )
                if record[1] not in ("BUY", "SELL"):
                    raise ValueError(f"bad trade type {record[1]!r}")
                float(record[4])
            except (KeyError, ValueError) as exc:
                skipped += 1
                if skipped <= 10:
                    logger.warning("historical line %d skipped: %s", line_no, exc)
                continue
            seen.add(trade_id)
            copy.write_row(record)
            loaded += 1
    cursor.close()
    logger.info("historical_trades: %d rows loaded, %d skipped, %.1f s", loaded, skipped, time.perf_counter() - started)


def seed_config(db: Session) -> None:
    rows = [{"key": key, "value": value, "description": description, "editable_by": editable_by}
            for key, (value, description, editable_by) in seed_data.SYSTEM_CONFIG.items()]
    # Never overwrite a value an admin has already changed.
    db.execute(pg_insert(SystemConfig).on_conflict_do_nothing(index_elements=["key"]), rows)
    holidays = [{"holiday_date": date.fromisoformat(d), "market": market, "description": description}
                for d, market, description in seed_data.HOLIDAYS]
    db.execute(pg_insert(Holiday).on_conflict_do_nothing(index_elements=["holiday_date", "market"]), holidays)


def _as_dict(value) -> dict:
    return asdict(value) if is_dataclass(value) else dict(value)


def compute_stats(db: Session, historical_path: Path) -> str:
    """Counterparty tiers + symbol stats, computed by the ML engine (section 8.3)."""
    from app.engine import compute_counterparty_tiers, compute_symbol_stats, load_historical_csv

    thresholds = db.get(SystemConfig, "counterparty_tier_thresholds").value
    source = "engine"
    historical = load_historical_csv(str(historical_path))
    tiers = compute_counterparty_tiers(historical, thresholds)
    stats = compute_symbol_stats(historical)

    now = datetime.now(timezone.utc)
    tiers_by_name = {name.strip().lower(): _as_dict(info) for name, info in tiers.items()}
    for party in db.scalars(select(Counterparty)).all():
        info = tiers_by_name.get(party.name.lower())
        if info is None:  # no history for this counterparty -> lowest risk tier
            info = {"trade_count": 0, "fail_count": 0, "fail_rate": 0.0, "risk_tier": 1, "tier_label": "Tier1_HighTrust"}
        party.historical_trade_count = int(info["trade_count"])
        party.historical_fail_count = int(info["fail_count"])
        party.historical_fail_rate = round(float(info["fail_rate"]), 4)
        party.risk_tier = int(info["risk_tier"])
        party.tier_label = info["tier_label"]
        party.tier_updated_at = now

    stats_by_symbol = {symbol.strip().upper(): _as_dict(info) for symbol, info in stats.items()}
    for symbol in db.scalars(select(Symbol)).all():
        info = stats_by_symbol.get(symbol.symbol.upper())
        if info is None:
            continue
        symbol.price_mean = round(float(info["price_mean"]), 4)
        symbol.price_std = round(float(info["price_std"]), 4)
        symbol.qty_mean = round(float(info["qty_mean"]), 4)
        symbol.qty_std = round(float(info["qty_std"]), 4)
        symbol.historical_fail_rate = round(float(info["fail_rate"]), 4)
    db.flush()
    logger.info("stats source: %s", source)
    return source


def run_seed(db: Session, *, reset: bool = False, if_empty: bool = False,
             data_dir: str | None = None, password: str | None = None) -> bool:
    """Returns False if skipped because of --if-empty. Commits on success."""
    settings = get_settings()
    if if_empty and not reset and db.scalar(select(func.count()).select_from(Role)):
        logger.info("database already seeded - nothing to do (--if-empty)")
        return False
    password = password if password is not None else settings.seed_default_password
    if len(password) < 8:
        raise SystemExit("ERROR: SEED_DEFAULT_PASSWORD is missing or shorter than 8 characters. Set it in .env.")
    folder = resolve_data_dir(data_dir or settings.data_dir)
    historical_path = find_file(folder, HISTORICAL_FILE)
    logger.info("data folder: %s", folder)

    if reset:
        reset_database(db)
    seed_roles(db, folder)
    seed_permissions(db)
    created = seed_users(db, password, settings.seed_email_domain)
    seed_reference(db, folder)
    seed_historical(db, historical_path)
    seed_config(db)
    compute_stats(db, historical_path)
    db.commit()

    tier_counts = dict(db.execute(select(Counterparty.risk_tier, func.count()).group_by(Counterparty.risk_tier)).all())
    logger.info("users created this run: %d (total %d)", created, db.scalar(select(func.count()).select_from(User)))
    logger.info("counterparty tiers: " + ", ".join(f"Tier{t}={tier_counts.get(t, 0)}" for t in (4, 3, 2, 1)))
    logger.info("SEED COMPLETE")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Trade Perfect database")
    parser.add_argument("--reset", action="store_true", help="delete ALL data first")
    parser.add_argument("--if-empty", action="store_true", help="skip if the database is already seeded")
    parser.add_argument("--data-dir", help="folder with the CSV files (default: DATA_DIR from .env)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s seed: %(message)s")

    from app.core.database import SessionLocal

    with SessionLocal() as db:
        try:
            run_seed(db, reset=args.reset, if_empty=args.if_empty, data_dir=args.data_dir)
        except Exception:
            db.rollback()
            raise


if __name__ == "__main__":
    main()
