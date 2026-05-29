import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import engine, get_session
from .schema import ActorRow, metadata
from .handler import (
    InferRequest,
    InferResponse,
    IngestRequest,
    authenticate,
    infer,
    ingest,
)
from .observability import configure_tracing, instrument

# Tracing must be configured before the app is instrumented below.
configure_tracing()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create any missing tables from the SQLModel schema on startup. This is a
    # create-if-not-exists step (not a full migration): it adds tables that
    # don't yet exist but does not alter columns on tables that already do.
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)

    yield
    # Dispose the pool on shutdown so connections are closed cleanly.
    await engine.dispose()


app = FastAPI(title="Threatmark", lifespan=lifespan)

# Auto-instrument HTTP requests and SQL statements (spans nest under the manual
# per-field spans created in handler.infer).
instrument(app, engine)


# Dependencies

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_actor(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> ActorRow:
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No API key in `Authorization` header",
        )

    actor = await authenticate(authorization, session)
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return actor


ActorDep = Annotated[ActorRow, Depends(get_actor)]


# Endpoints

@app.post("/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_handler(
    ingest_request: IngestRequest,
    session: SessionDep,
    actor: ActorDep,
) -> Response:
    await ingest(ingest_request, session, actor)
    return Response(status_code=status.HTTP_201_CREATED)


@app.post("/infer")
async def infer_handler(
    infer_request: InferRequest,
    session: SessionDep,
    actor: ActorDep,
) -> InferResponse:
    return await infer(infer_request, session, actor)


# Error handlers

@app.exception_handler(ValidationError)
async def handle_validation_error(request, error: ValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "Validation failed", "details": error.errors()},
    )


@app.exception_handler(ValueError)
async def handle_value_error(request, error: ValueError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": str(error)},
    )


@app.exception_handler(PermissionError)
async def handle_permission_error(request, error: PermissionError):
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"error": str(error)},
    )


def run() -> None:
    """Entry point for ``uv run serve``."""
    import uvicorn

    uvicorn.run(
        "threatmark_backend.server:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        reload=bool(os.environ.get("RELOAD")),
    )
