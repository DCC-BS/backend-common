import pytest

from dcc_backend_common.config import log_secret


@pytest.mark.parametrize(
    "secret,expected",
    [
        (None, "None"),
        ("", "None"),
        ("a", "a"),
        ("ab", "ab"),
        ("abc", "ab*"),
        ("token", "to***"),
        ("secret123", "se*******"),
    ],
)
def test_log_secret(secret: str | None, expected: str) -> None:
    assert log_secret(secret) == expected
