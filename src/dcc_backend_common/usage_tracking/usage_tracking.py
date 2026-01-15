import hashlib
import hmac

from dcc_backend_common.logger import get_logger


class UsageTrackingService:
    """
    UsageTrackingService is responsible for tracking and logging usage events compatible for OpenSearch functionality.
    """

    def __init__(self, hmac_secret: str):
        """
        Initializes the UsageTrackingService with the given HMAC secret.
        """
        self.hmac_secret = hmac_secret

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
        """
        logger = get_logger(module)

        pseudonym_id = self.get_pseudonymized_user_id(user_id)

        logger.info("app_event", action_name=f"{module}.{func}", pseudonym_id=pseudonym_id, **kwargs)
