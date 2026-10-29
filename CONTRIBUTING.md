# Working on this backend

## Before you push

```powershell
ruff check .
mypy
pytest -q
```

CI runs the same three, plus an Alembic round trip against an empty database.
All three have to pass; none of them is advisory.

## Where things go

| You are adding | It goes in |
| --- | --- |
| A request or response shape | `app/schemas/` |
| A table mapping | `app/models.py` |
| Storage, memory and SQL | `app/services/` |
| Endpoints | `app/routes/` |
| Anything resolved from `app.state` | `app/dependencies.py` |
| Password or token handling | `app/core/security.py` |

**A router never imports from another router.** That is how `cameras.py` ended
up depending on `events.py` and `get_clock` existed in two copies. Shared
providers live in `app/dependencies.py`.

## Every store has two implementations

`InMemory*` and `Sql*`, behind a `Protocol`. The in-memory one is not a test
double: it is what runs when `DATABASE_URL` is unset, which is the default.
A feature that only works with a database is a feature that does not work on a
fresh checkout.

Keep them behaviourally identical, ordering included. Two stores returning
different orders for the same query is a difference that only shows up in
production, where exactly one of them is running.

## Shared tables versus ours

`cameras`, `events`, `users`, `animals`, `zones` and `alerts` belong to the
database repository and are written by more than one service. `video_analyses`
and the `cameras.reported_status` group are ours.

For a shared table:

- **Most columns are nullable**, because no single writer has every field. Our
  response schemas require more than the table does, which is why the queries
  filter rows they cannot represent -- see `_listable_conditions()` in
  `app/services/event_store.py`.
- **One unrepresentable row must never fail a whole response.** That was a real
  500: a single event with a NULL `confidence` blanked the entire events page.
- A migration here adds columns and never drops a shared one. Coordinate a
  rename with the other services first; the last one broke all three at once.

## Migrations

```powershell
alembic revision -m "what it does"
alembic upgrade head
alembic downgrade base   # it has to go back down too
```

Revisions `0004` onwards follow one shape: return early if the table already
exists, because on PostgreSQL it came from `database/schema.sql`, and create it
otherwise so SQLite -- the test database -- has it too. Copy the shared
schema's CHECK constraints when you do, or the test database accepts rows
production refuses, which is the worst direction for that difference to run.

`downgrade` refuses to drop a shared table on PostgreSQL. It holds other
services' data.

## Time

Every timestamp is timezone-aware UTC. Nothing calls `datetime.now()` outside
`create_app`: handlers take the clock through `Depends(get_clock)` and stores
receive the instant as an argument. A request reads the clock once, so what
gets stored and what gets derived from it agree.

## Tests

- Name the behaviour, not the method. `test_an_unlocated_animal_is_still_listed`
  survives a rename; `test_list_animals_2` says nothing when it fails.
- Assert the boundary from both sides. A threshold tested on one side passes
  when it is off by one.
- The suite runs bcrypt at cost 4 through an autouse fixture, and
  `test_security.py` pins the production value by reading the source. If you
  lower a security constant for tests, pin the real one in the same change.
- Dispose engines through the `engine_for` fixture. Warnings are errors, and a
  leaked connection fails the run at a point unrelated to the cause.

## Secrets

`AI_API_KEY` guards the AI service's endpoints. `JWT_SECRET` signs user
tokens. Neither may reach the frontend: `VITE_` variables are public in the
browser bundle. That is why a browser-facing endpoint is guarded by a bearer
token and never by `AI_API_KEY`.
