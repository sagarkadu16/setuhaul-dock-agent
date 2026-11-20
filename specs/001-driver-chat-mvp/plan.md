# Plan 001 — Driver chat MVP (show slots)

| | |
|---|---|
| Status | Approved v2 — updated for spec v4 (show slots, no booking) |
| Implements | [`spec.md`](spec.md) (v4) |
| Next | `tasks.md` |

> This file says **how** we build what `spec.md` asks for. Requirement IDs (R…, S…, AC…) point back
> to the spec. If this plan and the spec disagree, the spec wins and this plan gets fixed.

---

## 1. Shape of the system

```
Browser (one HTML page + small JS)
   │  JSON over HTTP, session cookie
   ▼
FastAPI app ──────────────► identity      (R1–R5)        ─┐ read/write:
   │                        conversations (R6–R9)        ─┘ app_users, chat_threads, chat_messages
   │                        input_clean   (R10–R11)
   ▼
LangGraph agent  ── tools ─► scheduling   (S1–S7, R24)   ◄── deterministic, no LLM, READ-ONLY
   │                             │
   ▼                             ▼
LLM via OpenRouter         SQLite: data/app.db  (seed data + our tables)
                           SQLite: data/checkpoints.db (agent memory)
```

**The one rule that shapes everything:** the LLM talks and picks *which* tool to call. Code decides
which slots are valid, computes times and checks ownership. The LLM never shows a slot that code did
not return (R15), and it cannot pass a driver ID (R5).

**Read-only by construction (R26):** the scheduling layer opens SQLite in read-only mode
(`mode=ro`), so it *cannot* write to shipments, appointments, slots or ETAs even by mistake. Only
`identity` and `conversations` open writable connections, and they touch only their own tables.

## 2. Tech choices

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | LangGraph's home language; local machine has 3.14 |
| Web | FastAPI + Uvicorn, one static HTML/JS page | Smallest thing that gives a real browser flow; no frontend build step |
| Sessions | Starlette `SessionMiddleware` (signed cookie holding `user_id`) | Identification only (A1); no auth library needed |
| Agent | LangGraph: agent node ⇄ tool node loop; chat model via `langchain-openai` pointed at OpenRouter | Matches README; graph is small enough to read in one sitting |
| LLM provider | OpenRouter (OpenAI-compatible API); base URL is configurable | Owner has an OpenRouter key; switching provider is a `.env` change only |
| Agent memory | `langgraph-checkpoint-sqlite`, `thread_id` = conversation ID | Keeps tool calls/results (e.g. shown slots) across turns and restarts |
| Database | SQLite, built from the seed SQL plus one migration | Seed data and views (`v_latest_eta`, `v_slot_availability`) are ready to query |
| Tests | pytest + FastAPI `TestClient` + a scripted fake chat model | Every AC except the LLM-judgement ones runs offline and free |
| Setup | `Makefile` + `venv` + `pip` (uv is not installed locally) | Gives the spec's "two commands": `make setup`, `make run` |

Model name, API key, base URL, clock and paths come from `.env` (template in `.env.example`). The
model is a small, cheap OpenRouter model that supports tool calling, pinned in T4.1.

## 3. Repository layout

```
Makefile                    setup / run / test / reset-db
pyproject.toml
.env.example                LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, APP_CLOCK, SESSION_SECRET
data/
  seed/setuhaul_schema_and_seed.sql   copied unchanged from the data package
  migrations/001_users.sql            our additions (§4.2)
  app.db, checkpoints.db              built locally, git-ignored
app/
  config.py                 settings + clock.now()               (A3)
  db.py                     connections (read-write and read-only), build/reset database
  identity.py               login, link phone, ownership checks   (R1–R5)
  conversations.py          list / open / create / append         (R6–R9)
  input_clean.py            clean_message()                       (R10–R11)
  scheduling/               all read-only
    shipments.py            active shipments, labels, arrival maths, coordinator contact
    rules.py                pure slot rules S1–S7
    slots.py                find_slots(), check_pick()
  agent/
    prompt.py               system prompt builder
    tools.py                LangChain tools wrapping scheduling/*
    graph.py                LangGraph graph + run_turn()
  web/
    main.py                 FastAPI routes
    static/index.html, app.js, style.css
tests/
  conftest.py               fresh DB copy per test, fixed clock, fake LLM
  test_setup.py             Phase 1 checks
  test_identity.py          AC1–AC4a
  test_conversations.py     AC5
  test_input_clean.py       AC6
  test_shipments.py         AC8 / AC17 maths, AC16 lookup, active definition, coordinator
  test_rules.py             S1–S7 one by one
  test_slots.py             AC9, AC10, AC11, AC14, AC14a, AC18, check_pick
  test_agent_flow.py        AC12 + AC21 end to end with scripted fake model
```

