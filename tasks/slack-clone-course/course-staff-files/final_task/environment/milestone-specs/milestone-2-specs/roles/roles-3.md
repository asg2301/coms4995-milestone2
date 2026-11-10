# Component A.3: Invitations and join mode

# Component A: roles and invitations, staged specification v1

All routes are under /api on ports 8000, 8001, 8002. Use the staff contract
instruction.md for the full system. These are the Component A acceptance rules.


## Stage function dependencies

Functions used from earlier stages: open_db, authenticate_bearer, get_workspace_role, list_workspace_members, join_channel.

Functions added by this stage: create_invitation, list_active_invitations, accept_invitation, revoke_invitation, update_workspace.

Implement these responsibilities with the named functions (or equivalent methods
with these names). Later stages reuse the same authoritative state and permission
logic; do not duplicate it in a way that changes earlier behavior.

## A4. Invitations

- POST /workspaces/{slug}/invitations is admin/owner only (403 for member/guest).
  Accept optional expires_in_seconds,max_uses,invited_username,invited_user_id.
  Return 201 {invitation} containing a nonempty code.
- Default max_uses=1. A successful POST /invitations/{code}/accept consumes one
  use and adds the accepting user to the workspace; success is 200. Explicit
  multi-use invitations allow that many successful uses; exhausted codes give 404.
- Targeted invitations are single-use, including when max_uses is supplied.
  Resolve invited_username to the intended account. A user other than the
  invited_user_id target gets 403, without consuming the invitation's use.
- Unknown, expired, exhausted, revoked codes give 404. Expiry must be checked
  against server time on acceptance. State is shared across nodes, and use
  consumption is atomic so concurrent acceptance cannot overspend max_uses.
- GET /workspaces/{slug}/invitations returns 200 {invitations:[...]} for admins
  and owners, listing active invitations; member/guest callers get 403.
- DELETE /workspaces/{slug}/invitations/{code} is admin/owner only, returning
  200 {"deleted":true}. Revocation takes effect on every node immediately.

## A5. Join mode and settings

- PATCH /workspaces/{slug} accepts any subset of {name,join_mode}, admin/owner
  only; success 200 {workspace}. Allowed join_mode values: open, invite_only.
  Reject invalid values (400). A partial patch preserves fields not supplied.
- In open mode, an authenticated nonmember joining a public channel is added
  as workspace member and receives 200. Private-channel restrictions still apply.
- In invite_only mode a workspace nonmember gets 403 on public-channel join.
  After redeeming a valid workspace invitation the new member can join (200).
  Changing mode does not remove existing members or prevent their allowed joins.
- Name/mode changes, membership, role updates, invitation exhaustion/revocation,
  and ownership transfers must remain consistent across ports 8000/8001/8002.

## Gate

Follow the check order in /spec/README.md. Keep previous requirements working.
