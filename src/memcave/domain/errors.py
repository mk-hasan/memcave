"""Public errors raised by memcave."""


class MemcaveError(Exception):
    """Base class for errors that cross the public API."""


class NotFoundError(MemcaveError):
    """The requested record does not exist in the caller's scope."""


class TenantIsolationError(MemcaveError):
    """A record from another tenant was encountered."""


class ScopeError(MemcaveError):
    """The caller scope cannot perform this operation."""


class VersionConflictError(MemcaveError):
    """The stored version does not match expected_version."""


class PolicyRejectedError(MemcaveError):
    """A write or retention policy rejected the operation."""


class ValidationError(MemcaveError):
    """The request is structurally invalid."""


class ProviderError(MemcaveError):
    """An embedding or model provider failed."""


class DreamflowError(MemcaveError):
    """The dreamflow pipeline could not finish a run."""
