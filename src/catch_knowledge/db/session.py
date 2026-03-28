from sqlalchemy import create_engine, inspect, text
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
    _run_lightweight_migrations(engine)


def _run_lightweight_migrations(engine) -> None:
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "post_analysis" not in table_names and "raw_posts" not in table_names:
        return

    statements = []
    if "post_analysis" in table_names:
        analysis_columns = {column["name"] for column in inspector.get_columns("post_analysis")}
        if "interview_questions" not in analysis_columns:
            statements.append("ALTER TABLE post_analysis ADD COLUMN interview_questions JSON")

    if "raw_posts" in table_names:
        raw_columns = {column["name"] for column in inspector.get_columns("raw_posts")}
        if "raw_source_text" not in raw_columns:
            statements.append("ALTER TABLE raw_posts ADD COLUMN raw_source_text TEXT")
        if "raw_image_text" not in raw_columns:
            statements.append("ALTER TABLE raw_posts ADD COLUMN raw_image_text TEXT")
        if "image_urls" not in raw_columns:
            statements.append("ALTER TABLE raw_posts ADD COLUMN image_urls JSON")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
