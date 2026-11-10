# Component B.3: Mentions and read state

Read /spec/channels/common.md; its API conventions and correctness guidance
apply to every stage. Read the staff instruction.md for supporting API shapes.

## Function dependencies

Functions used from earlier stages (including A): create_message, serialize_message, get_workspace_detail, authenticate_bearer.

Functions added by this stage: resolve_mentions, get_read_state, advance_read_cursor.

Implement these responsibilities through the named functions or equivalent
methods with these names. Stage 2/3 can introduce the event storage/allocation
primitives needed for event-based read state; stage 4 completes their public
WebSocket/IRC behavior. Reuse shared state and authorization, rather than
copying implementations that diverge.

<details>
<summary>Mentions (5 lines)</summary>

- `mentions` is the list of resolved user ids referenced as `@username` (no mid-word matches, no
  self-mentions, deduped).
- Mentions resolve `@username` to user ids and populate `mentions`.
- Email-shaped or in-word mentions do not match; `@username` referencing a user who is not a
  member of the channel's workspace is excluded.
- `@channel` expands to every channel member except the author
- IDs from all mention forms are deduplicated, and mentioning oneself never increments one's own
  `mention_count`.

</details>

<details>
<summary>Read state (3 lines)</summary>

- `GET /api/channels/{id}/read` returns `200 {read_state:{channel_id, last_read_event_id,
  unread_count, mention_count}}`.
- `POST` on the same route accepts `{last_read_event_id}`, never moves the per-(user, channel)
  cursor backwards, and returns the updated `{read_state}`.
- Workspace detail embeds the same shape.

</details>

## Gate

Follow the check order in /spec/README.md. Keep previous requirements working.
