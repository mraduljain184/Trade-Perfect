"""Fixtures for engine tests. Real-data fixtures read ../data (override with TP_DATA_DIR)."""
import os
from pathlib import Path

import pytest

from app.engine import EngineConfig, load_historical_csv
from app.engine.reference import load_reference_data

from .helpers import make_refs

DATA_DIR = Path(os.environ.get("TP_DATA_DIR", Path(__file__).resolve().parents[3] / "data"))


@pytest.fixture
def cfg():
    return EngineConfig()


@pytest.fixture
def refs():
    return make_refs()


@pytest.fixture(scope="session")
def history():
    return load_historical_csv(str(DATA_DIR / "Historical_Trade_Dataset_50K.csv"))


@pytest.fixture(scope="session")
def real_refs(history):
    return load_reference_data(str(DATA_DIR / "Ref_Data"), history)


@pytest.fixture(scope="session")
def records_20k():
    from app.engine.normalize import read_trade_csv
    return read_trade_csv(str(DATA_DIR / "Trade_Dataset_20K.csv"))