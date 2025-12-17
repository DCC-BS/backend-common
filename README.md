# backend-common

[![Commit activity](https://img.shields.io/github/commit-activity/m/DCC-BS/backend-common)](https://img.shields.io/github/commit-activity/m/DCC-BS/backend-common)
[![License](https://img.shields.io/github/license/DCC-BS/backend-common)](https://img.shields.io/github/license/DCC-BS/backend-common)

Common utilities and components for backend services developed by the Data Competence Center Basel-Stadt.

## Overview

`backend-common` is a Python library that provides shared functionality for backend services, including:

- **FastAPI Health Probes**: Kubernetes-ready health check endpoints (liveness, readiness, startup)
- **Structured Logging**: Integration with `structlog` for consistent logging across services
- **Configuration Management**: Environment-based configuration with `python-dotenv`
- **DSPy Utilities**: Helpers for DSPy modules, streaming listeners, metrics, and dataset preparation

## Installation

### Basic Installation (uv)

```bash
uv add backend-common
```

### With FastAPI Support

```bash
uv add "backend-common[fastapi]"
```

## Requirements

- Python 3.12 or higher
- Dependencies:
  - `dspy>=3.0.4`
  - `python-dotenv>=1.0.1`
  - `structlog>=25.1.0`

### Optional Dependencies

- FastAPI extras: `aiohttp>=3.13.2`, `fastapi>=0.115,<1.0`

## Features

### FastAPI Health Probes

The library provides Kubernetes-ready health check endpoints that follow best practices for container orchestration:

#### Example Usage

```python
from fastapi import FastAPI
from backend_common.fastapi_health_probes import health_probe_router

app = FastAPI()

# Define external service dependencies
service_dependencies = [
    {
        "name": "database",
        "health_check_url": "http://postgres:5432/health",
        "api_key": None  # Optional API key for authenticated health checks
    },
    {
        "name": "external-api",
        "health_check_url": "https://api.example.com/health",
        "api_key": "your-api-key-here"
    }
]

# Include health probe router
app.include_router(health_probe_router(service_dependencies))
```

#### Available Endpoints

##### 1. Liveness Probe (`GET /health/liveness`)

- **Purpose**: Checks if the application process is running and not deadlocked
- **Kubernetes Action**: If this fails, the container is killed and restarted
- **Response**: Returns uptime in seconds
- **Rule**: Keep it simple. Do NOT check databases or external dependencies here

```json
{
  "status": "up",
  "uptime_seconds": 123.45
}
```

##### 2. Readiness Probe (`GET /health/readiness`)

- **Purpose**: Checks if the app is ready to handle user requests
- **Kubernetes Action**: If this fails, traffic stops being sent to this pod
- **Response**: Returns status of all configured service dependencies
- **Rule**: Check critical dependencies here (databases, external APIs, etc.)

```json
{
  "status": "ready",
  "checks": {
    "database": "healthy",
    "external-api": "healthy"
  }
}
```

If a dependency fails:

```json
{
  "status": "unhealthy",
  "checks": {
    "database": "error: Connection refused",
    "external-api": "unhealthy (status: 503)"
  },
  "error": "Service unavailable"
}
```

##### 3. Startup Probe (`GET /health/startup`)

- **Purpose**: Checks if the application has finished initialization
- **Kubernetes Action**: Blocks liveness/readiness probes until this returns 200
- **Response**: Returns startup timestamp
- **Rule**: Useful for apps that need to load large ML models or caches on boot

```json
{
  "status": "started",
  "timestamp": "2025-12-04T10:30:00.000000+00:00"
}
```

#### Features

- **Automatic Logging Suppression**: Health check endpoints are automatically excluded from access logs to reduce noise
- **Dependency Health Checks**: Readiness probe checks external service dependencies with configurable timeouts (5 seconds default)
- **Authentication Support**: Optional API key support for authenticated health checks
- **Kubernetes-Ready**: HTTP status codes follow Kubernetes conventions (200 = healthy, 503 = unhealthy)

### Structured Logging

- Initialize structured logging with `init_logger()`, which auto-selects JSON output in production (`IS_PROD=true`) and colored console output otherwise.
- Retrieve loggers via `get_logger(__name__)`. A `request_id` and timestamp are added automatically.

### Application Configuration

Load strongly-typed configuration from environment variables:

```python
from backend_common.config.app_config import AppConfig

config = AppConfig.from_env()
print(config)  # secrets are redacted in __str__
```

Required variables: `CLIENT_URL`, `HMAC_SECRET`, `OPENAI_API_KEY`, `LLM_URL`, `DOCLING_URL`, `WHISPER_URL`, `OCR_URL`. Missing values raise `AppConfigError`.

### DSPy Utilities

- **Adapters and Modules**: `AbstractDspyModule` wraps DSPy modules with automatic adapter selection. The `DisableReasoningAdapter` appends the `\no_think` token for Qwen 3 hybrid reasoning models.
- **Streaming Listener**: `SwissGermanStreamListener` normalizes `ß` to `ss` in streamed content and reasoning fields.
- **Metrics**: `edit_distance_metric` combines WER and CER for a maximized score.

#### Implementing `AbstractDspyModule`
`AbstractDspyModule` handles adapter selection (reasoning vs. no-reasoning) and normalizes streaming chunks. Subclasses only implement `predict_with_context` and `stream_with_context`; the base class already wires `forward` and `stream` to create a `dspy.context` with the right adapter.

Example translation module:

```python
import dspy
from dspy.streaming.messages import StreamResponse

from backend_common.config.app_config import AppConfig
from backend_common.dspy_common.module import AbstractDspyModule
from backend_common.dspy_common.stream_listener import SwissGermanStreamListener
from backend_common.logger import get_logger


class TranslationSignature(dspy.Signature):
    """source_text, source_language, target_language, domain, tone, glossary, context -> translated_text"""

    source_text = dspy.InputField(desc="Input text to translate. May contain markdown formatting.")
    source_language = dspy.InputField(desc="Source language")
    target_language = dspy.InputField(desc="Target language")
    domain = dspy.InputField(desc="Domain or subject area for translation")
    tone = dspy.InputField(desc="Tone or style for translation")
    glossary = dspy.InputField(desc="Glossary definitions for translation")
    context = dspy.InputField(desc="Context containing previous translations to get consistent translations")
    translated_text = dspy.OutputField(
        desc="Translated text. Contains markdown formatting if the input text contains markdown formatting."
    )


class TranslationModule(AbstractDspyModule):
    def __init__(self, app_config: AppConfig):
        super().__init__()
        self.predict = dspy.Predict(TranslationSignature)
        stream_listener = SwissGermanStreamListener(signature_field_name="translated_text", allow_reuse=True)
        self.stream_predict = dspy.streamify(self.predict, stream_listeners=[stream_listener])
        self.logger = get_logger(__name__)
        if os.path.exists(app_config.translation_module_path):
            self.load(app_config.translation_module_path)

    def predict_with_context(self, **kwargs: object) -> dspy.Prediction:
        return self.predict(**kwargs)

    async def stream_with_context(self, **kwargs: object) -> AsyncIterator[StreamResponse]:
        output = self.stream_predict(**kwargs)
        async for chunk in output:
            self.logger.info(str(chunk))
            yield chunk
```

Usage:

```python
module = TranslationModule(app_config)

# Single prediction
result = module.forward(
    source_text="Hallo zusammen!",
    source_language="de",
    target_language="en",
    reasoning=False,  # set True to enable the default DSPy reasoning adapter
)
print(result.translated_text)

# Streaming prediction
async for text_chunk in module.stream(
    source_text="Hallo zusammen!",
    source_language="de",
    target_language="en",
):
    print(text_chunk, end="", flush=True)
```



## Development

### Setup

1. Clone the repository:

```bash
git clone https://github.com/DCC-BS/backend-common.git
cd backend-common
```

2. Install development dependencies:

```bash
uv sync --group dev --extra fastapi  # include FastAPI extras for local dev
```

### Running Tests

```bash
uv run pytest
```

### Code Quality

This project uses:

- **Ruff**: For linting and formatting
- **Pre-commit**: For automated code quality checks
- **Tox**: For testing across multiple Python versions

Run linting:

```bash
uv run ruff check .
```

Run pre-commit hooks:

```bash
uv run pre-commit run --all-files
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to contribute to this project.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Authors

- **Data Competence Center Basel-Stadt** - [dcc@bs.ch](mailto:dcc@bs.ch)
- **Tobias Bollinger** - [tobias.bollinger@bs.ch](mailto:tobias.bollinger@bs.ch)

## Links

- **Homepage**: https://DCC-BS.github.io/backend-common/
- **Repository**: https://github.com/DCC-BS/backend-common
- **Documentation**: https://DCC-BS.github.io/backend-common/
