"""live-ear is the ASR slot name (ops/crew pattern). parakeet is an alias."""

from crissa_ear.config import (
    DEFAULT_LIVE_EAR_URL,
    is_live_ear_engine,
    live_ear_url,
    normalize_engine_name,
)
from crissa_ear.engines import LiveEarEngine, ParakeetEngine
from crissa_ear.engines.live_ear import DEFAULT_LIVE_EAR_URL as ENGINE_DEFAULT


def test_aliases_are_the_same_class():
    assert LiveEarEngine is ParakeetEngine
    assert LiveEarEngine.name == "live-ear"
    assert ENGINE_DEFAULT == "http://127.0.0.1:8096"


def test_engine_name_aliases():
    for n in ("live-ear", "live_ear", "parakeet", "audio8", "audio8-ark"):
        assert is_live_ear_engine(n), n
    assert not is_live_ear_engine("whisper")
    assert not is_live_ear_engine("dual")
    assert normalize_engine_name("live_ear") == "live-ear"


def test_url_prefers_live_ear_then_alias(monkeypatch):
    monkeypatch.delenv("LIVE_EAR_URL", raising=False)
    monkeypatch.delenv("RAIL_ASR_URL", raising=False)
    monkeypatch.delenv("PARAKEET_URL", raising=False)
    assert live_ear_url() == DEFAULT_LIVE_EAR_URL

    monkeypatch.setenv("PARAKEET_URL", "http://127.0.0.1:8092")
    assert live_ear_url() == "http://127.0.0.1:8092"

    monkeypatch.setenv("LIVE_EAR_URL", "http://127.0.0.1:8096")
    assert live_ear_url() == "http://127.0.0.1:8096"


def test_default_is_not_retired_parakeet_port():
    assert DEFAULT_LIVE_EAR_URL.endswith(":8096")
    assert ":8092" not in DEFAULT_LIVE_EAR_URL


def test_config_defaults_to_the_slot(monkeypatch):
    from crissa_ear.config import Config

    monkeypatch.delenv("RAIL_STT_ENGINE", raising=False)
    monkeypatch.delenv("LIVE_EAR_URL", raising=False)
    monkeypatch.delenv("RAIL_ASR_URL", raising=False)
    monkeypatch.delenv("PARAKEET_URL", raising=False)
    cfg = Config()
    assert cfg.engine == "live-ear"
    assert cfg.live_ear_url == DEFAULT_LIVE_EAR_URL
    assert cfg.parakeet_url == cfg.live_ear_url
