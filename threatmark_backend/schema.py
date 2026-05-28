"""SQLModel schema for append-only relation ingestion.

These models are mapped tables (``table=True``). ``metadata`` is re-exported so
callers can create every table at once via ``metadata.create_all`` (used both on
app startup and in tests).
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, LargeBinary, func
from sqlmodel import Field, SQLModel

# Re-export so callers don't need to know it lives on SQLModel.
metadata = SQLModel.metadata


class RelationEvent(SQLModel, table=True):
    """One append-only (left, right) relation observation."""

    __tablename__ = "relation_events"  # pyright: ignore[reportAssignmentType]

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    left_id: bytes = Field(sa_column=Column(LargeBinary(16), nullable=False))
    right_id: bytes = Field(sa_column=Column(LargeBinary(16), nullable=False))
    occurred_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ingested_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )

    __table_args__ = (
        Index("ix_relation_events_right_id_occurred_at", "right_id", "occurred_at"),
        Index("ix_relation_events_occurred_at", "occurred_at"),
    )


class RelationAggregate(SQLModel, table=True):
    """Running per-(left, right) rollup maintained via upsert on ingest.

    ``(left_id, right_id)`` is the composite primary key, which also serves as
    the upsert conflict target.
    """

    __tablename__ = "relation_aggregates"  # pyright: ignore[reportAssignmentType]

    left_id: bytes = Field(sa_column=Column(LargeBinary(16), primary_key=True))
    right_id: bytes = Field(sa_column=Column(LargeBinary(16), primary_key=True))
    first_occurred_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    last_occurred_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    count: int = Field(sa_column=Column(BigInteger, nullable=False))

    __table_args__ = (Index("ix_relation_aggregates_right_id", "right_id"),)


class ActorRow(SQLModel, table=True):
    """Authenticated principal and its coarse permissions.

    Named ``ActorRow`` to avoid colliding with the domain ``Actor`` dataclass in
    ``context.py``; the table is still ``actors``.
    """

    __tablename__ = "actors"  # pyright: ignore[reportAssignmentType]

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    can_ingest: bool
    can_infer: bool


class ApiKey(SQLModel, table=True):
    """Bearer credential resolving to an :class:`ActorRow`."""

    __tablename__ = "api_keys"  # pyright: ignore[reportAssignmentType]

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    key: str = Field(index=True, unique=True)
    actor_id: uuid.UUID = Field(foreign_key="actors.id", index=True)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


__all__ = [
    "metadata",
    "RelationEvent",
    "RelationAggregate",
    "ActorRow",
    "ApiKey",
]
