import os
from typing import override

from pydantic import BaseModel, Field


class AppConfigError(ValueError):
    """Exception raised for errors in the application configuration."""

    def __init__(self, variable_name: str) -> None:
        super().__init__(f"Configuration variable '{variable_name}' is not set or invalid.")


def get_env_or_throw(env_name: str) -> str:
    value = os.getenv(env_name)
    if value is None:
        raise AppConfigError(env_name)
    return value


def log_secret(secret: str | None) -> str:
    return "****" if secret is not None and len(secret) > 0 else "None"


class AbstractAppConfig(BaseModel):
    """Abstract base class for application configurations."""

    @classmethod
    def from_env(cls) -> "AbstractAppConfig":
        raise NotImplementedError("Subclasses must implement from_env.")

    @override
    def __str__(self) -> str:
        raise NotImplementedError("Subclasses must implement __str__.")


class AppConfig(AbstractAppConfig):
    client_url: str = Field(description="The URL for the client application")
    hmac_secret: str = Field(description="The secret key for HMAC authentication")
    openai_api_key: str = Field(description="The API key for authenticating with OpenAI")
    llm_url: str = Field(description="The URL for the LLM API")
    docling_url: str = Field(description="The URL for the Docling service")
    whisper_url: str = Field(description="The URL for the Whisper API")
    ocr_url: str = Field(description="The URL for the OCR API")

    @classmethod
    @override
    def from_env(cls) -> "AppConfig":
        client_url: str = get_env_or_throw("CLIENT_URL")
        hmac_secret: str = get_env_or_throw("HMAC_SECRET")
        openai_api_key: str = get_env_or_throw("OPENAI_API_KEY")
        llm_url: str = get_env_or_throw("LLM_URL")
        docling_url: str = get_env_or_throw("DOCLING_URL")
        whisper_url: str = get_env_or_throw("WHISPER_URL")
        ocr_url: str = get_env_or_throw("OCR_URL")
        return cls(
            client_url=client_url,
            hmac_secret=hmac_secret,
            openai_api_key=openai_api_key,
            llm_url=llm_url,
            docling_url=docling_url,
            whisper_url=whisper_url,
            ocr_url=ocr_url,
        )

    @override
    def __str__(self) -> str:
        return f"""
        AppConfig(
            client_url={self.client_url},
            hmac_secret={log_secret(self.hmac_secret)},
            openai_api_key={log_secret(self.openai_api_key)},
            llm_url={self.llm_url},
            docling_url={self.docling_url},
            whisper_url={self.whisper_url},
            ocr_url={self.ocr_url},
        )
        """
