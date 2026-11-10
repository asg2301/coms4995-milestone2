# Component B.1: Channel management

Read /spec/channels/common.md; its API conventions and correctness guidance
apply to every stage. Read the staff instruction.md for supporting API shapes.

## Function dependencies

Functions used from earlier stages (including A): authenticate_bearer, get_workspace_role, join_channel, create_channel.

Functions added by this stage: serialize_channel, serialize_user, get_workspace_detail, set_channel_topic, leave_channel, list_channel_members, archive_channel, unarchive_channel.

Implement these responsibilities through the named functions or equivalent
methods with these names. Stage 2/3 can introduce the event storage/allocation
primitives needed for event-based read state; stage 4 completes their public
WebSocket/IRC behavior. Reuse shared state and authorization, rather than
copying implementations that diverge.

<details>
<summary>Channel management (10 lines)</summary>

- `ChannelObj` carries at least `{id, workspace_id, name, is_private, is_dm, topic, is_archived}`.
- `topic` is a string up to 250 chars (longer → `400`) and defaults to `""` on a freshly created
  channel — the `POST /channels` body does not carry a `description` field that auto-populates
  `topic`.
- `POST /api/workspaces/{slug}/channels` — body `{name, is_private?, topic?}`. `name` kebab-case
  <=32 chars. Returns `201 {channel}`. Duplicate `name` within the workspace → `409`.
- `GET /api/workspaces/{slug}` — `200 {workspace, channels, read_state}`. Private channels the
  caller is not a member of are omitted unless explicitly requested. `?include_archived=1`
  includes archived channels (`true` may be accepted as an alias).
- `PATCH /api/channels/{id}` — accepts `{topic}` and returns `200 {channel}`; the caller must be
  a channel member or the workspace owner.
- `POST /api/channels/{id}/leave` — leaves idempotently and returns `200 {channel}`.
- `GET /api/channels/{id}/members` — returns `200 {members: [UserObj, ...]}`.
- `UserObj` carries at least `{id, username, display_name, timezone, avatar_url, status_text,
  status_emoji}`.
- `POST /api/channels/{id}/archive` and `POST /api/channels/{id}/unarchive` — workspace-owner
  only; return `200 {channel}`.
- Writes to an archived channel return `423`. Unarchive restores write access.

</details>

## Gate

Follow the check order in /spec/README.md. Keep previous requirements working.
