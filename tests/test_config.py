from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from binance_monitor.config import Settings
from binance_monitor.healthcheck import is_healthy


def env(**overrides: str) -> dict[str, str]:
    base = {
        "TELEGRAM_BOT_TOKEN": "123456:abcdef",
        "TELEGRAM_CHAT_ID": "-1001234567890",
        "TELEGRAM_TOPIC_ID": "7",
        "ADMIN_IDS": "1, 2,3",
        "PUSHOVER_APP_TOKEN": "tok",
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    base.update(overrides)
    return base


def settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    monkeypatch.chdir(Path(__file__).parent)  # no stray .env
    for key, value in env(**overrides).items():
        monkeypatch.setenv(key, value)
    return Settings()  # type: ignore[call-arg]


def test_defaults_and_admin_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(monkeypatch)
    assert s.admin_ids == [1, 2, 3]
    assert str(s.db_path).replace("\\", "/") == "/data/monitor.db"
    assert s.binance_fapi_url == "https://fapi.binance.com"
    assert s.binance_fstream_url == "wss://fstream.binance.com"
    assert (s.pinned_refresh_seconds, s.reconcile_seconds, s.log_level) == (60, 300, "INFO")
    assert (s.pushover_priority, s.pushover_retry_seconds, s.pushover_expire_seconds) == (
        2,
        60,
        3600,
    )


def test_pushover_priority_and_emergency_timing_are_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert settings(monkeypatch, PUSHOVER_PRIORITY="-1").pushover_priority == -1
    with pytest.raises(ValidationError):
        settings(monkeypatch, PUSHOVER_PRIORITY="3")
    with pytest.raises(ValidationError):
        settings(monkeypatch, PUSHOVER_RETRY_SECONDS="10")  # Pushover minimum is 30
    with pytest.raises(ValidationError):
        settings(monkeypatch, PUSHOVER_EXPIRE_SECONDS="20000")  # Pushover maximum is 10800


def test_bad_encryption_key_fails_with_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="not a valid Fernet key"):
        settings(monkeypatch, ENCRYPTION_KEY="nope")


def test_bad_admin_ids_and_missing_values(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="comma-separated integers"):
        settings(monkeypatch, ADMIN_IDS="1,abc")
    with pytest.raises(ValidationError):
        settings(monkeypatch, ADMIN_IDS="")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_trailing_slash_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(monkeypatch, BINANCE_FAPI_URL="https://x.test/")
    assert s.binance_fapi_url == "https://x.test"


def test_healthcheck_freshness(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    assert is_healthy(hb) is False  # missing
    hb.write_text("1000.0")
    assert is_healthy(hb, now=1030.0) is True
    assert is_healthy(hb, now=1100.0) is False  # stale
    hb.write_text("garbage")
    assert is_healthy(hb, now=1000.0) is False