## 4. Data

### 4.1 Building the database
- `make setup` (and `make reset-db`) runs the seed SQL, then everything in `data/migrations/`, into
  `data/app.db`. The original package `.db` is never written to.
- `connect()` opens read-write; `connect_readonly()` opens with `file:…?mode=ro` (§1).
  Both set `PRAGMA foreign_keys = ON` and `PRAGMA busy_timeout = 5000`.
- Tests build the database once per test session in a temp folder, then copy the file for each test,
  so every test starts from the seed state (spec §7).

### 4.2 Our additions (`001_users.sql`)

```sql
CREATE TABLE app_users (
    user_id     TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,          -- stored lower-cased and trimmed (R2)
    driver_id   TEXT UNIQUE                    -- NULL until linked; UNIQUE gives R4a
                REFERENCES drivers(driver_id),
    created_at  TEXT NOT NULL,
    linked_at   TEXT
);
```

There is no "unlink" statement anywhere in the code (R4b).

### 4.3 Reusing seed tables

| Need | Table | How |
|---|---|---|
| Conversations (R6–R8) | `chat_threads` | One row per conversation. Owner = `driver_id` (1 user ↔ 1 driver, permanently). New rows: `thread_status='OPEN'`, `thread_intent='UNKNOWN'`, `shipment_id` filled in once known. |
| Messages (R9) | `chat_messages` | `sender_type` `DRIVER` or `AGENT`. Seed threads for a driver also show up as history, which is realistic. |
| Shipments, ETAs, slots, appointments | `shipments`, `v_latest_eta`, `appointment_slots`, `docks`, `appointments`, `facility_rules` | **Read only.** |
| Coordinator contact (R13a, R16, R18) | `facility_contacts` | `contact_role='WAREHOUSE_COORDINATOR'` for the shipment's destination (Jaipur: CON001, +91-141-5001101). |

Using the seed chat tables means ownership (R5) is one check everywhere: *row's `driver_id` = session
user's `driver_id`*.

### 4.4 Time
- **Scheduling time** (`clock.now()`): fixed at `APP_CLOCK`, default `2026-08-04T09:40:00+05:30` (A3).
  Used by every rule. Tests can override it per test (AC11 uses 16:25).
- **Chat timestamps** (`message_ts`, `opened_at`): real wall clock. With a frozen clock every
  conversation would have the same time and "newest first" (R6) would not work. All are stored as
  ISO-8601 with `+05:30`.
- All comparisons parse to `datetime`; nothing compares timestamp strings.

### 4.5 IDs
New rows get prefixed short IDs: `USR-…`, `THR-…`, `MSG-…` (prefix + 8 hex chars from `uuid4`).

## 5. Login, conversations and input

### 5.1 Identity (R1–R5)
- `login(email)`: trim, lower-case, validate with `email-validator` (no DNS check). Invalid →
  `InvalidEmail` (R3). `INSERT … ON CONFLICT(email) DO NOTHING`, then select → same user every time
  (R1, R2).
- `link_phone(user_id, phone)`: keep digits, take the last 10, compare with the last 10 digits of
  `drivers.phone` (R4). No match → `NoSuchDriver`. Driver already linked → `DriverAlreadyLinked` (R4a).
  User already linked → `AlreadyLinked`.
- Every route after login requires a linked user; unlinked users only see the "enter your phone" step.

### 5.2 Conversations (R6–R9)
- List: `chat_threads WHERE driver_id = ? ORDER BY opened_at DESC`, with the last message as a preview.
- Open: check ownership, then messages `ORDER BY message_ts, chat_message_id`.
- The driver message is stored **before** the agent runs, and the agent reply right after, each in its
  own short transaction, so a crash never loses the driver's message (R9).

### 5.3 Input cleaning (R10–R11)
`clean_message(text) -> str | raises InvalidMessage`:
1. Normalise line endings; strip leading/trailing whitespace.
2. Remove Unicode control characters (category `Cc`) except `\n` and `\t`.
3. Empty → reject. Over 1,000 characters (after cleaning) → reject.

Nothing else: no case change, spelling fix or translation (R11). Rejected messages are not stored and
never reach the agent (AC6); the API returns 400 with a driver-friendly message.

## 6. Scheduling layer (deterministic, read-only)

### 6.1 Shipments (`scheduling/shipments.py`)
- `RESCHEDULABLE = {PLANNED, ASSIGNED, IN_TRANSIT}` (spec §5).
- `driver_shipments(driver_id)` returns:
  - `active`: reschedulable shipments, each with a **driver label** built from data
    ("Textiles from Ajmer"), destination name, baseline ETA (`original_eta_ts`), latest ETA
    (`v_latest_eta.effective_eta_ts`), current appointment (dock code + time) if any;
  - `most_recent_other`: if `active` is empty, the latest non-reschedulable shipment with its status
    (R20a, AC16: DRV008 → SHP1019 cancelled).
