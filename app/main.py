from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.jobs.decay import create_scheduler
from app.routers import memories


@asynccontextmanager
async def lifespan(app: FastAPI):
    # In-process scheduler. With multiple workers each starts one; the job's
    # advisory lock keeps runs from overlapping.
    scheduler = create_scheduler()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Recall", lifespan=lifespan)
app.include_router(memories.router)
