from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

#: SQLSTATE for unique_violation, which also covers a duplicate primary key.
UNIQUE_VIOLATION = "23505"

#: How long a request waits for a connection, whether opening one or taking it from the pool.
DATABASE_WAIT_SECONDS = 10


class Base(DeclarativeBase):
    pass


# What the stores receive. Spelling the Session parameter lets a type checker
# follow `with factory() as session` into the real Session API.
SessionFactory = sessionmaker[Session]


def create_db_engine(url: str) -> Engine:
    # pool_pre_ping drops connections that Render's PostgreSQL closed while idle.
    if not url.startswith("postgresql"):
        return create_engine(url, pool_pre_ping=True)
    # Without a connect timeout an unreachable host holds the request until the
    # operating system gives up, measured at 130 seconds on a registration: long
    # enough for the browser to look hung. Neon waking from idle needs a few seconds.
    return create_engine(
        url, pool_pre_ping=True, pool_timeout=DATABASE_WAIT_SECONDS,
        connect_args={"connect_timeout": DATABASE_WAIT_SECONDS},
    )


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, expire_on_commit=False)


def is_unique_violation(error: IntegrityError) -> bool:
    """Whether the database refused a duplicate, as opposed to any other rule.

    The stores turn a duplicate into a 409 ("name already taken"). A CHECK or
    foreign-key violation is a different problem, and reporting it as a
    duplicate sends whoever reads the 409 looking for a row that does not
    exist -- which is how a rejected zone outline once hid behind "Camera
    already has a zone named Safe zone".
    """
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate is not None:
        return bool(sqlstate == UNIQUE_VIOLATION)
    # SQLite's driver has no SQLSTATE; its message names the kind of constraint.
    message = str(original)
    return message.startswith("UNIQUE constraint failed") or "PRIMARY KEY" in message
