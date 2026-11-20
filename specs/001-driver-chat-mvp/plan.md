# Plan 001 — Driver chat MVP

| | |
|---|---|
| Status | Approved — decisions D1–D3 confirmed |
| Implements | [`spec.md`](spec.md) (v3) |
| Next | `tasks.md` |

> This file says **how** we build what `spec.md` asks for. Requirement IDs (R…, S…, AC…) point back
> to the spec. If this plan and the spec disagree, the spec wins and this plan gets fixed.

---

## 1. Shape of the system

```
Browser (one HTML page + small JS)
   │  JSON over HTTP, session cookie
   ▼
FastAPI app ──────────────► identity      (R1–R5)
   │                        conversations (R6–R9)
   │                        input_clean   (R10–R11)
   ▼
LangGraph agent  ── tools ─► scheduling   (S1–S7, R21–R25)  ◄── deterministic, no LLM
   │                             │
   ▼                             ▼
OpenAI chat model          SQLite: data/app.db  (seed data + our tables)
                           SQLite: data/checkpoints.db (agent memory)
```

**The one rule that shapes everything:** the LLM talks and picks *which* tool to call. Code decides
which slots are valid, computes times, checks ownership and writes bookings. The LLM never sees a
slot it could book that code did not return (R15), and it cannot pass a driver ID (R5).

## 2. Tech choices

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | LangGraph's home language; local machine has 3.14 |
| Web | FastAPI + Uvicorn, one static HTML/JS page | Smallest thing that gives a real browser flow; no frontend build step |
| Sessions | Starlette `SessionMiddleware` (signed cookie holding `user_id`) | Identification only (A1); no auth library needed |
| Agent | LangGraph: agent node ⇄ tool node loop, OpenAI chat model via `langchain-openai` | Matches README; graph is small enough to read in one sitting |
| Agent memory | `langgraph-checkpoint-sqlite`, `thread_id` = conversation ID | Keeps tool calls/results (e.g. offered slot IDs) across turns and restarts |
| Database | SQLite, built from the seed SQL plus one migration | Seed data already enforces the key constraints (one active booking per slot) |
| Tests | pytest + FastAPI `TestClient` + a scripted fake chat model | Every AC except the LLM-judgement ones runs offline and free |
| Setup | `Makefile` + `venv` + `pip` (uv is not installed locally) | Gives the spec's "two commands": `make setup`, `make run` |

Model name, API key, clock and paths come from `.env` (template in `.env.example`). The default model
is a current small, cheap OpenAI chat model with tool calling, pinned when the code is written.

## 3. Repository layout

```
Makefile                    setup / run / test / reset-db
pyproject.toml
.env.example                OPENAI_API_KEY, OPENAI_MODEL, APP_CLOCK, SESSION_SECRET
data/
  seed/setuhaul_schema_and_seed.sql   copied unchanged from the data package
  migrations/001_users.sql            our additions (§4.2)
  app.db, checkpoints.db              built locally, git-ignored
app/
  config.py                 settings + clock.now()               (A3)
  db.py                     connections, build/reset database
  identity.py               login, link phone, ownership checks   (R1–R5)
  conversations.py          list / open / create / append         (R6–R9)
  input_clean.py            clean_message()                       (R10–R11)
  scheduling/
    shipments.py            active shipments, labels, arrival maths
    rules.py                pure slot rules S1–S7
    slots.py                find_slots()
    booking.py              book_slot(), record_eta()             (R21–R25)
  agent/
    prompt.py               system prompt builder
    tools.py                LangChain tools wrapping scheduling/*
    graph.py                LangGraph graph + run_turn()
  web/
    main.py                 FastAPI routes
    static/index.html, app.js, style.css
tests/
  conftest.py               fresh DB copy per test, fixed clock, fake LLM
  test_identity.py          AC1–AC4a
  test_conversations.py     AC5
  test_input_clean.py       AC6
  test_shipments.py         AC8 / AC17 maths, AC16 lookup, active definition
  test_slots.py             AC9, AC10, AC11, AC18
  test_booking.py           AC12, AC12a, AC13, AC14, AC14a
  test_agent_flow.py        AC12 end to end with scripted fake model
```

## 4. Data

### 4.1 Building the database
- `make setup` (and `make reset-db`) runs the seed SQL, then everything in `data/migrations/`, into
  `data/app.db`. The original package `.db` is never written to.
- Every connection sets `PRAGMA foreign_keys = ON` and `PRAGMA busy_timeout = 5000`.
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
| Arrival time (R25) | `eta_updates` + `shipments.latest_eta_ts` | `source_type='DRIVER_DECLARED'`, `confidence_code='HIGH'` (driver confirmed), `delay_reason_code` from the conversation if clear, else `OTHER`. |
| Bookings (R21) | `appointments` | See §6.3. |

