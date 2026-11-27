"""API errors.

Handlers and services raise ``ApiError`` subclasses for every expected
failure. The error middleware renders them as
``{"error": <code>, "message": <text>, ...extra fields}`` with the class's
HTTP status. Anything else that escapes a handler is a bug and becomes a
500 (see middleware.errors).
"""
from __future__ import annotations


class ApiError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str = "", code: str | None = None, **fields):
        super().__init__(message or self.code)
        self.message = message or (code or self.code).replace("_", " ")
        if code is not None:
            self.code = code
        self.fields = fields
        self.headers: dict[str, str] = {}

    def body(self) -> dict:
        out = {"error": self.code, "message": self.message}
        for k, v in self.fields.items():
            out[k] = v
        return out


class BadRequest(ApiError):
    status = 400
    code = "bad_request"


class NotFound(ApiError):
    status = 404
    code = "not_found"


class MethodNotAllowed(ApiError):
    status = 405
    code = "method_not_allowed"


class Conflict(ApiError):
    status = 409
    code = "conflict"


class Unprocessable(ApiError):
    status = 422
    code = "unprocessable"


class TooManyRequests(ApiError):
    status = 429
    code = "rate_limited"


class ValidationFailed(BadRequest):
    code = "validation_failed"

    def __init__(self, problems: dict[str, str]):
        super().__init__("request body failed validation", problems=dict(sorted(problems.items())))
