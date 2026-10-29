# Authentication

Bearer tokens over the shared `users` table. Before this, the backend had no
authentication at all and the frontend kept a demo session in `localStorage`
with unsalted SHA-256 hashes -- which is what ran in the deployment.

## Switching it on

```
JWT_SECRET=<48 random bytes, url-safe>
```

Generate one with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**While `JWT_SECRET` is empty, authentication is off.** `/api/auth/*` answers
`503`, nothing is protected, and `create_app` logs a warning at startup. That
is deliberate: a deployment that has not configured it keeps serving the AI
ingestion and the dashboard rather than failing to boot.

Rejected at startup if shorter than 32 bytes. RFC 7518 puts the floor for an
HMAC key at the digest size, and a shorter key still signs and verifies -- the
tokens are just cheaper to forge, which never announces itself.

**Changing it signs everyone out.** Tokens are verified against the secret and
nothing else; there is no session table to consult.

## The endpoints

| Endpoint | Who | What |
| --- | --- | --- |
| `POST /api/auth/register` | anyone | Creates an account and returns a token |
| `POST /api/auth/login` | anyone | Returns a token |
| `GET /api/auth/me` | signed in | The current account |

The first account registered becomes the `owner`; everyone after is a
`worker`. Somebody has to be the owner and there is no other channel to grant
it. Promoting an account is not an API operation yet -- it is an `UPDATE` on
`users.role`.

## What each role may do

| Area | Worker | Owner |
| --- | --- | --- |
| Read animals, events, cameras, metrics | yes | yes |
| Read zones | yes | yes |
| Register / update / deactivate an animal | no | yes |
| Create / update / delete a zone | no | yes |
| Save a video analysis | yes | yes |

Saving a video analysis is deliberately not owner-only: analysing an uploaded
clip is the everyday job of whoever is watching the herd.

Zones are the widest-effect write in the API, which is why they are owner only:
the zone is what every detection is compared against, so a bad one either
floods the farm with false alerts or stops reporting real escapes.

## Using a token

```
Authorization: Bearer <access_token>
```

`expires_in` comes back with the token, in seconds. One hour by default
(`ACCESS_TOKEN_MINUTES`). There is no refresh token, so that is the whole
session: when it expires, sign in again.

## The two kinds of credential

They are not interchangeable, and the difference is about who the caller is.

| Credential | Caller | Endpoints |
| --- | --- | --- |
| `Authorization: Bearer` | a person, through the browser | the herd, the zones, saving an analysis |
| `X-API-Key` | the AI service | `POST /api/ai/events`, `PUT /api/ai/cameras/{id}/status`, `PUT /api/animals/{tag}/detection` |

`AI_API_KEY` cannot live in the frontend: `VITE_` variables are public in the
browser bundle, and the frontend's own `.env.example` says so. That is why
`POST /api/videos/analyses` -- called by the browser -- is guarded by a user
token and not by the shared key, and why it stayed open until there were
users to authenticate.

## Decisions worth knowing

**A failed login never says why.** A wrong password, an unknown address and a
deactivated account all return the same `401` with the same message.
Distinguishing them turns login into a way to find out which addresses have
accounts.

**The timing matches too.** When no user is found, the password is still
verified against a throwaway hash, so a missing address costs the same bcrypt
work as a wrong one. Without that, the response time reveals what the message
conceals.

**The account is loaded on every request.** The token's claims are signed and
cannot be forged, but they are a snapshot. Trusting the role from the token
would let a deactivated account keep working until its token expired, which is
the one case deactivation exists for.

**Lockout is keyed on the address, not the IP.** The farm staff share a
connection, so an IP key would lock all of them out together while not slowing
an attacker working through a list of addresses. The cost is real: somebody
who knows an address can lock its owner out for `LOGIN_LOCKOUT_SECONDS`. For a
handful of accounts on one farm that is the better trade, and it stops being
so if this ever serves accounts it does not know.

**The counters are per process.** A restart clears them. Surviving a restart
needs a table and a write per failed attempt, and what this blocks is a fast
guessing loop rather than a patient one.

## What is not here yet

- **No refresh tokens.** A session ends when the access token expires.
- **No revocation.** A stolen token works until it expires; the only lever is
  changing `JWT_SECRET`, which signs everyone out. Tokens carry a `jti` so a
  revocation list has something to name when one is added.
- **No password reset.** There is no email sender.
- **No role management endpoint.** Promoting a worker is a database `UPDATE`.
- **No audit log.** `users.last_login_at` records the last success, not the
  failures or where they came from.