- `get_own_shipment(driver_id, shipment_id)`: the shipment if it is the driver's and reschedulable,
  else `NotAllowed` (R24, AC14, AC14a).
- `resolve_arrival(shipment, *, clock_time=None, delay_minutes=None)`:
  - `clock_time` "11:20" → that time today (A3);
  - `delay_minutes` 60 → baseline ETA + 60 min (AC8: 10:00 → 11:00);
  - result earlier than `clock.now()` → `ArrivalInPast` (AC17).
  The LLM pulls the numbers out of the driver's words; **code does the maths**.
- `coordinator_for(facility_id)`: name and phone of the warehouse coordinator.

### 6.2 Slot rules (`scheduling/rules.py`)
One pure function, used for listing slots and for checking a slot the driver picked:

```python
def check_slot(shipment, slot: SlotView, *, arrival, finish_by=None, heavy_kg=None) -> list[str]:
    """Return the list of failed rule IDs; empty list = slot is valid."""
# SlotView = slot + its dock (type, status, max weight) + held_by_shipment (active appointment)
```

| Rule | Check |
|---|---|
| S1 | `slot.facility_id == shipment.destination_facility_id` |
| S2 | needed type = `REEFER` if `temperature_control_required` or `required_dock_type='REEFER'`; else `HEAVY` if `required_dock_type='HEAVY'` or weight > the facility's `HEAVY_DOCK_REQUIRED_KG` rule; else `STANDARD`. Must equal `dock.dock_type`. |
| S3 | `shipment.load_weight_kg <= dock.max_vehicle_weight_kg` |
| S4 | `slot.slot_status == 'OPEN'` and `dock.dock_status == 'ACTIVE'` (A6) |
| S5 | `held_by` (active appointment's shipment, if any) is empty **or** is this shipment (Q2) |
| S6 | `slot.slot_start_ts >= arrival` |
| S7 | if `finish_by`: `slot_start + expected_unload_min <= finish_by` |

A7: unload time longer than the slot is **not** a rule failure.

### 6.3 Finding slots (`find_slots`)
- `find_slots(driver_id, shipment_id, earliest_start, finish_by=None)`. `earliest_start` is the later
  of the confirmed arrival time and any "after 12" the driver asked for (AC9). It must not be in the
  past.
- One query: slots at the destination facility from `earliest_start` onward, joined to `docks` and to
  the active appointment (same join as `v_slot_availability`).
- Filter with `check_slot`, sort by `(slot_start_ts, dock_code)`, take 5.
- Each result: `slot_id`, `dock_code`, start, end, and `is_current: bool` (Q2 / AC18).
- Expected results, checked by tests: AC9, AC10 (D2/D4 only), AC11 (empty), AC18.

### 6.4 Checking a picked slot (`check_pick`) — R16, edge case "slot not shown"
- `check_pick(driver_id, shipment_id, dock_code, start_time, arrival, finish_by=None)` finds that slot
  and runs `check_slot`.
- Valid → `{valid: true, dock, start, coordinator}` so the agent can repeat the choice and hand over
  (R16). Not valid → `{valid: false, reason, alternatives: find_slots(...)}`.
- Nothing is written (R26).

## 7. Agent

### 7.1 Graph
A two-node LangGraph loop: **agent** (chat model with tools bound) → **tools** (runs tool calls) →
back to agent, until the model replies without a tool call. Checkpointed by conversation ID.

`run_turn(thread_id, driver_id, text) -> str` is the only entry point the web layer uses.

### 7.2 What the model is given every turn
The system prompt is rebuilt each turn from fresh data, so the model never has to ask for what the
system knows (R14):
- current time (`clock.now()`), driver's first name;
- the driver's active shipments with labels, current slot, baseline and latest ETA — or the
  most-recent-other shipment and its status (R20a);
- the destination's warehouse coordinator name and phone (R13a, R16, R18);
- the behaviour rules from spec §6.4, written as short instructions: one question at a time, suggest
  the answer, only two things may be asked, give up after two unclear answers, stay on topic, short
  phone-friendly replies, and **never say a slot is booked, reserved or held — say it is available
  now and must be confirmed by phone** (R17).

### 7.3 Tools
The driver ID comes from the run config (`config["configurable"]["driver_id"]`), never from the
model's arguments, so the model cannot act for another driver (R5, R24).

| Tool | Arguments from the model | Does |
|---|---|---|
| `resolve_arrival` | `shipment_id`, `clock_time` *or* `delay_minutes` | §6.1 maths; returns exact time or "in the past" |
| `find_slots` | `shipment_id`, `earliest_start`, `finish_by?` | §6.3 |
| `check_pick` | `shipment_id`, `dock_code`, `start_time`, `arrival`, `finish_by?` | §6.4 |

Every tool checks ownership again and returns plain JSON. Tool errors are returned to the model as
short messages ("That shipment is not yours"), not raised. There is no tool that writes.

### 7.4 Failure handling (spec §8)
Model call timeout 30 s, one retry. On any model or graph error the driver gets
"Sorry, I can't respond right now, please try again", which is stored as the agent message.

## 8. Web layer

| Method & path | Body | Returns |
|---|---|---|
| `POST /api/login` | `{email}` | `{user, linked}` — sets cookie |
| `POST /api/link` | `{phone}` | `{user, driver_name}` |
| `POST /api/logout` | — | clears cookie |
| `GET /api/conversations` | — | list, newest first |
| `POST /api/conversations` | — | new conversation |
| `GET /api/conversations/{id}` | — | messages in order |
| `POST /api/conversations/{id}/messages` | `{text}` | `{driver_message, agent_message}` |

One page with three views: email → phone link (first time only) → chat (conversation list on the
left, messages on the right; stacks on a phone screen). Replies are not streamed in the MVP.
Errors show as a one-line message under the input box.

## 9. Testing (spec §11)

| AC | Test | How |
|---|---|---|
| AC1–AC4a | `test_identity.py` | direct calls + API |
| AC5 | `test_conversations.py` | create 2 conversations, open a **new** connection/app instance, list |
| AC6 | `test_input_clean.py` | also asserts the agent function was never called |
| AC9, AC10, AC11, AC18 | `test_slots.py` | exact expected slot IDs and order |
| AC14, AC14a | `test_slots.py` | `find_slots` / `check_pick` raise `NotAllowed` |
| AC12, AC21 | `test_agent_flow.py` | scripted fake model emits the tool calls; reply names D2 12:00 and +91-141-5001101; snapshot of shipments, appointments, slots and ETA tables is identical before and after |
| R26 by construction | `test_slots.py` | a write through `connect_readonly()` raises |
| AC8, AC16, AC17 (code parts) | `test_shipments.py` | arrival maths, past-time error, most-recent-other lookup |
| AC7, AC8, AC15, AC16, AC17, AC19, AC20 | **manual**, real model | script listed in README with expected behaviour |

`make test` runs all automated tests with no API key and no network.

## 10. Order of work (outline for `tasks.md`)

1. Skeleton: `pyproject.toml`, `Makefile`, `.env.example`, config + clock, database build. ✅
2. Scheduling layer, read-only, + its tests (§6). ✅
3. Identity, conversations, input cleaning + tests (§5). ✅
4. Agent graph, prompt, tools, fake-model test (§7). ✅
5. Web API + page (§8); walk through AC12 in the browser.
6. Manual LLM checks; README (setup, demo script, manual AC list).

## 11. Risks and how we handle them

| Risk | Mitigation |
|---|---|
| Model says "booked" or "reserved" (R17) | Prompt rule + no booking tool exists + manual check in AC12; the fake-model test checks the scripted reply. |
| Driver thinks the shown slot is held, then it is gone when they call | Agent wording says "available now"; booking and holding are spec 002. |
| Model gets time maths wrong | It never does maths: `resolve_arrival` does (§6.1). |
| LangGraph / LangChain API changes between versions | Pin versions in `pyproject.toml`; the graph is small and hand-built. |
| SQLite "database is locked" under parallel requests | `busy_timeout`, short transactions, one connection per request/thread; scheduling only reads. |
| Seed chat history confuses the model | History is only shown in the UI; the agent's memory starts empty for each new conversation. |

## 12. Decisions (confirmed)

- **D1. ✅** Reuse the seed `chat_threads` / `chat_messages` tables for conversations instead of new
  tables (§4.3). Side effect: drivers with seeded threads see them as past conversations.
- **D2. ✅** Chat timestamps use the real clock; everything operational uses the fixed clock (§4.4).
- **D3. ✅** A plain HTML/JS page instead of Streamlit or a React app (§8).
- **D4. ✅** LLM provider is OpenRouter through its OpenAI-compatible API (`LLM_BASE_URL`).
- **D5. ✅** Show slots only; scheduling is read-only; booking design kept for spec 002 (plan v1 in
  git history, commit `062fc38`).
- **D6. ✅** Model: `openai/gpt-4.1-mini` via OpenRouter (owner's choice; tool calling checked in T4.1).
