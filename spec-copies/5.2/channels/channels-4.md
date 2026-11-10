# Component B.4: Durable events, replay and IRC

Read /spec/channels/common.md; its API conventions and correctness guidance
apply to every stage. Read the staff instruction.md for supporting API shapes.

## Function dependencies

Functions used from earlier stages (including A): create_message, get_read_state, advance_read_cursor, join_channel, leave_channel, set_channel_topic, archive_channel, unarchive_channel.

Functions added by this stage: commit_channel_event, subscribe_channel, resume_channel, dispatch_durable_events, edit_message, delete_message, add_reaction, remove_reaction, handle_irc_client.

Implement these responsibilities through the named functions or equivalent
methods with these names. Stage 2/3 can introduce the event storage/allocation
primitives needed for event-based read state; stage 4 completes their public
WebSocket/IRC behavior. Reuse shared state and authorization, rather than
copying implementations that diverge.

<details>
<summary>Event ordering and the cluster (7 lines)</summary>

- Durable channel events from any HTTP node or the IRC gateway must share a dense per-channel
  sequence, replay correctly after reconnecting to a different node, and fan out across the
  cluster.
- `ws://127.0.0.1:8000/api/ws?token=<bearer>` (and `:8001`, `:8002`). Frames are JSON, one per
  WebSocket message.
- Client → server: `{"type": "subscribe", "channel_id": <id>}` and `{"type": "resume",
  "channel_id": <id>, "since_event_id": <int>}`.
- Server → client: `{"type": "subscribed", "channel_id": <id>, "head_event_id": <int>}` (sent
  immediately after `subscribe`), `{"type": "resumed", "channel_id": <id>, "head_event_id":
  <int>}` (sent at the end of a resume's catch-up), `{"type": "resume.gap", "channel_id": <id>,
  "earliest_event_id": <int>}` if `since_event_id` is older than the retained tail.
- Durable per-channel broadcast frames carry `{"type": <kind>, "event_id": <int>, "channel_id":
  <id>, ...}`.
- The durable kinds are pinned: `message.created` (new top-level message), `message.reply`
  (thread reply), `message.updated` (edit), `message.deleted`, `reaction.added`,
  `reaction.removed`, `member.joined`, `member.left`, and `channel.updated` /
  `channel.archived` / `channel.unarchived`.
- `event_id` is dense and per-channel — every committed durable event advances the channel's
  sequence by exactly 1, no gaps, no duplicates, and the same sequence is observed across all
  three HTTP nodes and the IRC gateway.

</details>

## Gate

Follow the check order in /spec/README.md. Keep previous requirements working.
