"""Injectable clock, tracer, retention, and write policy."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from memcave.domain.errors import PolicyRejectedError, ScopeError, TenantIsolationError
from memcave.domain.models import RetrievalHit, TenantScope


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("clock instant must be timezone-aware")
        self.instant = instant

    def now(self) -> datetime:
        return self.instant


class NoOpSpan:
    def set_attribute(self, key: str, value: str | int | float | bool) -> None:
        del key, value

    def record_exception(self, exc: BaseException) -> None:
        del exc

    def end(self) -> None:
        return None


class NoOpTracer:
    def start_span(
        self,
        name: str,
        attributes: object | None = None,
    ) -> NoOpSpan:
        del name, attributes
        return NoOpSpan()


class KeepForeverRetention:
    def max_age(self) -> timedelta | None:
        return None

    def allows(self, occurred_at: datetime, now: datetime) -> bool:
        del occurred_at, now
        return True


class MaxAgeRetention:
    def __init__(self, max_age: timedelta) -> None:
        self._max_age = max_age

    def max_age(self) -> timedelta | None:
        return self._max_age

    def allows(self, occurred_at: datetime, now: datetime) -> bool:
        return now - occurred_at <= self._max_age


class DefaultWritePolicy:
    def check_content(self, title: str, content: str) -> None:
        if not title.strip() or not content.strip():
            raise PolicyRejectedError("title and content are required")

    def check_scope(self, scope: TenantScope, tenant_id: UUID, user_id: UUID | None) -> None:
        if scope.tenant_id != tenant_id:
            raise TenantIsolationError("write tenant does not match the caller scope")
        if scope.user_id is not None and user_id is not None and scope.user_id != user_id:
            raise ScopeError("write user does not match the caller scope")


class PatternRedaction:
    def redact_text(self, text: str) -> tuple[str, bool, list[str]]:
        from memcave.services.text import redact_text

        return redact_text(text)

    def redact_value(self, value: object) -> tuple[object, bool, list[str]]:
        from memcave.services.text import redact_value

        return redact_value(value)


class IdentityReranker:
    async def rerank(
        self,
        query: str,
        hits: Sequence[RetrievalHit],
    ) -> Sequence[RetrievalHit]:
        del query
        return list(hits)
