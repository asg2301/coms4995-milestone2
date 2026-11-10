# Common Component B rules and implementation guidance

## Both components: API conventions

These rules apply to every endpoint in both components.

<details>
<summary>REST conventions (6 lines)</summary>

- Base URL: `http://127.0.0.1:8000` (also `:8001`, `:8002`); all routes are under `/api/...` and
  return JSON.
- Auth: `Authorization: Bearer <token>` on every authenticated route. `401` when missing or
  invalid.
- Resource envelopes: responses use a single top-level resource key (`user`, `workspace`,
  `channel`, `message`, `file`, `workspaces`, `channels`, `messages`, `invitations`, `files`,
  `members`, `groups`, etc.).
- Timestamps are RFC 3339 / ISO 8601 UTC strings (e.g. `2026-05-16T08:07:34.123Z`).
- Resource ids are stable across the cluster.
- Status codes unless an endpoint below specifies an exception: `200` on read/update success,
  `201` on create, `204` on delete-with-no-body, `400` on validation, `401` on missing/invalid
  auth, `403` on auth-but-forbidden, `404` on unknown id, `409` on conflict (duplicate
  slug/name/registration), `413` on upload over limit, `423` on writes to an archived channel.

</details>


## Implementation and verification guidance

These are observable API rules, not optional examples. Read the full staff
instruction.md for supporting endpoint shapes, including edits, deletes,
reactions and IRC. Use one transactional per-channel event allocator shared by
all writers. Store the message/member change and event in the SAME transaction.
Do not use a process-local counter or global sequence across channels. Sequence
numbers advance only for committed durable changes, not heartbeats or errors.

Use stable cursor boundaries (for example a signed/validated encoded tuple of
resource ID, endpoint, channel and ordering key) rather than offsets into a
changing list. Validate limits and cursor structure/scope. An existing cursor
must remain valid when new messages arrive. Use exclusive boundaries. Return
next_cursor=null only when no further results remain. Thread replies must have
oldest-first paging. All paginated endpoints need consistent envelopes.

Build message edit/delete/reaction support from the staff contract so their
pinned durable event kinds can be generated and tested. Preserve author-only
edits, author-or-owner deletion and idempotent reactions/pins. Frame payloads
follow the staff instruction. Do not assume a REST MessageObj has event_id.

Unread state is per user AND channel, with a monotonic read event cursor. Derive
counts consistently from committed channel activity; own messages and own
mentions must not increment one's mention_count. Only qualifying mentions after
the read position count; reading to the observed head clears unread mentions.
Do not count arbitrary non-message events as new chat messages.

Implement literal / in IRC channel names (#workspace/channel) and token PASS
authentication, registration replies and JOIN/NAMES behavior per instruction.md.
IRC PRIVMSG and HTTP POST must share message persistence and event allocation;
HTTP-to-IRC and IRC-to-WebSocket fan-out must both work. Favor a durable DB event
log with polling fallback so delivery/replay is not dependent on Redis uptime.
The full staff contract asks for local Redis and supervised node/Redis recovery;
implement these after the core B rules and verify resilience.

Follow the check order in /spec/README.md. A passing sample does not replace implementing every requirement above.
