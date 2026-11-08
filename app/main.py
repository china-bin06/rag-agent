from contextlib import asynccontextmanager, closing

from fastapi import FastAPI
from qdrant_client import QdrantClient

from app.agent.session import ClarificationStore
from app.core.auth import router as auth_router
from app.core.config import settings
from app.db.database import SessionLocal
from app.rag.vector import QDRANT_PATH, validate_vector_index
from app.services.rag_api import router as rag_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.clarifications = ClarificationStore()
    app.state.qdrant = None
    if not (QDRANT_PATH / "meta.json").is_file():
        yield
        return
    with closing(QdrantClient(path=str(QDRANT_PATH))) as client:
        with SessionLocal() as db:
            validate_vector_index(client, db)
        app.state.qdrant = client
        yield
    app.state.qdrant = None


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(rag_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
