"""Dependency injection for FastAPI routes."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db


async def get_session(session: AsyncSession = Depends(get_db)) -> AsyncSession:
    return session
