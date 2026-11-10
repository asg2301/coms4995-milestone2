# Component B.2: Messages, history, search and pins

Read /spec/channels/common.md; its API conventions and correctness guidance
apply to every stage. Read the staff instruction.md for supporting API shapes.

## Function dependencies

Functions used from earlier stages (including A): authenticate_bearer, get_workspace_role, serialize_channel, join_channel.

Functions added by this stage: serialize_message, create_message, encode_cursor, validate_cursor, list_channel_messages, list_thread_replies, search_messages, pin_message, unpin_message, list_pins.

Implement these responsibilities through the named functions or equivalent
methods with these names. Stage 2/3 can introduce the event storage/allocation
primitives needed for event-based read state; stage 4 completes their public
WebSocket/IRC behavior. Reuse shared state and authorization, rather than
copying implementations that diverge.

<details>
<summary>Message history and paging (16 lines)</summary>

- `POST /api/channels/{id}/messages` — body `{body, parent_id?, file_ids?, blocks?}`. Returns
  `201 {message}`. Empty body → `400`.
- `MessageObj` carries at least `{id, channel_id, author_id, author, body, parent_id,
  reply_count, created_at, edited_at, files, reactions, mentions}`.
- Durable WebSocket events, rather than REST message objects, carry the channel `event_id`.
- `GET /api/channels/{id}/messages?limit=&cursor=` returns `200 {messages, next_cursor}`
  newest-first.
- Pagination is opaque-cursor based: every paginated list endpoint accepts
  `?limit=N&cursor=<string>` and adds a top-level `next_cursor` field to its envelope — an
  opaque string when more results exist, `null` on the last page.
- Omitting `cursor` returns the first page.
- Clients must not construct or parse cursors; a cursor issued by one node is valid on the other
  two, and it is not invalidated by new messages arriving — it always resumes from the same
  position.
- A malformed or unrecognized cursor → `400`.
- `limit` defaults to `50`, max `200`; out of range → `400`.
- Offset pagination (`?page=`, `?offset=`) is not supported.
- `GET /api/messages/{parent_id}/replies?limit=&cursor=` — `200 {replies: [MessageObj, ...],
  next_cursor}` in oldest-first order.
- `GET /api/search?q=<text>&limit=&cursor=` returns `200 {results, next_cursor}` newest-first,
  matching the caller's accessible channels.
- An optional `workspace_id=<id>` query parameter limits the search to that workspace.
- `GET /api/channels/{id}/pins?limit=&cursor=` returns `200 {pins: [{message, pinned_by,
  pinned_at}, ...], next_cursor}` newest-first.
- `POST /api/messages/{id}/pin` is idempotent and returns `200 {pin:{message_id, pinned_by,
  pinned_at}}`; `DELETE /api/messages/{id}/pin` returns `200 {"unpinned":true}`.
- Non-members of a private channel get `403` on the pin list.

</details>

## Gate

Follow the check order in /spec/README.md. Keep previous requirements working.
