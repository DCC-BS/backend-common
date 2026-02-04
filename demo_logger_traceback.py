"""Quick script to test the focused traceback formatter.

Usage:
    # Test focused mode (default):
    uv run python demo_logger_traceback.py

    # Test rich mode (full locals):
    uv run python demo_logger_traceback.py rich
"""

import os
import sys

# Set up environment for dev mode
os.environ["IS_PROD"] = "false"
# Add the current directory as a "user code" path
os.environ["LOGGER_USER_CODE_PATHS"] = "backend-common"

# Allow selecting traceback style via command line argument
if len(sys.argv) > 1 and sys.argv[1].lower() == "rich":
    os.environ["DEV_TRACEBACK_STYLE"] = "rich"
    print("Using: DEV_TRACEBACK_STYLE=rich (full locals for all frames)\n")
else:
    os.environ["DEV_TRACEBACK_STYLE"] = "focused"
    print("Using: DEV_TRACEBACK_STYLE=focused (locals only for user code)\n")

from dcc_backend_common.logger import get_logger, init_logger

init_logger()
logger = get_logger(__name__)


def my_function_in_user_code(user_id: int, data: dict) -> None:
    """A function in 'user code' that will fail."""
    processed_data = {"id": user_id, "payload": data}
    result = None

    # This will trigger an exception
    another_helper(processed_data)


def another_helper(info: dict) -> None:
    """Another user code function in the stack."""
    value = info["missing_key"]  # KeyError here


def test_library_frames() -> None:
    """Test that shows both library and user code frames."""
    import json

    bad_data = {"key": object()}  # Can't serialize
    # json.dumps will fail and create library frames in the traceback
    json.dumps(bad_data)


def main() -> None:
    logger.info("Test 1: Simple user code error")
    logger.info("-" * 50)

    try:
        my_function_in_user_code(user_id=42, data={"name": "test", "items": [1, 2, 3]})
    except Exception:
        logger.exception("KeyError in user code")

    logger.info("")
    logger.info("Test 2: Error with library frames in the stack")
    logger.info("-" * 50)

    try:
        test_library_frames()
    except Exception:
        logger.exception("TypeError from json library")


if __name__ == "__main__":
    main()
