"""The permission matrix in seed_data.py must agree with the provided UserRoles_Matrix.csv."""
import pytest

from app.core.config import get_settings
from scripts import seed, seed_data

COLUMN_TO_ROLE = {"Trader": 101, "Operations User": 102, "Operations Manager": 103, "ML Admin": 104, "Sys Admin": 105}
# Section 5.2 "*": we deliberately let Ops see the risk score although the matrix says No.
DOCUMENTED_DEVIATIONS = {"View AI Risk Score": {102, 103}}


def test_permission_codes_unique_and_complete():
    codes = [code for code, _, _ in seed_data.PERMISSIONS]
    assert len(codes) == len(set(codes)) == 28
    assert {"TRADE_AMEND", "CHATBOT_USE"} <= set(codes)


def test_sys_admin_has_every_permission():
    assert all(105 in grants for _, _, grants in seed_data.PERMISSIONS)


def test_matrix_matches_csv():
    folder = seed.resolve_data_dir(get_settings().data_dir)
    path = seed.find_file(folder, "UserRoles_Matrix.csv", required=False)
    if path is None:
        pytest.skip("UserRoles_Matrix.csv not found")
    ours = {description: set(grants) for _, description, grants in seed_data.PERMISSIONS}
    rows = seed.read_csv(path)
    assert len(rows) == 26
    for row in rows:
        expected = {role for column, role in COLUMN_TO_ROLE.items() if row[column].upper().startswith("YES")}
        expected |= DOCUMENTED_DEVIATIONS.get(row["Permission"], set())
        assert ours[row["Permission"]] == expected, row["Permission"]


def test_twenty_one_seed_users_with_unique_usernames():
    usernames = [n.lower().replace(" ", ".") for n in seed_data.TRADER_NAMES] + [u for u, _, _ in seed_data.STAFF_USERS]
    assert len(usernames) == len(set(usernames)) == 21


def test_date_and_bool_parsing():
    assert seed.parse_date("9/11/2026").isoformat() == "2026-09-11"
    assert seed.parse_date("2026-09-11").isoformat() == "2026-09-11"
    assert seed.parse_bool("YES") is True and seed.parse_bool(" n ") is False
    with pytest.raises(ValueError):
        seed.parse_bool("maybe")
