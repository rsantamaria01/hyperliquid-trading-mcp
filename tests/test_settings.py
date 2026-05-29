"""Persistent settings tests. SETTINGS_PATH is redirected to a tmp file by the
hermetic_settings fixture, so each test starts from defaults."""

from __future__ import annotations

import pytest

from hyperliquid_trading_mcp import settings


def test_defaults_loaded_when_no_file():
    data = settings.load()
    assert data["live_trading"] is False
    assert data["network"] == "mainnet"
    assert data["max_leverage"] == 10


def test_update_persists_and_coerces_types():
    new = settings.update({"max_leverage": "5", "max_position_pct": "7.5"})
    assert new["max_leverage"] == 5 and isinstance(new["max_leverage"], int)
    assert new["max_position_pct"] == 7.5
    # survives a reload from disk
    assert settings.load()["max_leverage"] == 5


def test_live_trading_coercion_from_string():
    assert settings.update({"live_trading": "true"})["live_trading"] is True
    assert settings.update({"live_trading": "off"})["live_trading"] is False


def test_update_rejects_non_editable_key():
    with pytest.raises(ValueError, match="not editable"):
        settings.update({"secret_key": "nope"})


def test_invalid_type_coercion_raises():
    with pytest.raises(ValueError, match="must be"):
        settings.update({"max_leverage": "not-an-int"})


def test_diff_from_defaults_tracks_changes():
    settings.update({"max_leverage": 3})
    diff = settings.diff_from_defaults()
    assert diff == {"max_leverage": 3}


def test_reset_wipes_overrides():
    settings.update({"max_leverage": 3, "live_trading": True})
    settings.reset()
    assert settings.load()["max_leverage"] == 10
    assert settings.diff_from_defaults() == {}
