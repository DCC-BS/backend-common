# dcc-backend-common

[![PyPI version](https://img.shields.io/pypi/v/dcc-backend-common.svg)](https://pypi.org/project/dcc-backend-common/)
[![Commit activity](https://img.shields.io/github/commit-activity/m/DCC-BS/backend-common)](https://img.shields.io/github/commit-activity/m/DCC-BS/backend-common)
[![License](https://img.shields.io/github/license/DCC-BS/backend-common)](https://img.shields.io/github/license/DCC-BS/backend-common)

Common utilities and components for backend services developed by the Data Competence Center Basel-Stadt.

## Overview

`dcc-backend-common` is a Python library that provides shared functionality for backend services, including:

- **LLM Agent**: Abstract base class for pydantic-ai agents with streaming, postprocessing, and thinking control
- **FastAPI Health Probes**: Kubernetes-ready health check endpoints (liveness, readiness, startup)
- **Structured Logging**: Integration with `structlog` for consistent logging across services
- **Configuration Management**: Environment-based configuration with `python-dotenv`

## Installation

### Basic Installation

```bash
uv add dcc-backend-common
```

### With pydantic-ai Support

```bash
uv add "dcc-backend-common[pydantic_ai]"
```

### With FastAPI Support

```bash
uv add "dcc-backend-common[fastapi]"
```

### All Extras

```bash
uv add "dcc-backend-common[pydantic_ai,fastapi]"
```

## Requirements

- Python 3.12 or higher
- Core dependencies: `pydantic>=2.12.5`, `python-dotenv`, `structlog>=25.5.0`

### Optional Dependencies

- `pydantic_ai` extras: `pydantic-ai>=1.103.0`, `pydantic-ai-slim[openai]>=1.103.0`
- `fastapi` extras: `aiohttp>=3.13.3`, `fastapi>=0.136.0,<1.0`

## Features

### LLM Agent

`BaseAgent` is an abstract base class for building pydantic-ai agents with built-in streaming, postprocessing, and thinking mode control. It targets OpenAI-compatible endpoints (e.g. vLLM serving Gemma).

#### Basic Usage

```python
from pydantic_ai import Agent
from pydantic_ai.models import Model
from dcc_backend_common.config.app_config import LlmConfig
from dcc_backend_common.llm_agent import BaseAgent

class MyAgent(BaseAgent[None, str]):
    def create_agent(self, model: Model) -> Agent[None, str]:
        return Agent(
            model=model,
            system_prompt="You are a helpful assistant.",
        )

config = LlmConfig(
    llm_model="gemma-3-27b-it",
    llm_url="https://your-vllm-endpoint/v1",
    llm_api_key="your-key",
)

agent = MyAgent(config)
result = await agent.run("What is the capital of Switzerland?")
```

#### Thinking Mode

Pass `enable_thinking=True` to enable extended reasoning. The agent uses `chat_template_kwargs: {enable_thinking: bool}` via the OpenAI `extra_body` parameter — no prompt modification required.

```python
agent_with_thinking = MyAgent(config, enable_thinking=True)
result = await agent_with_thinking.run("Solve this step by step: ...")

agent_no_thinking = MyAgent(config, enable_thinking=False)  # default
```

#### Streaming

```python
# Stream text deltas
async for chunk in agent.run_stream_text("Tell me a story"):
    print(chunk, end="", flush=True)

# Stream full accumulated text (delta=False)
async for text in agent.run_stream_text("Tell me a story", delta=False):
    print(text)

# Stream structured output chunks
async for chunk in agent.run_stream_output("List three cities"):
    print(chunk)

# Stream raw pydantic-ai events
async for event in agent.run_stream_events("Hello"):
    print(event)
```

#### Structured Output

```python
from pydantic import BaseModel

class CityList(BaseModel):
    cities: list[str]
    country: str

class CityAgent(BaseAgent[None, CityList]):
    def create_agent(self, model: Model) -> Agent[None, CityList]:
        return Agent(model=model, output_type=CityList)

agent = CityAgent(config, output_type=CityList)
result = await agent.run("List three Swiss cities")
# result.cities == ["Zurich", "Basel", "Bern"]
```

#### Streaming Lists

Use `stream_list` to yield list items one by one as they are generated:

```python
class ItemAgent(BaseAgent[None, str]):
    def create_agent(self, model: Model) -> Agent[None, str]:
        return Agent(model=model)

agent = ItemAgent(config)
async for item in agent.stream_list("Name five fruits"):
    print(item)  # prints each fruit as soon as it is ready
```

#### Postprocessing

All output passes through a postprocessing pipeline automatically:

- **`replace_eszett`**: Replaces `ß` with `ss` in all string fields (including nested Pydantic models, dicts, and lists)
- **`trim_text`**: Strips leading whitespace from text output (first chunk only in streaming)

Custom postprocessors can be added by overriding `_get_postprocessors()`:

```python
class MyAgent(BaseAgent[None, str]):
    def _get_postprocessors(self):
        return super()._get_postprocessors() + [my_custom_processor]
```

Prompt transformation (e.g. injecting context) can be done by overriding `process_prompt()`:

```python
class MyAgent(BaseAgent[None, str]):
    def process_prompt(self, prompt, deps):
        return f"[context] {prompt}"
```

#### Debugging

```python
from dcc_backend_common.llm_agent.debugging import withDebbugger

class MyAgent(BaseAgent[None, str]):
    @withDebbugger
    async def run(self, *args, **kwargs):
        return await super().run(*args, **kwargs)
```

Or inject an event stream handler directly:

```python
from dcc_backend_common.llm_agent.debugging import create_event_debugger

async for event in agent.run_stream_events(
    "Hello",
    event_stream_handler=create_event_debugger("my-agent"),
):
    ...
```

---

### FastAPI Health Probes

Kubernetes-ready health check endpoints that follow best practices for container orchestration.

#### Example Usage

```python
from fastapi import FastAPI
from dcc_backend_common.fastapi_health_probes import health_probe_router

app = FastAPI()

service_dependencies = [
    {
        "name": "database",
        "health_check_url": "http://postgres:5432/health",
        "api_key": None,
    },
    {
        "name": "external-api",
        "health_check_url": "https://api.example.com/health",
        "api_key": "your-api-key-here",
    },
]

app.include_router(health_probe_router(service_dependencies))
```

#### Available Endpoints

| Endpoint | Purpose | Kubernetes action on failure |
|----------|---------|------------------------------|
| `GET /health/liveness` | Process is alive and not deadlocked | Container is restarted |
| `GET /health/readiness` | App is ready to handle requests | Traffic is stopped to this pod |
| `GET /health/startup` | App has finished initialization | Liveness/readiness probes are blocked |

**Liveness** — returns uptime in seconds. Keep it simple; do not check external deps here.

**Readiness** — checks all configured service dependencies:

```json
{
  "status": "ready",
  "checks": {
    "database": "healthy",
    "external-api": "healthy"
  }
}
```

**Startup** — returns startup timestamp. Useful for apps that load large ML models on boot.

---

### Structured Logging

```python
from dcc_backend_common.logger import init_logger, get_logger

init_logger(app_name="my-app")  # JSON in production (IS_PROD=true), colored console otherwise
logger = get_logger(__name__)

logger.info("Processing document", page_count=42)  # flat, snake_case keys
```

`init_logger()` sets up a single pipeline: structlog events *and* stdlib
records (uvicorn, third-party libraries, Python warnings) are all rendered by
the root handler. In production everything is one JSON line per event; in
development a Rich console renderer is used. Chatty libraries (`httpx`,
`httpcore`, ...) are capped at WARNING (override per app via
`library_log_levels={"httpx": "DEBUG"}`), uvicorn access logs are disabled.
`app_name` is stamped as `app` on every production line so the JSON is
self-describing outside the k8s pod-label context.

**`event` vs `message`**: in production, `event` is a closed vocabulary of
event types (`EVENT_TYPES`: `app_event`, `llm_call`, `request_finished`,
`request_failed`, `health check failed`, `health check recovered`) that
dashboards and monitors term-match on. Any other event string — i.e. a
human-readable message like the one above — is moved to the `message` field
at render time. Extend the vocabulary per app with
`init_logger(extra_event_types={...})`.

`LOG_LEVEL` controls application diagnostics (recommended: `debug` on test
stages, `info` in prod). Usage events are exempt — see below.

**Usage events** go through the pinned `usage` logger and are always emitted,
regardless of `LOG_LEVEL`. Filter on `logger: "usage"` in OpenSearch.

```python
usage_tracking_service.log_event("translation.text", user_id, text_length=42)
```

`action` follows `<feature>.<operation>` in snake_case (`ACTION_PATTERN`) —
hand-chosen names, never `__name__` or module paths, so dashboard buckets
survive refactorings and stay comparable across apps. `log_event` also binds
the `pseudonym_id` into the request context, so subsequent `llm_call` and
`request_finished` lines of the same request carry it. `llm_call` lines come
from `BaseAgent` automatically; apps with a hand-built pydantic-ai agent can
call `log_llm_call(result)` themselves.

**Request correlation and completion events**: the logging middleware binds a
per-request `request_id` (from the `X-Request-ID` header, or generated) into
the log context and emits one `request_finished` (or `request_failed`, with
traceback) per request, carrying `method`, `path` (route template, never the
concrete URL), `status_code`, and `duration_s`. Health probes and CORS
preflights are not logged as `request_finished`; excluded paths still log
`request_failed` when they raise. Exclude additional high-frequency endpoints
by route template:

```python
from dcc_backend_common.fastapi_logging_middleware import add_logging_middleware

add_logging_middleware(app)  # excludes /health by default
add_logging_middleware(app, excluded_paths={"/health", "/task/{task_id}/status"})
```

**Serving**: run with plain `uvicorn` (not `fastapi run`, whose Rich banner
and handlers bypass the JSON pipeline):

```bash
uvicorn my_app.app:app --host 0.0.0.0 --port 8090 --no-access-log
```

---

### Application Configuration

Load strongly-typed configuration from environment variables:

```python
from dcc_backend_common.config.app_config import AppConfig, LlmConfig

config = AppConfig.from_env()
print(config)  # secrets are redacted in __str__

llm = LlmConfig(
    llm_model="gemma-3-27b-it",
    llm_url="http://vllm:8000/v1",
    llm_api_key="your-key",
)
```

`AppConfig.from_env()` reads `CLIENT_URL`, `HMAC_SECRET`, `OPENAI_API_KEY`, `LLM_URL`, `DOCLING_URL`, `WHISPER_URL`, `OCR_URL` from the environment. Missing required values raise `AppConfigError`.

---

## Development

### Setup

```bash
git clone https://github.com/DCC-BS/backend-common.git
cd backend-common
uv sync --group dev --all-extras
```

### Running Tests

```bash
uv run pytest tests/unit/
```

Integration tests require a real LLM endpoint:

```bash
LLM_URL=... LLM_API_KEY=... LLM_MODEL=... uv run pytest tests/integration/ -m integration
```

### Code Quality

```bash
make check          # lock check + pre-commit + ty type check
uv run pytest tests/unit  # unit tests
```

## Releasing

This project uses GitHub Actions for automated releases to PyPI.

1. Update the `version` field in `pyproject.toml`.
2. Commit and push to `main`.
3. In GitHub Actions, run the **Publish to PyPI** workflow manually.

The workflow detects the version, creates a git tag, builds the package, and publishes via Trusted Publishing.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

MIT — see [LICENSE](LICENSE).

## Authors

- **Data Competence Center Basel-Stadt** — [dcc@bs.ch](mailto:dcc@bs.ch)
- **Tobias Bollinger** — [tobias.bollinger@bs.ch](mailto:tobias.bollinger@bs.ch)
- **Yanick Schraner** — [yanick.schraner@bs.ch](mailto:yanick.schraner@bs.ch)

## Links
- **Homepage**: https://DCC-BS.github.io/backend-common/
- **Repository**: https://github.com/DCC-BS/backend-common
- **Documentation**: https://DCC-BS.github.io/backend-common/
