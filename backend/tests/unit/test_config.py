import pytest
from pydantic import ValidationError

from app.core.config import Settings

SECRET = "s" * 40


@pytest.mark.parametrize("given", ["postgres://u:p@h:5432/d", "postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"])
def test_database_url_always_uses_psycopg3(given):
    assert Settings(database_url=given, jwt_secret=SECRET).database_url == "postgresql+psycopg://u:p@h:5432/d"


def test_short_jwt_secret_rejected():
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://u:p@h/d", jwt_secret="too-short")


def test_cors_origins_split():
    settings = Settings(database_url="postgresql://u:p@h/d", jwt_secret=SECRET, cors_origins="http://a:1, http://b:2 ,")
    assert settings.cors_origin_list == ["http://a:1", "http://b:2"]
