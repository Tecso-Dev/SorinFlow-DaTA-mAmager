"""
SorinFlow Divar Scraper - Database Connection
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
import redis.asyncio as redis
from typing import AsyncGenerator
from app.config import get_settings

settings = get_settings()

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    poolclass=NullPool,
    future=True
)

# Create async session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


class Base(DeclarativeBase):
    """Base class for all models"""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Redis connection
redis_client = None


async def get_redis() -> redis.Redis:
    """Get Redis client connection"""
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
    return redis_client


async def close_redis():
    """Close Redis connection"""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None


async def init_db():
    """Initialize database tables and seed default super_admin if needed."""
    async with engine.begin() as conn:
        from app.models import property, cookie, scraping_job, lead, user, crm_models
        await conn.run_sync(Base.metadata.create_all)
        # Add 2FA columns to users table if they don't exist yet
        await _migrate_users_totp(conn)

    await _seed_super_admin()


async def _migrate_users_totp(conn):
    """Idempotently add totp_secret / totp_enabled columns to users table."""
    try:
        from sqlalchemy import text
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='users' AND column_name='totp_enabled'"
        ))
        if result.fetchone() is None:
            await conn.execute(text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(64), "
                "ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE"
            ))
    except Exception:
        pass  # non-PostgreSQL or column already exists


async def _seed_super_admin():
    """Create the default super_admin account if no users exist."""
    from app.models.user import User
    from app.auth.jwt import get_password_hash
    from app.config import get_settings

    cfg = get_settings()

    async with async_session_maker() as session:
        result = await session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(User)
        )
        if result.scalars().first():
            return  # users already exist

        admin = User(
            username=cfg.super_admin_username,
            full_name="Super Admin",
            hashed_password=get_password_hash(cfg.super_admin_password),
            role="super_admin",
            is_active=True,
        )
        session.add(admin)
        await session.commit()
        from loguru import logger
        logger.info(
            f"Default super_admin created: username='{cfg.super_admin_username}'"
        )


async def close_db():
    """Close database connections"""
    await engine.dispose()