Using the seed chat tables means ownership (R5) is one check everywhere: *row's `driver_id` = session
user's `driver_id`*.

### 4.4 Time
- **Scheduling time** (`clock.now()`): fixed at `APP_CLOCK`, default `2026-08-04T09:40:00+05:30` (A3).
  Used for every rule and every operational timestamp (`booked_at`, `eta_updates.created_at`…).
  Tests can override it per test (AC11 uses 16:25).
- **Chat timestamps** (`message_ts`, `opened_at`): real wall clock. With a frozen clock every
  conversation would have the same time and "newest first" (R6) would not work. All are stored as
  ISO-8601 with `+05:30`.
- All comparisons parse to `datetime`; nothing compares timestamp strings.

### 4.5 IDs
New rows get prefixed short IDs: `USR-…`, `THR-…`, `MSG-…`, `ETA-…`, `APT-…` (prefix + 8 hex chars
from `uuid4`).

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

## 6. Scheduling layer (deterministic)

### 6.1 Shipments (`scheduling/shipments.py`)
- `RESCHEDULABLE = {PLANNED, ASSIGNED, IN_TRANSIT}` (spec §5).
- `driver_shipments(driver_id)` returns:
  - `active`: reschedulable shipments, each with a **driver label** built from data
    ("Textiles from Ajmer"), destination name, baseline ETA (`original_eta_ts`), latest ETA
    (`v_latest_eta.effective_eta_ts`), current appointment (dock code + time) if any;
  - `most_recent_other`: if `active` is empty, the latest non-reschedulable shipment with its status
    (R20a, AC16: DRV008 → SHP1019 cancelled).
- `resolve_arrival(shipment, *, clock_time=None, delay_minutes=None)`:
  - `clock_time` "11:20" → that time today (A3);
  - `delay_minutes` 60 → baseline ETA + 60 min (AC8: 10:00 → 11:00);
  - result earlier than `clock.now()` → `ArrivalInPast` (AC17).
  The LLM pulls the numbers out of the driver's words; **code does the maths**.

### 6.2 Slot rules (`scheduling/rules.py`)
One pure function used by both search and booking, so R23 re-uses exactly the same rules:

```python
def check_slot(shipment, slot, dock, *, arrival, finish_by, held_by, facility_rules) -> list[str]:
    """Return the list of failed rule IDs; empty list = slot is valid."""
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
- One query: slots at the destination facility from `arrival` onward, joined to `docks` and to the
  active appointment (same join as `v_slot_availability`).
- Filter with `check_slot`, sort by `(slot_start_ts, dock_code)`, take 5.
- Each result: `slot_id`, `dock_code`, start, end, and `is_current: bool` (Q2 / AC18).
- Expected results, checked by tests: AC9, AC10 (D2/D4 only), AC11 (empty), AC18.

### 6.4 Booking (`book_slot`) — R21–R24

```
book_slot(driver_id, shipment_id, slot_id, finish_by=None) -> BookingResult
  BEGIN IMMEDIATE                      -- takes the write lock up front
    shipment must belong to driver_id and be reschedulable      → else NOT_ALLOWED (R24)
    current = current active appointment of shipment
    if current.slot_id == slot_id                                → ALREADY_YOURS (R21a)
    arrival = latest ETA for shipment (recorded earlier, R25)
    failed = check_slot(... re-read now ...)                     → S5 failed: SLOT_TAKEN
                                                                   other:     RULE_FAILED (R23)
    UPDATE current  SET appointment_status='CANCELLED', is_current=0,
                        cancelled_at=now, cancellation_reason='Replaced by driver rebooking'
    INSERT new      appointment_status='CONFIRMED', booking_source='DRIVER_CHAT',
                    is_current=1, booked_at=now, confirmed_at=now (A2),
                    replaced_appointment_id=current.appointment_id
  COMMIT
  sqlite3.IntegrityError on ux_active_appointment_per_slot      → ROLLBACK, SLOT_TAKEN
