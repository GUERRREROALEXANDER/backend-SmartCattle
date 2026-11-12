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
| Read animals, events, cameras, metrics, zones, farms, lots | yes | yes |
| Register an animal | no | yes |
| Update or deactivate an animal | no | yes |
| Create, update or delete a zone | no | yes |
| Register a farm | no | yes |
| Change or delete a farm, or its lots | no | only the owner who registered it |
| Save a video analysis | yes | yes |
| Read, acknowledge or resolve security alerts; open the live stream | yes | yes |
| Watch a camera's live video | yes | yes |
| Change their own password | yes | yes |
| Link their own WhatsApp number and switch its notices on or off | yes | yes |
| List, create, promote, demote or deactivate accounts | no | yes |

Farms add a second check on top of the role. Being an owner allows
configuring farms; it does not allow reconfiguring someone else's. A farm
records the account that registered it in `owner_id`, and every write to the
farm or its lots compares that with the caller. Another owner gets `403`, for
the same reason a worker does: farms are readable by every signed-in user, so
a `404` would hide nothing.

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

The first account registered becomes the owner. After that, any owner manages
accounts through `/api/users`: creating them with a role, promoting a worker,
demoting an owner, deactivating anyone. Workers cannot reach those endpoints,
including to read the list, which is the staff's email addresses.

```http
PATCH /api/users/2
{"role": "owner"}
```

The change takes effect on that user's next request, because the role is read
from the database rather than from their token.

### At least one active owner remains

Demoting or deactivating the last active owner answers `409`, including when
owners try it on themselves. With no owner left nobody could grant the role
again through the API, and the farm's configuration would be frozen until
someone edited the database by hand. An inactive owner does not count as the
one remaining.

The check locks the other owners' rows in the same transaction. Two owners
demoting each other at the same moment would otherwise each see the other
still in place, and both updates would succeed.
