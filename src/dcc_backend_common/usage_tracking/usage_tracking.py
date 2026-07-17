import hashlib
import hmac

from dcc_backend_common.logger import get_usage_logger


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

    def log_event(self, module: str, func: str, user_id: str | None, **kwargs: str | int | float | bool | None) -> None:
        """
        Logs a usage event with the given details.

        Emits an "app_event" record with action="{module}.{func}", a
        pseudonymized user id, and any extra fields as flat top-level keys.
        """
        pseudonym_id = self.get_pseudonymized_user_id(user_id)

        self._logger.info("app_event", action=f"{module}.{func}", pseudonym_id=pseudonym_id, **kwargs)
