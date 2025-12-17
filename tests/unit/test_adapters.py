import dspy  # type: ignore[import-not-found]  # pyright: ignore[reportMissingTypeStubs]

from src.backend_common.dspy_common.adapters import DisableReasoningAdapter


def test_disable_reasoning_adapter_appends_magic_token() -> None:
    signature = dspy.Signature(
        "comment -> toxic: bool",
        instructions="Mark as 'toxic' if the comment includes insults, harassment, or sarcastic derogatory remarks.",
    )
    adapter = DisableReasoningAdapter()
    content = adapter.format_user_message_content(signature, {"text": "hi"}, suffix="!")

    assert content.endswith("!\no_think")
