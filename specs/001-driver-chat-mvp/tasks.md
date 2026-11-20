# Tasks 001 — Driver chat MVP

| | |
|---|---|
| Status | Ready to start |
| Implements | [`plan.md`](plan.md) → [`spec.md`](spec.md) |

> Small steps, in order. Each task says what to build, where, and how we know it is done.
> Tick the box when the "Done when" line is true. `[P]` = can be done in parallel with the task above.
> Section numbers like §6.4 point to `plan.md`.

---

## Phase 1 — Skeleton (plan §2–§4)

- [ ] **T1.1 Project files.** `pyproject.toml` (pinned: fastapi, uvicorn, langgraph,
  langgraph-checkpoint-sqlite, langchain-openai, python-dotenv, itsdangerous, email-validator;
  dev: pytest, httpx), `Makefile` with `setup`, `run`, `test`, `reset-db`, `.env.example`.
  Add `data/*.db` to `.gitignore`.
  *Done when:* `make setup` creates `.venv` and installs everything on a clean clone.
- [ ] **T1.2 Config and clock.** `app/config.py`: settings from `.env`; `clock.now()` returns
  `APP_CLOCK` (default `2026-08-04T09:40:00+05:30`); tests can override it.
  *Done when:* a unit test sets the clock to 16:25 and reads it back.
- [ ] **T1.3 Seed + migration.** Copy `setuhaul_schema_and_seed.sql` to `data/seed/`; write
  `data/migrations/001_users.sql` (§4.2).
- [ ] **T1.4 Database build.** `app/db.py`: `build_db(path)` runs seed then migrations;
  `connect()` sets `foreign_keys` and `busy_timeout`. `make setup` / `make reset-db` call it.
  *Done when:* `make reset-db` produces `data/app.db` with 21 shipments, 106 slots, 20 appointments
  and an empty `app_users` table.
- [ ] **T1.5 Test fixtures.** `tests/conftest.py`: build the DB once per session, copy per test,
  fixed-clock fixture, helper to set a different clock.
  *Done when:* two tests that write to the DB don't see each other's changes.

## Phase 2 — Scheduling layer (plan §6) — no LLM

- [ ] **T2.1 Shipment queries.** `scheduling/shipments.py`: `RESCHEDULABLE`, `driver_shipments()`
  with driver labels ("Textiles from Ajmer"), current appointment, baseline and latest ETA,
  `most_recent_other`.
  *Done when:* DRV007 → 2 active; DRV004 → 1 active (SHP1020); DRV006 → 2 active;
  DRV008 → 0 active, most recent other = SHP1019 cancelled (AC16 code part).
- [ ] **T2.2 Arrival maths.** `resolve_arrival()` (§6.1).
  *Done when:* SHP1013 + 60 min → 11:00 (AC8); "11:20" → `2026-08-04T11:20+05:30`;
  "9:00" at clock 09:40 → `ArrivalInPast` (AC17).
- [ ] **T2.3 Slot rules.** `scheduling/rules.py`: `check_slot()` returning failed rule IDs (§6.2).
  *Done when:* one small test per rule S1–S7 passes, plus: a 75-min unload in a 60-min slot does
  **not** fail (A7); a slot held by the same shipment passes S5.
- [ ] **T2.4 Find slots.** `scheduling/slots.py`: `find_slots()` (§6.3).
  *Done when:*
  - AC9: SHP1006 after 12:00 → D2 12:00, D4 12:00, D1 13:00, D2 13:00, D3 13:00;
  - AC10: SHP1014 → only D2 / D4;
  - AC11: SHP1015, ETA 18:30, clock 16:25 → empty;
  - AC18: SHP1017 from 11:30 → D1 12:00 (`is_current`), D2 12:00, D4 12:00, D1 13:00, D2 13:00.
- [ ] **T2.5 Record arrival.** `scheduling/booking.py`: `record_eta()` (§6.5).
  *Done when:* after recording 18:30 for SHP1015, `v_latest_eta` shows 18:30 and the new row is
  `DRIVER_DECLARED`.
- [ ] **T2.6 Book slot.** `book_slot()` + `BookingResult` (§6.4).
  *Done when:*
  - AC12: SHP1006 → SLOT-JAI-019 gives `BOOKED`; new row `CONFIRMED`, `DRIVER_CHAT`, current;
    APT1006 `CANCELLED`, not current; `replaced_appointment_id = APT1006`;
  - AC12a: booking SLOT-JAI-019 again → `ALREADY_YOURS`, no new rows;
  - AC14: DRV006 booking SHP1014 → `NOT_ALLOWED`; AC14a: DRV004 booking SHP1004 → `NOT_ALLOWED`;
  - a slot that fails a rule (e.g. D1 for SHP1014) → `RULE_FAILED` (R23);
  - a taken slot → `SLOT_TAKEN` with up to 5 alternatives.
