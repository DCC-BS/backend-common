import hashlib
import hmac
import re
import warnings
from typing import Any

import structlog.contextvars

from dcc_backend_common.logger import get_logger, get_usage_logger

logger = get_logger(__name__)

ACTION_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
"""Naming convention for the ``action`` field: ``<feature>.<operation>``,
snake_case, exactly two segments (e.g. ``translation.text``,
``quick_action.bullet_points``). Hand-chosen names, never ``__name__`` or
module paths — ``action`` is a terms-aggregation bucket in OpenSearch, so it
must be stable across refactorings and comparable across apps."""

# Closed vocabulary for the llm_call finish_reason field — a contract with the
# OpenSearch finish-reason panel. Provider values outside this set are logged
# as "unexpected" (plus a warning), never passed through raw.
FINISH_REASONS = frozenset({"stop", "length", "tool_call", "content_filter", "error"})


def log_llm_call(result: Any) -> None:
    """
    Emit one flat "llm_call" usage line for a completed pydantic-ai run.

    Accepts any object with pydantic-ai's result shape (AgentRunResult or
    StreamedRunResult): ``result.usage`` and ``result.response``. Duck-typed on
    purpose so apps that build their own pydantic-ai Agent (without BaseAgent)
    can call it without this module importing pydantic_ai.

    The flat top-level keys (input_tokens, output_tokens, total_tokens,
    finish_reason, ...) are a contract with the OpenSearch token panels —
    do not rename or nest them.

    Never raises: it is also called from `finally` blocks on aborted streams,
    where the result may be incomplete.
    """
    try:
        usage = result.usage
        response = result.response

        # RunUsage.tool_calls only counts *executed* function tools, so a
        # structured-output tool ("final_result") yields finish_reason "tool_call"
        # with tool_calls=0 — an inconsistency that made the finish-reason panel
        # unusable. Count the tool invocations actually present in the response.
        tool_calls = sum(1 for part in response.parts if getattr(part, "part_kind", None) == "tool-call")

        finish_reason = response.finish_reason
        if finish_reason is not None and finish_reason not in FINISH_REASONS:
            logger.warning("unexpected finish_reason from provider", raw_finish_reason=finish_reason)
            finish_reason = "unexpected"

        get_usage_logger().info(
            "llm_call",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            tool_calls=tool_calls,
            requests=usage.requests,
            usage_details=usage.details,
            finish_reason=finish_reason,
        )
    except Exception:
        logger.warning("cannot log llm_call for incomplete result", exc_info=True)


class UsageTrackingService:
    """
    UsageTrackingService is responsible for tracking and logging usage events compatible for OpenSearch functionality.

    Events are emitted through the pinned "usage" logger, so they are always
    logged regardless of LOG_LEVEL. Fields are flat, snake_case top-level keys
    so OpenSearch can aggregate on them without nested paths.
    """

    def __init__(self, hmac_secret: str):
        """
        Initializes the UsageTrackingService with the given HMAC secret.
        """
        if not hmac_secret or hmac_secret == "none":  # noqa: S105
            raise ValueError("HMAC secret is not set")
        self.hmac_secret = hmac_secret
        self._logger = get_usage_logger()

    def get_pseudonymized_user_id(self, user_id: str | None) -> str:
        """
        Generates a consistent, one-way pseudonym for a given user ID.
        """
        if user_id is None:
            user_id = "unknown"
        message = user_id.encode("utf-8")
        signature = hmac.new(self.hmac_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return signature

    def log_event(
        self,
        action: str | None = None,
        user_id: str | None = None,
        *,
        module: str | None = None,
        func: str | None = None,
        **kwargs: str | int | float | bool | None,
    ) -> None:
        """
        Logs a usage event with the given details.

        Emits an "app_event" record with the given ``action``, a pseudonymized
        user id, and any extra fields as flat top-level keys.

        ``action`` must follow ACTION_PATTERN: ``<feature>.<operation>`` in
        snake_case (e.g. ``translation.text``). A non-conforming action is
        still emitted, but logged with a warning — telemetry must never break
        a user request.

        The ``module``/``func`` keyword pair is the deprecated pre-0.1.19
        calling convention (action was built as ``{module}.{func}``, usually
        from ``__name__``, which coupled dashboards to Python module paths).
        It will be removed in a future release.

        Also binds pseudonym_id into the structlog context, so every later log
        line of the same request (llm_call, request_finished, ...) carries it —
        per-user token attribution then needs no join back to the app_event.
        The logging middleware clears the context at the start of each request,
        so the binding cannot leak across requests.
        """
        if module is not None or func is not None:
            warnings.warn(
                "log_event(module=..., func=...) is deprecated; pass action='<feature>.<operation>' instead",
                DeprecationWarning,
                stacklevel=2,
            )
            action = f"{module}.{func}"
        if action is None:
            raise TypeError("log_event() requires an action")
        if not ACTION_PATTERN.match(action):
            logger.warning("action does not match <feature>.<operation> naming convention", action=action)

        pseudonym_id = self.get_pseudonymized_user_id(user_id)
        # The computed pseudonym always wins; a caller-supplied one would
        # otherwise raise TypeError (duplicate keyword) and break the request.
        kwargs.pop("pseudonym_id", None)
        structlog.contextvars.bind_contextvars(pseudonym_id=pseudonym_id)

        self._logger.info("app_event", action=action, pseudonym_id=pseudonym_id, **kwargs)
