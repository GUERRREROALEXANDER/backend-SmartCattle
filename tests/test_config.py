import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in ("ALLOWED_ORIGINS", "SMARTCATTLE_AI_URL", "AI_API_KEY", "DATABASE_URL", "CAMERA_OFFLINE_AFTER_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults():
    settings = Settings()
    assert settings.cors_origins == [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
        "http://localhost:3000", "http://127.0.0.1:5500",
    ]
    assert settings.smartcattle_ai_url is None
    assert settings.ai_api_key is None


def test_origins_from_environment(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", " http://localhost:3000 , , http://localhost:5173, ")
    assert Settings().cors_origins == ["http://localhost:3000", "http://localhost:5173"]


def test_trailing_slashes_are_removed():
    assert Settings(allowed_origins=" https://example.com/ , http://localhost:5173/// ").cors_origins == [
        "https://example.com", "http://localhost:5173",
    ]


@pytest.mark.parametrize("origin", ["localhost:5173", "ftp://example.com"])
def test_invalid_origin_scheme(origin):
    with pytest.raises(ValidationError, match="CORS origins must start with http:// or https://"):
        Settings(allowed_origins=origin)


@pytest.mark.parametrize("origins", ["*", "http://localhost:3000, * "])
def test_wildcards_are_rejected(origins):
    with pytest.raises(ValidationError, match="Wildcard CORS origins are not allowed"):
        Settings(allowed_origins=origins)


def test_empty_values_become_none(monkeypatch):
    monkeypatch.setenv("SMARTCATTLE_AI_URL", "")
    monkeypatch.setenv("AI_API_KEY", "")
    settings = Settings()
    assert settings.smartcattle_ai_url is None
    assert settings.ai_api_key is None


def test_dotenv_and_environment_precedence(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "SMARTCATTLE_AI_URL=https://smartcattle-ai.example.com\nAI_API_KEY=dotenv-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_API_KEY", "environment-key")
    settings = Settings()
    assert str(settings.smartcattle_ai_url) == "https://smartcattle-ai.example.com/"
    assert settings.ai_api_key.get_secret_value() == "environment-key"
    assert "environment-key" not in repr(settings)


def test_settings_are_cached():
    assert get_settings() is get_settings()
