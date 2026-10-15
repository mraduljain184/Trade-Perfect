"""Reference data built from history + master files.

- load_historical_csv        : 50K history -> typed canonical DataFrame
- compute_counterparty_tiers : historical fail counts -> risk tier 1..4 (PROJECT_CONTEXT §2.3)
- compute_symbol_stats       : per-symbol price/quantity mean+std and fail rate
- load_reference_data        : build ReferenceData from the CSV files (CLI / tests / notebooks).
                               The BACKEND builds ReferenceData from the DB instead.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Iterable, Optional

import pandas as pd

from .config import DEFAULT_TIER_THRESHOLDS, TIER_LABELS
from .errors import EngineError
from .normalize import CANONICAL_COLUMNS, LABEL_COLUMN, normalize_records, read_trade_csv
from .types import CounterpartyRef, ReferenceData, SymbolStats

log = logging.getLogger(__name__)

# file names inside data/Ref_Data/
SYMBOL_FILE = "Symbol_DataSet.csv"
COUNTERPARTY_FILE = "Counterparty_Dataset.csv"
ACCOUNT_FILE = "Account_MasterData.csv"


# ---- historical data --------------------------------------------------------
def load_historical_csv(path: str) -> pd.DataFrame:
    """Read the labelled history. Returns canonical snake_case columns:
    trade_id, trade_type, symbol, quantity, price, counterparty, trade_date (date),
    settlement_date (date), account_id, ssi_matched (bool), currency_matched (bool),
    trader_name, settlement_failed (bool).
    Rows that cannot be parsed or miss a required value are dropped (and logged)."""
    batch = normalize_records(read_trade_csv(path))
    df = batch.df
    if LABEL_COLUMN not in df.columns:
        raise EngineError(f"{path}: column 'Settlement Failed' not found - not a historical file")

    required = [c for c in CANONICAL_COLUMNS if c != "trader_name"] + [LABEL_COLUMN]
    has_parse_error = pd.Series([bool(e) for e in batch.parse_errors], index=df.index)
    ok = df[required].notna().all(axis=1) & ~has_parse_error
    dropped = int((~ok).sum())
    if dropped:
        log.warning("load_historical_csv: dropped %d invalid rows from %s", dropped, path)
    df = df[ok].reset_index(drop=True)

    df["quantity"] = df["quantity"].astype("int64")
    df["price"] = df["price"].astype("float64")
    for col in ("ssi_matched", "currency_matched", LABEL_COLUMN):
        df[col] = df[col].astype(bool)
    return df


# ---- counterparty risk tiers -------------------------------------------------
def tier_for_fail_count(fail_count: int, thresholds: Optional[dict] = None) -> int:
    """>tier4 -> 4 · [tier3, tier4] -> 3 · [tier2, tier3) -> 2 · <tier2 -> 1.
    Defaults: >1500 T4, 1000-1500 T3, 500-999 T2, <500 T1 (decision-logic doc)."""
    t = {**DEFAULT_TIER_THRESHOLDS, **(thresholds or {})}
    if fail_count > t["tier4"]:
        return 4
    if fail_count >= t["tier3"]:
        return 3
    if fail_count >= t["tier2"]:
        return 2
    return 1


def compute_counterparty_tiers(historical: pd.DataFrame, thresholds: Optional[dict] = None) -> dict:
    """name -> {"fail_count","trade_count","fail_rate","risk_tier","tier_label"} (sorted by fail_count desc)."""
    g = historical.groupby("counterparty")[LABEL_COLUMN].agg(fail_count="sum", trade_count="count")
    g = g.sort_values(["fail_count", "trade_count"], ascending=False)
    out: dict[str, dict] = {}
    for name, row in g.iterrows():
        fails, trades = int(row.fail_count), int(row.trade_count)
        tier = tier_for_fail_count(fails, thresholds)
        out[str(name)] = {
            "fail_count": fails,
            "trade_count": trades,
            "fail_rate": round(fails / trades, 4) if trades else 0.0,
            "risk_tier": tier,
            "tier_label": TIER_LABELS[tier],
        }
    return out


def build_counterparty_refs(master_names: Iterable[str], tiers: dict) -> dict[str, CounterpartyRef]:
    """Master list + computed tiers -> {name.lower(): CounterpartyRef}.
    A master counterparty with no history gets Tier 1 and zero counts."""
    refs: dict[str, CounterpartyRef] = {}
    for name in master_names:
        name = name.strip()
        t = tiers.get(name, {"fail_count": 0, "trade_count": 0, "fail_rate": 0.0,
                             "risk_tier": 1, "tier_label": TIER_LABELS[1]})
        refs[name.lower()] = CounterpartyRef(
            name=name, risk_tier=t["risk_tier"], tier_label=t["tier_label"],
            fail_count=t["fail_count"], trade_count=t["trade_count"], fail_rate=t["fail_rate"],
        )
    return refs


# ---- symbol statistics ------------------------------------------------------
def compute_symbol_stats(historical: pd.DataFrame) -> dict[str, SymbolStats]:
    """Per symbol: price mean/std, quantity mean/std, raw fail rate.
    std is sample std (ddof=1); a missing/zero std is stored as 1.0 so z-scores never divide by 0."""
    g = historical.groupby("symbol").agg(
        price_mean=("price", "mean"), price_std=("price", "std"),
        qty_mean=("quantity", "mean"), qty_std=("quantity", "std"),
        fail_rate=(LABEL_COLUMN, "mean"),
    )

    def safe_std(v: float) -> float:
        return float(v) if pd.notna(v) and v > 0 else 1.0

    return {
        str(sym): SymbolStats(
            price_mean=round(float(r.price_mean), 4), price_std=round(safe_std(r.price_std), 4),
            qty_mean=round(float(r.qty_mean), 4), qty_std=round(safe_std(r.qty_std), 4),
            fail_rate=round(float(r.fail_rate), 4),
        )
        for sym, r in g.iterrows()
    }

# ---- master files -> ReferenceData (CLI / tests only) ----------------------------
def _read_single_column(path: str) -> list[str]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return [v.strip() for v in df.iloc[:, 0] if v.strip()]


def load_reference_data(
    ref_dir: str,
    historical: Optional[pd.DataFrame] = None,
    holidays: Iterable[date] = (),
    tier_thresholds: Optional[dict] = None,
) -> ReferenceData:
    """Build ReferenceData from data/Ref_Data/*.csv (+ history for tiers and symbol stats)."""
    symbols = {s.upper() for s in _read_single_column(os.path.join(ref_dir, SYMBOL_FILE))}
    cp_names = _read_single_column(os.path.join(ref_dir, COUNTERPARTY_FILE))

    acc = pd.read_csv(os.path.join(ref_dir, ACCOUNT_FILE), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    acc.columns = [c.strip().lower().replace(" ", "_") for c in acc.columns]   # account_id, active
    accounts = {
        a.strip().upper(): act.strip().upper() in {"Y", "YES", "TRUE", "1"}
        for a, act in zip(acc["account_id"], acc["active"]) if a.strip()
    }

    if historical is not None:
        tiers = compute_counterparty_tiers(historical, tier_thresholds)
        symbol_stats = compute_symbol_stats(historical)
    else:
        tiers, symbol_stats = {}, {}

    return ReferenceData(
        symbols=symbols,
        counterparties=build_counterparty_refs(cp_names, tiers),
        accounts=accounts,
        symbol_stats=symbol_stats,
        holidays=set(holidays),
    )