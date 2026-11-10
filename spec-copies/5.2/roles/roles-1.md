# Component A.1: Authentication and API conventions

# Component A: roles and invitations, staged specification v1

All routes are under /api on ports 8000, 8001, 8002. Use the staff contract
instruction.md for the full system. These are the Component A acceptance rules.


## Stage function dependencies

Functions used from earlier stages: none (first stage; standard library/framework/database primitives only).

Functions added by this stage: open_db, initialize_schema, serialize_user, authenticate_bearer, register_user, login_user, get_current_user.

Implement these responsibilities with the named functions (or equivalent methods
with these names). Later stages reuse the same authoritative state and permission
logic; do not duplicate it in a way that changes earlier behavior.

## A1. API and authentication

- Authenticated routes require Authorization: Bearer <token>. Missing/invalid
  tokens return 401. Enforce authorization server-side, including reads.
- JSON response envelopes use the resource keys specified below, not raw lists
  or a generic data field. Resource IDs are stable across nodes. Timestamps, when
  returned, are ISO 8601 UTC. Distinguish 400 validation, 401 unauthenticated,
  403 forbidden, 404 unknown, 409 duplicate. Successful reads/updates return 200,
  creates 201, deletes 204 unless a specific endpoint overrides that default.
- POST /auth/register accepts {username,password,display_name?}, no email field
  required. Usernames contain letters/digits/underscore; reject spaces and other
  special characters with 400. Password length must be at least 8 (400 otherwise).
  Return 201 {user,token}, token string length >=16; duplicate username gives 409.
  UserObj includes id, username, display_name, timezone, avatar_url, status_text,
  status_emoji. Store passwords securely; do not expose them in user objects.
- POST /auth/login {username,password} returns 200 {user,token}; wrong password
  gives 401. GET /auth/me returns 200 {user} for the actual token holder; missing
  or invalid token gives 401. Registration and tokens work across all nodes.

## Gate

Follow the check order in /spec/README.md. Keep previous requirements working.
