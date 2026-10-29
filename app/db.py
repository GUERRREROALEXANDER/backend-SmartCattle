from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


# What the stores receive. Spelling the Session parameter lets a type checker
# follow `with factory() as session` into the real Session API.
SessionFactory = sessionmaker[Session]


def create_db_engine(url: str) -> Engine:
    # pool_pre_ping drops connections that Render's PostgreSQL closed while idle.
    return create_engine(url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, expire_on_commit=False)
