# Production readiness checklist

Gaps found in the review of the Level 1 build (2026-11-26).
Tick an item when it's done **and** it has a test or a check that proves it.

Priority: **P0** = fix before anyone real uses it · **P1** = needed for production · **P2** = needed at scale.
Scope: **L1** = fits Level 1 (CLAUDE.md) · **L2** = planned in docs/level-2.md · **Later** = not yet planned.

---

## 1. Bugs in current code

- [ ] **P0 · L1** Contested slots are decided only when a driver calls `check_my_request`. Add a background resolver that decides every window once it closes. (`services/contention.py` → `resolve_slot`)
- [ ] **P0 · L1** A retry while the turn is still running returns the *previous* agent reply. Add a status to each message (PROCESSING / DONE / FAILED); a retry waits for the result, or runs the turn again if it failed. (`agent/runner.py`)
- [ ] **P0 · L1** Timeouts don't line up. The LLM can take about 90s per call (30s × 3 attempts) but the UI waits 60s. Set one time limit for the whole turn and make the UI wait longer than it. (`agent/llm.py`, `ui/app.py`)
- [ ] **P1 · L1** If a tool succeeded (e.g. `request_slot`) and a later LLM call fails, the fallback reply hides it. Base the fallback reply on what the tools actually did.
- [ ] **P1 · L1** Remove the dead first `book_slot` in `services/booking.py` (defined twice).
- [ ] **P2 · L1** Remove the duplicate pydantic imports in `config.py`.
- [ ] **P1 · Later** `requires_human_review` is saved but never read. Add a way for ops to see flagged messages.

## 2. Security

- [ ] **P0 · L2** Login is email-only. Add OTP (SMS or email) or a magic link. → Cognito email OTP, docs/level-2.md
- [ ] **P0 · Later** Rate-limit `/login` (per IP) and `/chat` (per driver).
- [ ] **P1 · L2** Make sessions revocable: a sessions table, or short-lived tokens plus refresh tokens. Logout must invalidate the token on the server.
- [ ] **P1 · L1** Check the agent's reply before sending: no "booked" unless the request is WON, and no IDs belonging to other drivers.
- [ ] **P1 · L1** Catch unexpected exceptions in each tool and return a safe error dict. Set `ToolNode(handle_tool_errors=...)` explicitly.
- [ ] **P1 · Later** Handle personal data: a data agreement with the LLM provider, personal data removed from logs, a retention period for chat history.
- [ ] **P2 · Later** Move secrets from `.env` to a secret manager and rotate them.

## 3. Reliability

- [ ] **P1 · L1** Lock each thread so two messages on the same thread never run at the same time.
- [ ] **P1 · L1** Pick one source of truth for chat history (`chat_messages` vs the LangGraph checkpoints), or write both in one step.
- [ ] **P1 · L1** Add a global FastAPI exception handler with a consistent error shape and a `request_id`.
- [ ] **P1 · L1** Add a readiness check (DB and checkpoint DB open); keep `/health` as the liveness check.
- [ ] **P2 · Later** Add a fallback LLM model and a circuit breaker.

## 4. Scale and performance

- [ ] **P1 · Later** Stop running the agent inside the HTTP request: async endpoints, or a job queue with streaming (SSE or WebSocket).
- [ ] **P1 · L1** Cap chat memory sent to the LLM: keep the last N messages, or summarise older ones.
- [ ] **P2 · L1** Build the agent graph once, and pass `ToolContext` through the run config instead of rebuilding every turn.
- [ ] **P2 · Later** Hold an offered slot for a short time (e.g. 2 min) so it isn't taken or offered twice.
- [ ] **P2 · Later** Move from SQLite to Postgres (`SELECT ... FOR UPDATE` or exclusion constraints, `PostgresSaver`).
- [ ] **P2 · Later** Add a migration tool (Alembic or similar) instead of `CREATE TABLE IF NOT EXISTS`.

## 5. Agent quality

- [ ] **P1 · L1** Build an eval set of scripted conversations: never says "booked" while PENDING, never invents slot IDs, refuses to act for other drivers, handles Hindi and Hinglish.
- [ ] **P1 · L1** Save the prompt version and model name with every agent turn.
- [ ] **P2 · L1** Cap output tokens per call (`max_tokens`).
- [ ] **P2 · Later** Add a token budget per driver per day.
- [ ] **P1 · Later** Add a handoff to a human (stuck agent, flagged message, "talk to a person").

## 6. Observability

- [ ] **P1 · L1** Set up structured JSON logging with `request_id`, `thread_id` and `driver_id` on every line.
- [ ] **P1 · Later** Add tracing of every LLM call and tool call (LangSmith, Langfuse or OpenTelemetry).
- [ ] **P2 · Later** Add metrics: turn time, LLM errors, tokens and cost, tool error rate, contested slots, how often the fallback reply is shown.
- [ ] **P2 · L1** Extend the decision audit: record the inputs used for ranking with each decided request.

## 7. Testing and CI

- [ ] **P1 · L1** Unit tests for `priority` (tie-breaks, `deciding_criterion`).
- [ ] **P1 · L1** Unit tests for `slots` (each reason in `check_slot`).
- [ ] **P1 · L1** Unit tests for `sanitize` (invisible characters, length limits, empty input).
- [ ] **P1 · L1** Unit tests for `contention` (window open/closed, WON/LOST, FAILED-then-next-in-line).
- [ ] **P1 · L1** Unit tests for `identity` (expired or tampered token).
- [ ] **P1 · L1** API tests with `TestClient` (401, 404 for another driver's thread, 422, retry with the same `client_msg_id`).
- [ ] **P1 · L1** Agent tests with a fake LLM (fast, free, deterministic).
- [ ] **P1 · L1** Add `ruff` and `mypy` to dev dependencies and get both passing.
- [ ] **P2 · Later** Run tests, lint and type checks in CI (GitHub Actions) on every PR.

## 8. Product

- [ ] **P1 · Later** Push the decision to the driver (SSE, WebSocket or SMS) instead of waiting for them to ask.
- [ ] **P2 · Later** Add a WhatsApp or SMS channel.
- [ ] **P2 · Later** Manage thread lifecycle: close old threads, and resume or list existing threads on login.
