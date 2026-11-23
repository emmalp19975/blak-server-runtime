# Game Server — Production Roadmap

This document tracks the path from the current prototype to something
safe and reliable to run for real players. It's organized in phases,
roughly ordered by "what breaks first / costs the most if skipped."

## Project layout

```
your_project/                  <- run "python -m server.main" from HERE
├── GameEngine.py               <- CardGame engine
└── server/
    ├── __init__.py
    ├── main.py                 entrypoint
    ├── sessions.py              connection state (leaf module)
    ├── connection.py            websocket handshake / message loop
    ├── rooms.py                 room data structure + lifecycle
    ├── matchmaking.py           host / join / quick-match
    ├── game_dispatch.py         player actions -> engine calls
    └── protocol.py              message-type routing table
```

Dependency graph (one-way, no cycles):

```
main.py
  └─> connection.py
        ├─> sessions.py   (leaf — imports nothing)
        ├─> protocol.py
        │     ├─> matchmaking.py ──> rooms.py ──> sessions.py
        │     └─> game_dispatch.py ──> rooms.py ──> sessions.py
        └─> matchmaking.py
```

---

## Current state (baseline)

A single-process asyncio websocket server. Room creation, host/join/quick-match
matchmaking, and per-player sanitized game state broadcasting all work.
Good separation of concerns at the module level. What's missing is
everything below.

---

## Phase 0 — Trust boundary
**Priority: do this before exposing the server to anyone but yourself.**
This is the only phase that's a security hole today rather than a scaling
limitation — everything else can wait, this can't.

- [ ] **Real auth on the identify message.** Replace `{"gamertag": "..."}`
  with a signed token (JWT or session token) issued by a login service.
  The websocket handshake validates the token in `connection.py`;
  `gamertag` should come *from* the verified token, never from an
  unauthenticated client-supplied field.
- [ ] **Server-authoritative game actions.** Every action in
  `game_dispatch.py` (`play_card`, `discard_card`, etc.) needs the server
  to independently verify: is it this player's turn, do they actually own
  the card/slot referenced, is the target legal. Never trust
  `owner_id`/`card_index` from the client at face value.
- [ ] **Input schema validation.** Add a validation layer (e.g. `pydantic`)
  between "message parsed as JSON" and "message handed to a handler" —
  natural home is `protocol.route_message()`. Reject malformed types /
  out-of-range indices with a clean error instead of letting them reach
  engine internals.

**Estimated effort (solo, comfortable with Python/asyncio):** 3–6 weeks.

---

## Phase 1 — Reliability primitives
**Priority: second, right after Phase 0.** Biggest reliability win for
the effort spent.

- [ ] **Supervised sends, not fire-and-forget.** `sessions.send_to()`
  currently does `asyncio.create_task(ws.send(...))` with no error
  handling — failures are silently swallowed. Either catch/log exceptions
  in that task, or give each connection a dedicated outbound
  `asyncio.Queue` + writer task so a slow/dead client can't block
  room-wide broadcasts.
- [ ] **Reconnection grace period.** On disconnect, don't immediately drop
  the player from their room — mark them "disconnected," start a timer
  (30–60s), and let a reconnect with a valid token re-bind to the same
  room/game state. Only forfeit/pause after the grace period expires.
  (Currently `connection.py`'s disconnect handler removes them from
  matchmaking queues but has no equivalent grace period for in-room
  players — this is a known, called-out gap.)
- [ ] **Heartbeats.** App-level ping/pong to detect dead-but-not-closed
  connections and reap them, rather than relying solely on
  `ConnectionClosed`.
- [ ] **Rate limiting per connection.** Cap messages/sec per gamertag in
  `connection.py`'s message loop, to blunt spam/flood (intentional or from
  a buggy client retry loop).

**Estimated effort (solo):** 3–5 weeks (reconnection edge cases eat time).

---

## Phase 2 — State externalization
**Priority: only as far as "crash-safe single server" for most projects.
Defer full horizontal scaling until load actually demands it.**

This phase forks depending on scale needs:

### 2a — Crash recovery (do this regardless of scale)
- [ ] Move `rooms` / `room_codes` / `queues_by_size` (currently in-memory
  dicts in `rooms.py` / `matchmaking.py`) into a lightweight persistence
  layer (SQLite or Redis) so a process restart doesn't wipe every active
  game.
- [ ] Periodic state snapshotting for debugging/dispute resolution.