```

- The old row is updated **before** the insert, because the seed has a unique index allowing only
  one current active appointment per shipment.
- `SLOT_TAKEN` results carry up to 5 fresh alternatives from `find_slots` (R22).
- Two layers protect against double booking (AC13): `BEGIN IMMEDIATE` makes writers queue, so the
  second one sees the first one's row and fails S5; the unique index catches anything that slips past.
- `BookingResult` = `status`, `appointment_id`, `dock_code`, `slot_start`, `released_appointment_id`,
  `alternatives`, `reason`.

### 6.5 Recording the arrival time (`record_eta`) — R25
Inserts into `eta_updates` and updates `shipments.latest_eta_ts` / `updated_at` in one transaction.
Called when the driver confirms the time, before slots are searched, so AC11 records 18:30 even
though nothing is booked.

## 7. Agent

### 7.1 Graph
A two-node LangGraph loop: **agent** (chat model with tools bound) → **tools** (runs tool calls) →
back to agent, until the model replies without a tool call. Checkpointed by conversation ID.

`run_turn(thread_id, driver_id, text) -> str` is the only entry point the web layer uses.

### 7.2 What the model is given every turn
The system prompt is rebuilt each turn from fresh data, so the model never has to ask for what the
system knows (R14) and often needs no lookup tool:
- current time (`clock.now()`), driver's first name;
- the driver's active shipments with labels, current slot, baseline and latest ETA — or the
  most-recent-other shipment and its status (R20a);
- the destination's warehouse coordinator name and phone (R13a);
- the behaviour rules from spec §6.4, written as short instructions (one question at a time, suggest
  the answer, only two things may be asked, give up after two unclear answers, stay on topic, never
  claim a booking unless `book_slot` returned `BOOKED`, short phone-friendly replies).

### 7.3 Tools
The driver ID comes from the run config (`config["configurable"]["driver_id"]`), never from the
model's arguments, so the model cannot act for another driver (R5, R24).

| Tool | Arguments from the model | Does |
|---|---|---|
| `resolve_arrival` | `shipment_id`, `clock_time` *or* `delay_minutes` | §6.1 maths; returns exact time or "in the past" |
| `record_arrival` | `shipment_id`, `arrival_time`, `reason?` | §6.5, only after the driver confirms the time |
| `find_slots` | `shipment_id`, `start_after?`, `finish_by?` | §6.3; arrival = recorded ETA, or `start_after` if later (AC9 "any slot after 12") |
| `book_slot` | `shipment_id`, `slot_id`, `finish_by?` | §6.4 |

Every tool checks ownership again and returns plain JSON. Tool errors are returned to the model as
short messages ("That shipment is not yours"), not raised.

### 7.4 Failure handling (spec §8)
- Model call timeout 30 s, one retry. On any model or graph error the driver gets
  "Sorry, I can't respond right now, please try again", which is stored as the agent message.
- If `book_slot` already returned `BOOKED` during that turn, the fallback reply is instead a
  confirmation built by code ("Booked: dock D2 at 12:00."), so the driver is never told "sorry"
  about a booking that happened (R17 in both directions).

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
| AC12, AC12a, AC14, AC14a | `test_booking.py` | checks DB rows: new `CONFIRMED`, APT1006 `CANCELLED`/not current, `replaced_appointment_id` set |
| AC13 | `test_booking.py` | 10 threads, own connections, a `threading.Barrier`, then count results |
| AC12 end to end | `test_agent_flow.py` | scripted fake model emits the tool calls; checks reply text and DB |
| AC8, AC16, AC17 (code parts) | `test_shipments.py` | arrival maths, past-time error, most-recent-other lookup |
| AC7, AC8, AC15, AC16, AC17, AC19, AC20 | **manual**, real model | script listed in README with expected behaviour |

`make test` runs all automated tests with no API key and no network.

## 10. Order of work (outline for `tasks.md`)

1. Skeleton: `pyproject.toml`, `Makefile`, `.env.example`, config + clock, database build. *(nothing to demo yet)*
2. Scheduling layer + its tests (§6). *Biggest risk, no LLM needed, ACs 9–14, 18.*
3. Identity, conversations, input cleaning + tests (§5).
4. Agent graph, prompt, tools, fake-model test (§7).
5. Web API + page (§8); walk through AC12 in the browser.
6. Manual LLM checks; README (setup, demo script, manual AC list).

## 11. Risks and how we handle them

| Risk | Mitigation |
|---|---|
| Model books without a clear choice (R16) or claims a booking that failed (R17) | Prompt rules + manual checks; R17 also enforced in code for the failure path (§7.4). A later spec could add a "confirm" button. |
| Model gets time maths wrong | It never does maths: `resolve_arrival` does (§6.1). |
| LangGraph / LangChain API changes between versions | Pin versions in `pyproject.toml`; the graph is small and hand-built. |
| SQLite "database is locked" under parallel requests | `busy_timeout`, short transactions, one connection per request/thread. |
| Seed chat history confuses the model | History is only shown in the UI; the agent's memory starts empty for each new conversation. |

## 12. Decisions (confirmed)

- **D1. ✅** Reuse the seed `chat_threads` / `chat_messages` tables for conversations instead of new
  tables (§4.3). Side effect: drivers with seeded threads see them as past conversations.
- **D2. ✅** Chat timestamps use the real clock; everything operational uses the fixed clock (§4.4).
- **D3. ✅** A plain HTML/JS page instead of Streamlit or a React app (§8).
- **Still open:** the exact OpenAI model name, pinned in `.env.example` when task T4.1 is done.
