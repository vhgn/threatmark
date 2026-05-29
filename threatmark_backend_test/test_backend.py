import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import col

from threatmark_backend.handler import (
    InferRequest,
    IngestRequest,
    authenticate,
    infer,
    ingest,
)
from threatmark_backend.schema import ActorRow, ApiKey, RelationAggregate, RelationEvent, metadata
from threatmark_backend.utils import parse


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    os.environ.get("DATABASE_URL", "postgresql+asyncpg://localhost/postgres"),
)


class AuthorizationAndParsingTest(unittest.IsolatedAsyncioTestCase):
    async def test_authenticate_accepts_active_database_key(self) -> None:
        actor_id = uuid.uuid4()

        actor = await self._authenticate_with_seed(
            "alpha-key",
            actor_id=actor_id,
            key="alpha-key",
            can_ingest=True,
            can_infer=False,
        )

        self.assertIsNotNone(actor)
        assert actor is not None
        self.assertEqual(actor.id, actor_id)
        self.assertTrue(actor.can_ingest)
        self.assertFalse(actor.can_infer)

    async def test_authenticate_rejects_unknown_database_key(self) -> None:
        actor = await self._authenticate_with_seed(
            "beta-key",
            actor_id=uuid.uuid4(),
            key="alpha-key",
            can_ingest=True,
            can_infer=True,
        )

        self.assertIsNone(actor)

    async def test_authenticate_rejects_revoked_database_key(self) -> None:
        actor = await self._authenticate_with_seed(
            "alpha-key",
            actor_id=uuid.uuid4(),
            key="alpha-key",
            can_ingest=True,
            can_infer=True,
            revoked_at=datetime.now(UTC),
        )

        self.assertIsNone(actor)

    async def test_ingest_permission_is_required(self) -> None:
        actor = ActorRow(can_ingest=False, can_infer=True)

        # Permission is checked before the session is touched, so passing None
        # is sufficient to exercise the guard.
        with self.assertRaisesRegex(PermissionError, "not allowed to ingest"):
            await ingest(
                IngestRequest(
                    left_id=b"left-id-00000001",
                    right_id=b"right-id-0000000",
                    occurred_at=datetime.now(UTC),
                ),
                session=None,  # type: ignore[arg-type]
                actor=actor,
            )

    def test_parse_ingest_request(self) -> None:
        request = parse(
            IngestRequest,
            {
                "left_id": "left-id-00000001",
                "right_id": "right-id-0000000",
                "occurred_at": "2026-05-28T08:15:30Z",
            },
        )

        self.assertEqual(request.left_id, b"left-id-00000001")
        self.assertEqual(request.right_id, b"right-id-0000000")
        self.assertEqual(request.occurred_at, datetime(2026, 5, 28, 8, 15, 30, tzinfo=UTC))

    def test_parse_rejects_invalid_member_id_length(self) -> None:
        with self.assertRaises(ValidationError):
            parse(
                InferRequest,
                {
                    "id": "too-short",
                },
            )

    async def _authenticate_with_seed(
        self,
        provided_key: str,
        *,
        actor_id: uuid.UUID,
        key: str,
        can_ingest: bool,
        can_infer: bool,
        revoked_at: datetime | None = None,
    ) -> ActorRow | None:
        schema_name = f"threatmark_test_{uuid.uuid4().hex}"
        engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)

        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                await connection.run_sync(metadata.create_all)

            async with sessionmaker() as session:
                await session.execute(
                    insert(ActorRow).values(
                        id=actor_id,
                        can_ingest=can_ingest,
                        can_infer=can_infer,
                    )
                )
                await session.execute(
                    insert(ApiKey).values(key=key, actor_id=actor_id, revoked_at=revoked_at)
                )
                await session.commit()

                return await authenticate(provided_key, session)
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))

            await engine.dispose()


class IngestionInferenceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.schema_name = f"threatmark_test_{uuid.uuid4().hex}"
        self.engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"server_settings": {"search_path": self.schema_name}},
        )
        self.sessionmaker = async_sessionmaker(bind=self.engine, expire_on_commit=False)
        self.member_ids: set[bytes] = set()

        async with self.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema_name}"'))
            await connection.run_sync(metadata.create_all)

    async def asyncTearDown(self) -> None:
        if self.member_ids:
            async with self.sessionmaker() as session:
                await session.execute(
                    delete(RelationEvent).where(
                        col(RelationEvent.left_id).in_(self.member_ids)
                        | col(RelationEvent.right_id).in_(self.member_ids)
                    )
                )
                await session.commit()

        async with self.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{self.schema_name}" CASCADE'))

        await self.engine.dispose()

    async def test_ingest_persists_events_and_infer_returns_statistics(self) -> None:
        now = datetime.now(UTC)
        left_a = self._member_id()
        left_b = self._member_id()
        right = self._member_id()
        unrelated_right = self._member_id()

        actor = ActorRow(can_ingest=True, can_infer=True)

        async with self.sessionmaker() as session:
            await ingest(IngestRequest(left_id=left_a, right_id=right, occurred_at=now - timedelta(days=1)), session, actor)
            await ingest(IngestRequest(left_id=left_a, right_id=right, occurred_at=now - timedelta(days=3)), session, actor)
            await ingest(IngestRequest(left_id=left_b, right_id=right, occurred_at=now - timedelta(days=8)), session, actor)
            await ingest(IngestRequest(left_id=left_a, right_id=unrelated_right, occurred_at=now), session, actor)

            aggregate_rows = (
                await session.execute(
                    select(RelationAggregate).where(col(RelationAggregate.right_id) == right)
                )
            ).scalars().all()
            output = await infer(InferRequest(id=right), session, actor)

        aggregates_by_left_id = {row.left_id: row for row in aggregate_rows}
        self.assertEqual(len(aggregates_by_left_id), 2)
        self.assertEqual(aggregates_by_left_id[left_a].count, 2)
        self.assertEqual(aggregates_by_left_id[left_a].first_occurred_at, now - timedelta(days=3))
        self.assertEqual(aggregates_by_left_id[left_a].last_occurred_at, now - timedelta(days=1))
        self.assertEqual(aggregates_by_left_id[left_b].count, 1)
        self.assertEqual(aggregates_by_left_id[left_b].first_occurred_at, now - timedelta(days=8))
        self.assertEqual(aggregates_by_left_id[left_b].last_occurred_at, now - timedelta(days=8))
        self.assertEqual(output.right_side_count, 3)
        self.assertEqual(output.right_side_count_last_7_days, 2)
        self.assertEqual(output.distinct_left_side_count, 2)
        self.assertIsNotNone(output.first_appeared)
        self.assertIsNotNone(output.last_appeared)
        assert output.first_appeared is not None
        assert output.last_appeared is not None
        self.assertGreater(output.first_appeared, timedelta(days=8).total_seconds() - 60)
        self.assertLess(output.first_appeared, timedelta(days=8).total_seconds() + 60)
        self.assertGreater(output.last_appeared, timedelta(days=1).total_seconds() - 60)
        self.assertLess(output.last_appeared, timedelta(days=1).total_seconds() + 60)

    def _member_id(self) -> bytes:
        value = uuid.uuid4().bytes
        self.member_ids.add(value)
        return value
