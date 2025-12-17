import pytest

from src.backend_common.config.app_config import AppConfig, AppConfigError, log_secret


def _set_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("secret,expected", [("token", "****"), ("", "None"), (None, "None")])
def test_log_secret_masks_values(secret: str | None, expected: str) -> None:
    assert log_secret(secret) == expected


def test_from_env_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(
        monkeypatch,
        CLIENT_URL="http://client",
        HMAC_SECRET="secret",  # nosec B105
        OPENAI_API_KEY="key",  # nosec B105
        LLM_URL="http://llm",
        DOCLING_URL="http://docling",
        WHISPER_URL="http://whisper",
        OCR_URL="http://ocr",
    )

    config = AppConfig.from_env()

    assert config.client_url == "http://client"
    assert config.hmac_secret == "secret"  # nosec B105
    assert "****" in str(config)


@pytest.mark.parametrize(
    "missing_key",
    ["CLIENT_URL", "HMAC_SECRET", "OPENAI_API_KEY", "LLM_URL", "DOCLING_URL", "WHISPER_URL", "OCR_URL"],
)
def test_from_env_raises_when_missing(monkeypatch: pytest.MonkeyPatch, missing_key: str) -> None:
    env = {
        "CLIENT_URL": "http://client",
        "HMAC_SECRET": "secret",  # nosec B105
        "OPENAI_API_KEY": "key",  # nosec B105
        "LLM_URL": "http://llm",
        "DOCLING_URL": "http://docling",
        "WHISPER_URL": "http://whisper",
        "OCR_URL": "http://ocr",
    }
    _ = env.pop(missing_key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing_key, raising=False)

    with pytest.raises(AppConfigError):
        _ = AppConfig.from_env()
