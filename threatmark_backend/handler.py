from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import field_validator
from sqlmodel import SQLModel, col
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from opentelemetry import trace

from .schema import ActorRow, ApiKey, RelationAggregate, RelationEvent

Permission = Literal["can_ingest", "can_infer"]

tracer = trace.get_tracer(__name__)


def _require_permission(actor: ActorRow | None, permission: Permission) -> None:
    if actor is None:
        raise PermissionError("Actor is not authenticated")

    if not getattr(actor, permission):
        raise PermissionError(f"Actor is not allowed to {permission.removeprefix('can_')}")


class IngestRequest(SQLModel):
    left_id: bytes
    right_id: bytes
    occurred_at: datetime

    @field_validator("left_id", "right_id")
    @classmethod
    def _validate_member_id(cls, value: bytes) -> bytes:
        if len(value) != 16:
            raise ValueError("member IDs must be exactly 16 bytes")

        return value


async def ingest(event: IngestRequest, session: AsyncSession, actor: ActorRow | None) -> None:
    _require_permission(actor, "can_ingest")

    await session.execute(
        insert(RelationEvent).values(
            left_id=event.left_id,
            right_id=event.right_id,
            occurred_at=event.occurred_at,
        )
    )

    aggregate_insert = pg_insert(RelationAggregate).values(
        left_id=event.left_id,
        right_id=event.right_id,
        first_occurred_at=event.occurred_at,
        last_occurred_at=event.occurred_at,
        count=1,
    )
    await session.execute(
        aggregate_insert.on_conflict_do_update(
            index_elements=["left_id", "right_id"],
            set_={
                "first_occurred_at": func.least(
                    RelationAggregate.first_occurred_at,
                    aggregate_insert.excluded.first_occurred_at,
                ),
                "last_occurred_at": func.greatest(
                    RelationAggregate.last_occurred_at,
                    aggregate_insert.excluded.last_occurred_at,
                ),
                "count": RelationAggregate.count + 1,
            },
        )
    )
    await session.commit()


class InferRequest(SQLModel):
    id: bytes

    @field_validator("id")
    @classmethod
    def _validate_member_id(cls, value: bytes) -> bytes:
        if len(value) != 16:
            raise ValueError("member IDs must be exactly 16 bytes")

        return value


class InferResponse(SQLModel):
    """
    For a given set A member a , the inference endpoint returns a numerical vector that encodes the following statistics
    """

    right_side_count: int
    """
    The number of times a has been on the right side of an ingest relation.
    """

    right_side_count_last_7_days: int
    """
    The number of times `a` has been on the right side of an ingest relation in the last 7 days.
    """

    first_appeared: float | None
    """
    Seconds since a first appeared on the right side of the ingest relation.
    """

    last_appeared: float | None
    """
    Seconds since a last appeared on the right side of the ingest relation.
    """

    distinct_left_side_count: int
    """
    The number of distinct items that appeared on the left side of the ingest relation when a was on the right side.
    """


async def infer(event: InferRequest, session: AsyncSession, actor: ActorRow | None) -> InferResponse:
    _require_permission(actor, "can_infer")

    right_id = event.id
    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)

    with tracer.start_as_current_span("infer.aggregate") as span:
        span.set_attribute("infer.right_id", right_id.hex())
        aggregate_row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(RelationAggregate.count), 0).label("right_side_count"),
                    func.min(RelationAggregate.first_occurred_at).label("first_seen_at"),
                    func.max(RelationAggregate.last_occurred_at).label("last_seen_at"),
                    func.count().label("distinct_left_side_count"),
                )
                .select_from(RelationAggregate)
                .where(col(RelationAggregate.right_id) == right_id)
            )
        ).one()

    with tracer.start_as_current_span("infer.right_side_count_last_7_days"):
        last_7_days_row = (
            await session.execute(
                select(func.count().label("right_side_count_last_7_days"))
                .select_from(RelationEvent)
                .where(col(RelationEvent.right_id) == right_id)
                .where(col(RelationEvent.occurred_at) >= seven_days_ago)
            )
        ).one()

    right_side_count = aggregate_row.right_side_count
    distinct_left_side_count = aggregate_row.distinct_left_side_count
    right_side_count_last_7_days = last_7_days_row.right_side_count_last_7_days

    first_seen_at = aggregate_row.first_seen_at
    first_appeared_seconds = (now - first_seen_at).total_seconds() if first_seen_at is not None else None
    last_seen_at = aggregate_row.last_seen_at
    last_appeared_seconds = (now - last_seen_at).total_seconds() if last_seen_at is not None else None

    return InferResponse(
        right_side_count=right_side_count,
        right_side_count_last_7_days=right_side_count_last_7_days,
        first_appeared=first_appeared_seconds,
        last_appeared=last_appeared_seconds,
        distinct_left_side_count=distinct_left_side_count,
    )


async def authenticate(api_key: str, session: AsyncSession) -> ActorRow | None:
    query = (
        select(ActorRow)
        .join(ApiKey, col(ApiKey.actor_id) == col(ActorRow.id))
        .where(col(ApiKey.key) == api_key)
        .where(col(ApiKey.revoked_at).is_(None))
    )
    return (await session.execute(query)).scalars().one_or_none()
