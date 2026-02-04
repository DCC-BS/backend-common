"""Focused traceback formatter for development mode.

Shows detailed tracebacks for user code while keeping library code traces
dense and minimal.
"""

import os
from io import StringIO
from typing import Any

from rich.console import Console
from rich.traceback import Traceback

# Default paths to consider as "user code" for detailed tracebacks
# Can be extended via LOGGER_USER_CODE_PATHS env var (comma-separated)
DEFAULT_USER_CODE_PATHS = ("dcc_backend_common", "src/", "app/", "tests/")


def _get_user_code_paths() -> tuple[str, ...]:
    """Get the list of path patterns that identify user code."""
    env_paths = os.getenv("LOGGER_USER_CODE_PATHS", "")
    if env_paths:
        custom_paths = tuple(p.strip() for p in env_paths.split(",") if p.strip())
        return DEFAULT_USER_CODE_PATHS + custom_paths
    return DEFAULT_USER_CODE_PATHS


def _is_user_code_frame(filename: str) -> bool:
    """Check if a frame's filename belongs to user code."""
    user_paths = _get_user_code_paths()
    return any(path in filename for path in user_paths)


class FocusedTracebackFormatter:
    """
    Custom exception formatter that shows detailed tracebacks for user code
    while keeping library code traces dense and minimal.

    Uses Rich for the main traceback (without locals), then appends
    a focused "Local variables in your code" section.

    Configuration:
        - LOGGER_USER_CODE_PATHS: Comma-separated paths to consider as user code
          (in addition to defaults: dcc_backend_common, src/, app/, tests/)

    Args:
        width: Width of the traceback output (default: 100)
        max_frames: Maximum number of frames to show (default: 50)
        locals_max_string: Maximum length for local variable repr (default: 80)
    """

    def __init__(
        self,
        width: int = 100,
        max_frames: int = 50,
        locals_max_string: int = 80,
    ) -> None:
        self.width = width
        self.max_frames = max_frames
        self.locals_max_string = locals_max_string

    def __call__(self, sio: Any, exc_info: tuple[Any, ...]) -> None:
        """Format the exception and write to the string IO."""
        exc_type, exc_value, exc_tb = exc_info

        # Create a Rich Traceback with show_locals=False for the full trace
        tb = Traceback.from_exception(
            exc_type,
            exc_value,
            exc_tb,
            width=self.width,
            max_frames=self.max_frames,
            show_locals=False,  # We handle locals ourselves for user code only
        )

        # Render Rich traceback to a temp buffer, then write to sio
        temp_buffer = StringIO()
        console = Console(file=temp_buffer, force_terminal=True, width=self.width)
        console.print(tb)
        sio.write(temp_buffer.getvalue())

        # Now add focused locals for user code frames only
        self._print_user_code_locals(sio, exc_tb)

    def _print_user_code_locals(self, sio: Any, tb: Any) -> None:
        """Print local variables only for user code frames."""
        user_frames = []

        # Walk through all frames
        current_tb = tb
        while current_tb is not None:
            frame = current_tb.tb_frame
            filename = frame.f_code.co_filename

            if _is_user_code_frame(filename):
                user_frames.append({
                    "filename": filename,
                    "lineno": current_tb.tb_lineno,
                    "name": frame.f_code.co_name,
                    "locals": frame.f_locals.copy(),
                })
            current_tb = current_tb.tb_next

        if not user_frames:
            return

        # ANSI color codes
        CYAN_BOLD = "\033[1;36m"
        YELLOW_BOLD = "\033[1;33m"
        GREEN = "\033[32m"
        RESET = "\033[0m"

        # Print locals for user code frames
        sio.write(f"\n{CYAN_BOLD}━━━ Local variables in your code ━━━{RESET}\n")

        for frame_info in user_frames:
            short_filename = frame_info["filename"].split("/")[-3:]
            short_path = "/".join(short_filename)
            sio.write(f"\n{YELLOW_BOLD}► {short_path}:{frame_info['lineno']} in {frame_info['name']}(){RESET}\n")

            locals_dict = frame_info["locals"]
            if not locals_dict:
                sio.write("  (no local variables)\n")
                continue

            # Filter out private/dunder variables and common noise
            filtered_locals = {
                k: v
                for k, v in locals_dict.items()
                if not k.startswith("_") and k not in ("self", "cls") and not callable(v)
            }

            if not filtered_locals:
                sio.write("  (no relevant local variables)\n")
                continue

            for name, value in filtered_locals.items():
                try:
                    repr_value = repr(value)
                    if len(repr_value) > self.locals_max_string:
                        repr_value = repr_value[: self.locals_max_string] + "..."
                    sio.write(f"  {GREEN}{name}{RESET} = {repr_value}\n")
                except Exception:
                    sio.write(f"  {GREEN}{name}{RESET} = <repr failed>\n")
