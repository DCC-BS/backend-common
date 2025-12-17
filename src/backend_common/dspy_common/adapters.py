from typing import override

import dspy
from dspy.signatures import Signature


class DisableReasoningAdapter(dspy.ChatAdapter):
    """Adapter that adds a "\no_think" magic token to disable reasoning in Qwen 3 hybrid reasoning models."""

    @override
    def format_user_message_content(
        self,
        signature: type[Signature],
        inputs: dict[str, str | int | float | bool],
        prefix: str = "",
        suffix: str = "",
        main_request: bool = False,
    ) -> str:
        """
        Format the user message content by adding a "\no_think" magic token to disable reasoning in Qwen 3 hybrid reasoning models.
        """
        custom_suffix = "\no_think"
        return super().format_user_message_content(
            signature=signature,
            inputs=inputs,
            prefix=prefix,
            suffix=suffix + custom_suffix,
            main_request=main_request,
        )
