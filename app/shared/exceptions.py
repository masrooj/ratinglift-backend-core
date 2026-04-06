class ApplicationError(Exception):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ValidationError(ApplicationError):
    pass


class NotFoundError(ApplicationError):
    pass
