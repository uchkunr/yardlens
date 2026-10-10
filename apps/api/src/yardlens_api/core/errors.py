import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors that map to a JSON ``{"detail": ...}`` HTTP response."""

    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT


class UnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class SourceError(Exception):
    """Raised when a camera source cannot be resolved or opened."""


def register_exception_handlers(app: FastAPI) -> None:
    async def handle_app_error(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, AppError)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled error", extra={"path": request.url.path}, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(Exception, handle_unexpected)
