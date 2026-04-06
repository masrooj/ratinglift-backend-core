from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApplicationException(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationException)
    async def application_exception_handler(request: Request, exc: ApplicationException):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
