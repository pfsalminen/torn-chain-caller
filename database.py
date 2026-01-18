import os
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./chain_claims.db")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


class Faction(Base):
    __tablename__ = "factions"

    id = Column(Integer, primary_key=True)  # torn faction ID
    name = Column(String, nullable=False)


class ChainClaim(Base):
    __tablename__ = "chain_claims"

    id = Column(Integer, primary_key=True, autoincrement=True)
    faction_id = Column(Integer, ForeignKey("factions.id"), nullable=False)
    hit_number = Column(Integer, nullable=False)
    claimed_by = Column(Integer, nullable=False)  # torn user ID
    claimed_by_name = Column(String, nullable=False)
    claimed_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "faction_id": self.faction_id,
            "hit_number": self.hit_number,
            "claimed_by": self.claimed_by,
            "claimed_by_name": self.claimed_by_name,
            "claimed_at": self.claimed_at.isoformat() if self.claimed_at else None,
        }


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