### 2b — Horizontal scale (only if you actually need multiple processes/regions)
- [ ] Move matchmaking state into Redis so multiple server instances share
  one queue/room registry.
- [ ] Add a routing layer / gateway that knows which backend process owns
  which room, so a client's websocket reaches the right shard.
- [ ] Decide room affinity (each active game pinned to one worker process;
  matchmaking/lobby state is the shared/distributed part).

**Note:** because `rooms.py` and `sessions.py` are the only modules
holding this state, and everything else reaches it through their function
interfaces (never touching the dicts directly), this swap should stay
localized to those two files rather than rippling across the codebase.

**Estimated effort (solo):** 2a is a moderate lift; 2b is real
distributed-systems work — expect this to be the hardest phase to get
right, and most solo/small projects should skip 2b until there's clear
evidence of need (a single well-resourced server handles a lot of
concurrent websocket players before this becomes necessary).

---

## Phase 3 — Observability
**Priority: cheap now, invaluable once anything breaks in prod. Worth
doing early even in small slices.**

- [ ] Replace `print()` calls throughout with structured logging
  (`logging` with JSON output, or `structlog`) — each log line should
  carry `room_id` / `gamertag` / `event_type` so one room's lifecycle is
  traceable across the noise.
- [ ] Metrics: active connections, active rooms, queue depth per size,
  matchmaking wait time, message throughput, error rate, disconnect rate.
  Export via Prometheus/statsd, or start cheap with a hosted log
  viewer / error tracker (Sentry, Better Stack).
- [ ] Health check endpoint for load balancer / orchestrator probes.
- [ ] Graceful shutdown on SIGTERM: stop accepting new connections, notify
  active rooms, give in-flight games a chance to persist/pause before exit.

**Estimated effort (solo):** 3–5 days for the logging basics; ongoing for
full metrics/dashboards.

---

## Phase 4 — Protocol hardening
**Priority: as the product matures.**

- [ ] Version the message schema (`"v": 1` field or similar) so
  client/server can evolve independently.
- [ ] Structured error codes instead of free-text `message` strings in
  `sessions.send_error()`, so clients can branch on them (localize, retry,
  show specific UI).
- [ ] Idempotency keys on actions if client-side retry logic is ever
  added, so a duplicate `play_card` send (e.g. from a flaky reconnect)
  doesn't double-apply.

---

## Phase 5 — Game-specific integrity
**Priority: only if this becomes competitive/ranked/real-money. Pure
overengineering otherwise.**

- [ ] Server-side replay log per match (sequence of validated actions) —
  enables dispute resolution, anti-cheat auditing, replay features. Natural
  home is `game_dispatch.py`, since every action passes through it.
- [ ] Anomaly detection on action timing/patterns if there's real-money or
  ranked stakes.
- [ ] Skill-based matchmaking fairness if it's competitive rather than
  casual/friends-based (extends `matchmaking.py`).

---

## Suggested order of attack

1. **Phase 0** (auth + server-authoritative validation) — non-negotiable
2. **Phase 1** (reconnection, supervised sends, heartbeats)
3. **Phase 3's logging piece** — cheap, do it early
4. **Phase 2a** (crash-safe single server) — defer 2b until load demands it
5. **Phase 4 and full Phase 3/5** — as the product matures

## Pragmatic shortcut: lean on managed services

Rather than building Phase 0–2 infrastructure from scratch:

- **Auth** → Supabase Auth, Firebase Auth, or Clerk instead of a
  hand-rolled token system
- **Persistence/state** → managed Redis (e.g. Upstash's free tier) instead
  of self-hosting
- **Hosting** → Fly.io or Railway for process supervision/restarts
- **Error tracking** → Sentry instead of a custom log pipeline

This reframes the work from "build a distributed systems platform" to
"integrate a handful of managed services" — a much more solo-dev-shaped
problem.

## Realistic timeline (solo, moderate Python/asyncio experience)

| Milestone | Estimate |
|---|---|
| Phase 0 | 3–6 weeks |
| Phase 1 | 3–5 weeks |
| Phase 3 (logging basics) | 3–5 days |
| **Safe to expose to real strangers, reasonably reliable** | **~2–3 months part-time** |

Full production-grade (all 5 phases) is not a realistic solo target on any
short timeline — most funded studios don't build all of it either. The
practical bar for most projects is Phase 0 + 1 + basic logging, leaning on
managed services for the hard infrastructure pieces.
