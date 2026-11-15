"""Exception hierarchy. Everything ferrule raises on purpose derives from
FerruleError so API handlers can map it to a status code in one place."""


class FerruleError(Exception):
    status = 500
    code = "internal"


class ConfigError(FerruleError):
    code = "config"


class NotFound(FerruleError):
    status = 404
    code = "not_found"


class Conflict(FerruleError):
    status = 409
    code = "conflict"


class Forbidden(FerruleError):
    status = 403
    code = "forbidden"


class Unauthorized(FerruleError):
    status = 401
    code = "unauthorized"


class RateLimited(FerruleError):
    status = 429
    code = "rate_limited"

    def __init__(self, retry_after_s: float, message: str = "rate limited"):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class QuotaExceeded(FerruleError):
    status = 429
    code = "quota"


class InvalidTransition(FerruleError):
    status = 409
    code = "invalid_transition"

    def __init__(self, state: str, event: str):
        super().__init__(f"no transition from {state} on {event!r}")
        self.state = state
        self.event = event


class DuplicateJob(Conflict):
    code = "duplicate"


class ValidationFailed(FerruleError):
    status = 422
    code = "validation"


class AmbiguousCommand(FerruleError):
    code = "cli_ambiguous"


class UnknownCommand(FerruleError):
    code = "cli_unknown"


class MigrationError(FerruleError):
    code = "migration"
