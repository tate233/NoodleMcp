from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from catch_knowledge.config import Settings

from .base import Base


def create_engine_from_settings(settings: Settings):
    return create_engine(settings.database_url, future=True)


def create_session_factory(settings: Settings) -> sessionmaker:
    engine = create_engine_from_settings(settings)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def create_tables(settings: Settings) -> None:
    engine = create_engine_from_settings(settings)
    Base.metadata.create_all(engine)
