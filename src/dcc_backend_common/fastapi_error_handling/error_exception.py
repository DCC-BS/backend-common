from typing import TypedDict

from fastapi import status
from fastapi.exceptions import HTTPException

from dcc_backend_common.fastapi_error_handling.error_codes import ApiErrorCodes


class ErrorResponse(TypedDict, total=False):
    errorId: str | ApiErrorCodes
    status: int | None  # default to 500 if not provided
    debugMessage: str | None


class ApiErrorException(Exception):
    def __init__(self, error_response: ErrorResponse):
        if "status" not in error_response:
            error_response["status"] = status.HTTP_500_INTERNAL_SERVER_ERROR
        self.error_response = error_response


def api_error_exception(
    errorId: str = ApiErrorCodes.UNEXPECTED_ERROR,
    status: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    debugMessage: str | None = None,
):
    return ApiErrorException(
        error_response={
            "errorId": errorId,
            "status": status,
            "debugMessage": debugMessage,
        }
    )


def construct_api_error_exception(
    exception: Exception,
    error_id: str | ApiErrorCodes = ApiErrorCodes.UNEXPECTED_ERROR,
    status_code: int | None = None,
) -> ApiErrorException:
    """
    Constructs an ApiErrorException from the given exception, error ID, and status code.

    Parameters:
        exception (Exception): The original exception that occurred.
        error_id (str | ApiErrorCodes): A string or enum value representing the error ID
        status_code (int | None): An optional HTTP status code for the error response.

    Returns:
        ApiErrorException: An exception containing the structured error response.
    """
    error_response: ErrorResponse = {"errorId": error_id, "status": status_code}

    if not status_code and isinstance(exception, HTTPException):
        error_response["status"] = exception.status_code

    debug_message = str(exception) if exception is not None else None

    if debug_message is not None:
        error_response["debugMessage"] = debug_message

    return ApiErrorException(error_response)
