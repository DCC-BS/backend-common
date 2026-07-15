"""Logfire instrumentation for the integration suite.

Spans are only shipped when a token is present — either ``LOGFIRE_TOKEN`` or the
credentials in ``.logfire/logfire_credentials.json`` written by ``logfire auth``.
Without one the SDK stays local and the tests run unchanged.

``make integration`` also passes ``--logfire``, which enables logfire's own pytest
plugin so each test gets a span the agent spans below nest into.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def logfire_instrumentation():
    logfire = pytest.importorskip("logfire", reason="logfire is required for the integration suite")

    # logfire defaults send_to_logfire to False whenever PYTEST_VERSION is in the environment.
    # The --logfire plugin only overrides that for its own local config, so the global instance
    # the instrumentation below writes into has to ask for it explicitly.
    logfire.configure(send_to_logfire="if-token-present", service_name="backend-common-integration")
    logfire.instrument_pydantic_ai()
    # Captures the underlying HTTP calls so retries made by AsyncTenacityTransport are visible.
    logfire.instrument_httpx(capture_headers=False)
    yield logfire
    logfire.force_flush()
