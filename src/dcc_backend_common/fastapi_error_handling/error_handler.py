from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from dcc_backend_common.fastapi_error_handling.error_codes import ApiErrorCodes
from dcc_backend_common.fastapi_error_handling.error_exception import ApiErrorException


def inject_api_error_handler(app: FastAPI):
    app.add_exception_handler(ApiErrorException, api_error_handler)


def api_error_handler(request: Request, exc: Exception) -> Response:
    """
    Convert exceptions raised during request handling into a JSON HTTP response.

    If `exc` is an `ApiErrorException`, returns its `error_response` payload and status code.
    Otherwise returns a 500 response with `errorId` set to `UNEXPECTED_ERROR`, `status` 500, and a `debugMessage` containing the exception string.

    Parameters:
        request (Request): The incoming HTTP request that triggered the exception.
        exc (Exception): The exception raised during request processing.

    Returns:
        Response: A JSON HTTP response describing the error.
    """
    if isinstance(exc, ApiErrorException):
        status_code = (
            exc.error_response.get("status", status.HTTP_500_INTERNAL_SERVER_ERROR)
            or status.HTTP_500_INTERNAL_SERVER_ERROR
        )

        return JSONResponse(
            status_code=status_code,
            media_type="application/json",
            content=exc.error_response,
        )

    return JSONResponse(
        status_code=500,
        media_type="application/json",
        content={"errorId": ApiErrorCodes.UNEXPECTED_ERROR, "status": 500, "debugMessage": str(exc)},
    )
