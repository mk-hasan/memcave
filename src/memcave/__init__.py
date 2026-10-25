"""Framework-independent persistent memory for AI agents."""

from memcave.domain.errors import (
    DreamflowError,
    MemcaveError,
    NotFoundError,
    PolicyRejectedError,
    ProviderError,
    ScopeError,
    TenantIsolationError,
    ValidationError,
    VersionConflictError,
)
from memcave.services.memory_manager import MemoryManager

__version__ = "0.1.0"
__all__ = [
    "DreamflowError",
    "MemcaveError",
    "MemoryManager",
    "NotFoundError",
    "PolicyRejectedError",
    "ProviderError",
    "ScopeError",
    "TenantIsolationError",
    "ValidationError",
    "VersionConflictError",
    "__version__",
]
