# Component A.2: Workspaces, membership and roles

# Component A: roles and invitations, staged specification v1

All routes are under /api on ports 8000, 8001, 8002. Use the staff contract
instruction.md for the full system. These are the Component A acceptance rules.


## Stage function dependencies

Functions used from earlier stages: open_db, authenticate_bearer, serialize_user, get_current_user.

Functions added by this stage: create_workspace, list_workspaces, list_workspace_members, get_workspace_role, join_channel, create_channel, set_member_role, transfer_ownership.

Implement these responsibilities with the named functions (or equivalent methods
with these names). Later stages reuse the same authoritative state and permission
logic; do not duplicate it in a way that changes earlier behavior.

## A2. Workspaces and membership

- POST /workspaces {slug,name} returns 201 {workspace,general_channel}. Validate
  slug with ^[a-z0-9-]{2,32}$; duplicates give 409. WorkspaceObj includes id,
  slug, name, owner_id, join_mode. Default join_mode is open. Auto-create a
  general channel and make the creator workspace owner and general member.
- GET /workspaces returns 200 {workspaces:[...]} containing only workspaces the
  caller owns or belongs to. An unrelated authenticated user cannot discover
  others' workspaces through this list.
- GET /workspaces/{slug}/members returns 200 {members:[{user_id,role,...}]} with
  every workspace member and their current role. Roles are owner/admin/member/guest.
- POST /channels/{id}/join returns 200 for allowed joins; unknown channel gives
  404. A private channel cannot be joined without the required invitation/access
  (403). Joining an open workspace's public channel auto-adds a nonmember as a
  workspace member. Repeat joins must not duplicate membership or reset roles.
- Support the channel creation prerequisites: POST /workspaces/{slug}/channels
  {name,is_private?,topic?} returns 201 {channel}; unique name per workspace,
  duplicate 409. ChannelObj includes id,workspace_id,name,is_private,is_dm,topic,
  is_archived. Default topic is empty. Respect name and topic constraints in the
  staff contract. Creator membership is recorded.

## A3. Role changes and ownership

- PATCH /workspaces/{slug}/members/{user_id} {role} returns
  200 {member:{user_id,role,...}} after an allowed change. Invalid roles give 400.
- Owner/admin may promote or demote member/guest to permitted non-owner roles.
  A regular member/guest cannot modify roles (403).
- An admin cannot modify another admin or the owner (403). The current owner's
  role cannot be modified by PATCH, even by that owner (403).
- Setting role=owner through PATCH is invalid (400); ownership has its own route.
  Test these constraints independently, not with overlapping invalid cases.
- POST /workspaces/{slug}/transfer_ownership {new_owner_id} is owner-only (403
  for other roles). Target must already be an admin in this workspace (400
  otherwise). On success the target is owner, old owner is admin, owner_id
  changes, and member-list roles agree. All changes are atomic and visible on
  other nodes; exactly one owner remains.
- Guests cannot create channels (403) or join public channels (403). Existing
  guest workspace membership must not be overwritten to member by auto-onboarding.
- Members may create public channels (201) but private creation is 403.
  Owners and admins may create private channels (201).
- Roles are scoped to a workspace. Authority in one workspace never grants
  authority in another. A token must not retain a demoted role in its claims.

## Gate

Follow the check order in /spec/README.md. Keep previous requirements working.