- [ ] **T2.7 Race test.** AC13: 10 threads, own connections, `threading.Barrier`, same slot.
  *Done when:* exactly 1 `BOOKED`, 9 `SLOT_TAKEN`, and the DB has one active appointment on that
  slot. Run it 20 times in a loop once to check it is not flaky.

## Phase 3 — Identity, conversations, input (plan §5)

- [ ] **T3.1 Login.** `identity.login()`.
  *Done when:* AC1 (` Manoj@Test.com ` twice → one user) and AC2 (`manoj@` rejected) pass.
- [ ] **T3.2 Link phone.** `identity.link_phone()`.
  *Done when:* AC3, AC3a, AC4, AC4a pass, and a second link attempt by the same user is refused (R4b).
- [ ] **T3.3 Ownership helper.** `identity.require_driver(user)` and
  `conversations.require_owner(thread_id, driver_id)`.
  *Done when:* opening another driver's thread raises `NotFound` (not "forbidden", so IDs don't leak).
- [ ] **T3.4 Conversations.** `conversations.py`: list (newest first, with preview), open, create,
  append message (§5.2).
  *Done when:* AC5 passes using a fresh connection after creating 2 conversations.
- [ ] **T3.5 Input cleaning.** [P] `input_clean.clean_message()` (§5.3).
  *Done when:* AC6 ("   " and 1,200 chars rejected), control characters removed, and
  "Reaching 11:20 , tyre burst!!" comes back unchanged.

## Phase 4 — Agent (plan §7)

- [ ] **T4.1 Model setup.** Pick and pin the OpenAI model in `.env.example`; build the chat model
  with 30 s timeout and 1 retry. Allow injecting a fake model for tests.
- [ ] **T4.2 Prompt.** `agent/prompt.py`: build the system prompt from fresh data (§7.2): clock,
  driver's first name, shipments, coordinator phone, behaviour rules.
  *Done when:* a snapshot test for DRV007 and DRV008 shows the expected facts and no shipment the
  driver doesn't own.
- [ ] **T4.3 Tools.** `agent/tools.py`: `resolve_arrival`, `record_arrival`, `find_slots`,
  `book_slot` (§7.3), driver ID read from run config only.
  *Done when:* calling each tool with another driver's shipment returns "not yours"; tool
  signatures shown to the model have no driver ID parameter.
- [ ] **T4.4 Graph and `run_turn()`.** `agent/graph.py`: agent ⇄ tools loop, SQLite checkpointer
  keyed by thread ID, failure handling (§7.4).
  *Done when:* a fake model that raises → reply is the "Sorry…" text and nothing is booked; a fake
  model that books and then raises → reply is the code-built confirmation.
- [ ] **T4.5 End-to-end agent test.** `tests/test_agent_flow.py`: scripted fake model plays the AC12
  conversation (which shipment → 11:20 → slots → D2 12:00).
  *Done when:* the DB matches T2.6's AC12 checks and the final reply names D2 and 12:00.

## Phase 5 — Web (plan §8)

- [ ] **T5.1 API routes.** `web/main.py`: the 7 routes, session cookie, error → 400/404 with a
  short message. The message route stores the driver message, calls `run_turn()`, stores the reply.
  *Done when:* API tests cover login → link → create conversation → send message (fake model) →
  list shows it; a 1,200-char message gets 400 and the agent is not called.
- [ ] **T5.2 Page.** `web/static/`: email view → phone view (first time only) → chat view with
  conversation list; works at phone width; "sending…" state while waiting.
- [ ] **T5.3 Browser walkthrough.** With a real key: `make reset-db`, `make run`, do the AC12 flow
  as DRV006 in the browser.
  *Done when:* booking confirmed in chat and in the DB; restart the app and the conversation is
  still there (R9).

## Phase 6 — Manual checks and docs

- [ ] **T6.1 Manual LLM checks.** Run AC7, AC8, AC15, AC16, AC17, AC19, AC20 against the real model;
  write the result (pass/fail + the reply) into the README table. Fix the prompt for any failure and
  re-run all of them.
- [ ] **T6.2 README.** Setup (clone, copy `.env.example` → `.env`, add key, `make setup`,
  `make run`), demo script for AC12, manual check table, how to reset the data, known limits
  (spec §9).
- [ ] **T6.3 Definition of done.** Fresh clone in a new folder, follow only the README, complete
  AC12. `make test` is green.

---

## Coverage check

| Spec item | Task(s) |
|---|---|
| R1–R4b | T3.1, T3.2 |
| R5 | T3.3, T4.3 |
| R6–R9 | T3.4, T5.3 |
| R10–R11 | T3.5, T5.1 |
| R12–R20a | T4.2, T6.1 |
| S1–S7, ordering, Q2 | T2.3, T2.4 |
| R21–R25 | T2.5, T2.6, T2.7 |
| Spec §8 LLM down | T4.4 |
| AC1–AC20 | every AC appears in a "Done when" line above |
