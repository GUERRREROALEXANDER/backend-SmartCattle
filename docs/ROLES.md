# Roles

`users.role` is `owner` or `worker`. The column has been in the shared schema
from the start; until the herd and zone endpoints existed, nothing enforced
it.

## Where it is enforced

**In the backend, on the write endpoints.** That is the only place that
counts. The frontend has a `can(role, permission)` helper whose own comment
says it is for the interface only, and the database records the role without
constraining anything -- `docs/diccionario/users.md` in the database
repository says as much.

## The rule

A worker reads everything and changes nothing about the farm's
**configuration**; an owner does both.

| | Worker | Owner |
| --- | --- | --- |
| Read animals, events, cameras, metrics, zones | yes | yes |
| Register an animal | no | yes |
| Update or deactivate an animal | no | yes |
| Create, update or delete a zone | no | yes |
| Save a video analysis | yes | yes |

The split is between watching the farm and defining it. Registering an animal
is deciding what the herd is; configuring a zone is deciding what counts as an
escape. Analysing a clip someone just recorded is neither -- it is the
everyday work -- so it is open to both.

## Why 403 and not 404

A worker calling an owner-only endpoint gets `403` with a message naming the
role. Hiding the endpoint behind a `404` would buy nothing: the caller is
already authenticated and known, and a permissions problem presented as a
wrong URL sends them looking in the wrong place. The frontend can also say
something useful with the message.

## Why the role is re-read from the database

The token carries a `role` claim, and nothing reads it for authorisation. The
account is loaded on every request and the stored role is used.

The claim is signed and cannot be forged, but it is a snapshot: demoting an
owner would not take effect until their token expired, up to an hour later.
The claim is kept for debugging and for a future revocation list, not as the
authority.

## Granting owner

The first account registered becomes the owner. There is no endpoint to change
a role, which is a gap rather than a decision -- it needs a rule about who may
grant what, and that question has not been answered yet.

Until then it is a database update:

```sql
UPDATE users SET role = 'owner' WHERE email = 'ana@finca.example';
```

The change takes effect on the user's next request, because the role is read
from the database rather than from their token.
