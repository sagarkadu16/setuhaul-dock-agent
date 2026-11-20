# Spec 001 — Driver chat MVP (show slots)

| | |
|---|---|
| Status | Approved v4 — scope cut to showing slots; booking moved to spec 002 |
| Owner | Sagar |
| Depends on | `specs/000-constitution.md` (optional for this MVP) |
| Next | `plan.md` (how), `tasks.md` (steps); booking is [`../002-booking/spec.md`](../002-booking/spec.md) |

> This file says **what** the system must do and **why**. It contains no implementation details.
> Anything about libraries, tables or code belongs in `plan.md`.

---

## 1. Problem

Truck drivers heading to the SetuHaul Jaipur Distribution Centre often run late. Today they have no
simple way to say "I'm late" and find out which unloading slots still work for them, so they arrive
and wait, or miss their dock entirely. We need a chat where a driver describes the delay in plain
English, gets clarifying questions when needed, and sees the slots that actually work for their truck.

Booking the chosen slot from the chat is the next step and is covered by spec 002. In this MVP the
driver confirms the slot by phone with the warehouse.

## 2. Goal of this MVP

One working end-to-end path:

**log in with email → see past chats → describe a delay → answer follow-up questions → see valid slots
→ pick one → get the warehouse coordinator's number to confirm it.**

The app **does not change any operational data** (shipments, appointments, ETAs). It only stores
users and chat messages. Speed of learning matters more than completeness; booking, warehouse
approval, priority rules and breakdown handling build on this path.

## 3. Actors

| Actor | In this MVP |
|---|---|
| Truck driver | Uses the chat UI |
| Chat agent (LLM) | Talks to the driver, asks follow-up questions, calls tools |
| Slot logic (deterministic code) | Decides which slots are valid for a shipment; read-only |
| Warehouse coordinator | Not in the app — the driver calls them to confirm a slot |

## 4. Assumptions

- **A1 — Email login is identification, not security.** Anyone who knows an email can log in. This is
  acceptable for a local prototype only.
- **A2 — No booking in the app.** *(Moved to spec 002: auto-approval of bookings.)* Slots shown are a
  snapshot; one may be taken by someone else before the driver calls.
- **A3 — Fixed clock.** The data is a snapshot of 4 Aug 2026. The system uses a configurable "current
  time" (default `2026-08-04T09:40:00+05:30`) instead of the real clock. All times the driver says
  ("11:20", "1 pm") are read as that same day in Asia/Kolkata.
- **A4 — One facility in practice.** Behaviour must work for any facility in the data, but demos use
  Jaipur (`FAC-JAI-01`).
- **A5 — English only.** Driver messages in other languages are out of scope.
- **A6 — Slot status already reflects dock events.** Breakdowns and maintenance in the seed data are
  already applied as blocked slots, so the MVP relies on slot status alone.
- **A7 — Long unloads are allowed to overrun.** Some loads need longer to unload than one slot
  (e.g. 75 min in a 60-min slot). In this MVP such a slot is still shown; handling the overrun is
  a later spec (see §9). The finish-by rule (S7) still uses the real unload time.

## 5. Definitions

- **Reschedulable shipment** — a shipment belonging to the driver whose status is planned, assigned
  or in transit. Shipments that are waiting in the yard, in a dock, completed or cancelled are not
  reschedulable.
- **Active shipment** — in this spec, the same as reschedulable.
- **Current appointment** — the shipment's present booking (confirmed or pending confirmation), if it
  has one.
- **Baseline ETA** — the shipment's originally planned arrival time. A relative delay ("late by one
  hour") is added to this.

## 6. Functional requirements

### 6.1 Login and identity
- **R1.** A user logs in by entering an email address. No password.
- **R2.** The email is stored the first time it is used. Logging in again with the same email
  (case-insensitive, trimmed) returns the same user.
- **R3.** An invalid email format is rejected with a clear message.
- **R4.** Each user is linked to exactly one driver from the data. If the email is not linked yet, the
  user must link it by entering the driver's phone number, which must match a driver in the data.
  Phone matching compares only the last 10 digits, ignoring spaces, dashes and any `+91` prefix, so
  `+91-9000010006`, `9000010006` and `+91 90000 10006` all match the same driver.
- **R4a.** A driver can be linked to only one email. Linking a second email to an already-linked
  driver is rejected with a message to contact dispatch.
- **R4b.** A link between an email and a driver is permanent. There is no way to unlink or relink it
  in the app; a wrong link is fixed by an admin outside the app.
- **R5.** A user can only see their own conversations and slots for their own driver's shipments.

### 6.2 Conversations
- **R6.** After login, the user sees a list of their past conversations, newest first.
- **R7.** The user can open a past conversation and see all its messages in order.
- **R8.** The user can start a new conversation.
- **R9.** Every driver message and every agent reply is stored and survives an app restart.

### 6.3 Input handling
- **R10.** Driver input is cleaned before it reaches the LLM: surrounding whitespace removed, control
  characters removed, empty messages rejected, messages longer than 1,000 characters rejected.
- **R11.** Cleaning must not change the meaning of the message (no rewriting, no translation).

### 6.4 Agent behaviour
- **R12.** The agent understands plain-English delay reports, e.g. "stuck in traffic, reaching around
  11:20", "late by one hour", "tyre burst, will be 45 min late".
- **R13.** The agent asks as few follow-up questions as possible, and each one is easy for a driver
  to answer.
  - **Only two things are ever asked for:** which shipment (only if the driver has more than one
    active shipment) and the exact arrival time (only if it was not given, was relative or vague, or
    is earlier than the current time). Nothing else is asked; the driver may add a constraint such as
    "must finish by 2 pm" on their own.
  - **One short question per message**, in plain words, with no shipment IDs or jargon on their own.
  - **Questions suggest the answer** so the driver can reply "yes" or pick a number:
    - shipment (DRV007): "Which delivery is late? 1) Textiles from Ajmer  2) Construction equipment
      from Mumbai";
    - relative delay: the delay is added to the baseline ETA and the agent confirms it
      (DRV013, planned 10:00, "late by one hour" → "So you will reach at about 11:00?").
  - **Work things out instead of asking** whenever one message gives enough, e.g. "the Jaipur one,
    reaching 11:20" answers both questions at once.
  - When showing slots, the agent adds one short line such as "Tell me if you must finish by a
    certain time." instead of asking about constraints.
- **R13a.** If the driver still has not given a clear answer after being asked for the same thing
  twice, the agent stops asking and suggests calling the destination facility's warehouse
  coordinator, giving their phone number (Jaipur: +91-141-5001101).
- **R14.** The agent never asks for information the system already has (load type, weight, facility,
  current slot).
- **R15.** The agent only shows slots returned by the slot logic. It never invents or edits a slot.
- **R16.** When the driver picks a slot, the agent repeats the choice (dock and time) and gives the
  destination warehouse coordinator's name and phone number to confirm it.
  *(Booking from the chat: moved to spec 002.)*
- **R17.** The agent never says or implies that a slot is booked, reserved or held. It says the slot
  is *available now* and must be confirmed by phone.
- **R18.** If no valid slot exists, the agent says so plainly and gives the coordinator's number.
- **R19.** The agent stays on topic (delays, slots). Off-topic requests get a short polite refusal.
- **R20.** Replies are short, simple English suitable for a driver on a phone.
- **R20a.** If the driver has no active shipment, the agent looks at their most recent shipment and
  explains its status (e.g. cancelled → "This shipment was cancelled. Please contact dispatch.").
  It does not show slots.

### 6.5 Slot rules (decided by code, not the LLM)
A slot is shown for a shipment only if **all** are true:
- **S1.** The slot belongs to the shipment's destination facility.
- **S2.** The dock type matches the load: temperature-controlled → reefer dock; heavy load (above the
  facility's heavy-weight rule, or marked heavy) → heavy dock; otherwise → standard dock.
- **S3.** The load weight is ≤ the dock's maximum weight.
- **S4.** The slot status is open.
- **S5.** No active booking (confirmed, pending confirmation or in progress) of *another* shipment
  holds the slot. The shipment's own current slot passes this rule.
- **S6.** The slot starts at or after the driver's new arrival time.
- **S7.** If the driver gave a "finish by" time, unloading (start + expected unload minutes) ends by then.

Slots are listed earliest start first, then by dock code, max 5. If the shipment's current slot
passes all rules, it is included in its normal place in the list and marked "your current slot".

### 6.6 Read-only guarantee
- **R24.** Slots can only be looked up for a shipment that is the driver's own and reschedulable.
- **R26.** No conversation changes shipments, appointments, slots or ETA records. The only data the
  app writes is users and chat messages.
- *R21–R23 (booking, double-booking protection, re-check at booking time) and R25 (recording the new
  arrival time): moved to spec 002.*

## 7. Acceptance criteria

Real records from the seed data are used so each criterion can become a test. Each test starts from a
fresh copy of the seed data.

| ID | Covers | Given | When | Then |
|---|---|---|---|---|
| AC1 | R1-R2 | no user `manoj@test.com` | log in twice with ` Manoj@Test.com ` | one user exists; both logins return it |
| AC2 | R3 | — | log in with `manoj@` | rejected: invalid email |
| AC3 | R4 | new email | link phone `+91-9000010006` | user linked to driver DRV006 |
| AC3a | R4 | new email | link phone `9000010006` | user linked to driver DRV006 |
| AC4 | R4 | new email | link phone `+91-0000000000` | rejected: no such driver |
| AC4a | R4a | DRV006 already linked to `a@test.com` | `b@test.com` links phone `+91-9000010006` | rejected |
| AC5 | R6-R9 | user has 2 conversations | logs in again after restart | sees both, newest first, with all messages |
| AC6 | R10 | logged in | sends "   " or 1,200 characters | rejected before reaching the LLM |
| AC7 | R13 | DRV007 (SHP1007 and SHP1016, both in transit) | "I will be late by 45 minutes" | agent asks which shipment |
| AC8 | R13 | DRV013 (SHP1013, planned 10:00) | "I am late by one hour" | agent asks to confirm arrival at about 11:00 |
| AC9 | R15, S2, S6, ordering | DRV006, SHP1006 chosen | "reaching 11:20, any slot after 12?" | shown, in order: D2 12:00, D4 12:00, D1 13:00, D2 13:00, D3 13:00 — all standard docks |
| AC10 | S3, A7 | SHP1014 (21,000 kg, 75-min unload) | asks for slots | only D2 / D4 slots; none on D1 or D3 (20,000 kg max) |
| AC11 | S2, S4, R18 | SHP1015 (reefer), clock 16:25 | ETA 18:30 | no slots shown; agent says none available and gives the coordinator's number |
| AC12 | R13, R15-R17 | DRV006 logged in | delay report → agent asks which shipment → "the Jaipur one" → "reaching 11:20" → slots shown → "I'll take the 12 o'clock one on D2" | agent repeats "D2 at 12:00", gives +91-141-5001101 to confirm, and does not say it is booked |
| AC14 | R24 | DRV006 logged in | slots requested for SHP1014 (not theirs) | refused |
| AC14a | R24 | DRV004 logged in | slots requested for SHP1004 (waiting in yard) | refused |
| AC15 | R19 | logged in | "write me a poem" | short polite refusal, no tool calls |
| AC16 | R20a | DRV008 (all shipments cancelled) | "what time is my slot?" | agent says SHP1019 is cancelled and to contact dispatch; no slots shown |
| AC17 | R13 | DRV006, SHP1006, clock 09:40 | "reaching 9:00" | agent asks for the arrival time again |
| AC18 | S5, Q2 | DRV001, SHP1017 (current slot D1 12:00) | "reaching 11:30" | shown, in order: D1 12:00 marked "your current slot", D2 12:00, D4 12:00, D1 13:00, D2 13:00 |
| AC19 | R13 | DRV006 | "the Jaipur one, reaching 11:20" as the first message | no follow-up question; slots shown straight away |
| AC20 | R13a | DRV013 | answers "not sure" twice to the arrival-time question | agent stops asking and gives the Jaipur warehouse coordinator's number |
| AC21 | R26 | fresh seed data | the full AC12 conversation | shipments, appointments, appointment slots and ETA updates are identical to the seed |

*AC12a and AC13 (re-picking your own slot, simultaneous bookings): moved to spec 002.*

## 8. Edge cases

- The driver changes their mind after seeing the slots ("actually make it 1 pm") → show slots again.
- The driver picks a slot that was not shown → the agent checks it with the slot logic; if it is not
  valid, the agent says so and shows the valid ones.
- The driver says "book it" → the agent explains it cannot book yet and gives the coordinator's number.
- The driver sends the same message twice → the duplicate chat message is just stored twice.
- The LLM service is down or times out → the driver sees "Sorry, I can't respond right now, please try again".
- The shipment is cancelled (SHP1019) → covered by R20a / AC16.
- The driver's shipment is already in the yard or at a dock (e.g. SHP1004, SHP1002) → not reschedulable; agent tells them to speak to the gate / dispatch.

## 9. Out of scope (later specs)

- **Booking from the chat, double-booking protection, recording new ETAs → spec 002**
- Warehouse confirmation and the ops console
- Priority between shipments (critical vs low)
- Dock breakdown and maintenance *events* (beyond the blocked slots already in the data), unloading overruns, after-21:00 approval
- Unloads longer than a slot (shown anyway in this MVP, see A7)
- Rescheduling trucks already in the yard or at a dock
- Real WhatsApp integration, passwords/OTP, multiple languages
- Deploying anywhere other than a laptop

## 10. Decisions (formerly open questions)

- **Q1.** Can a user unlink or relink their email? **No** — see R4b.
- **Q2.** Should the agent show the driver's current slot if it is still valid? **Yes**, marked
  "your current slot" — see S5 and AC18.
- **Q3.** How many follow-up questions? **As few as possible, each easy to answer** — only shipment
  and arrival time are ever asked, answers are suggested, and after two unclear answers to the same
  question the agent hands over to the coordinator — see R13, R13a, AC19, AC20.
- **Q4.** Book slots in the MVP? **No** — show them only; booking is spec 002. New ETAs are not
  recorded either, so the app never changes operational data (R26).

## 11. Definition of done

- Every acceptance criterion has a passing automated test, except AC7, AC8, AC15, AC16, AC17, AC19 and
  AC20, which need the real LLM and are checked manually and listed in the README. (Their
  deterministic parts — e.g. AC16's "no active shipment" lookup — still get automated tests.)
- A new person can clone the repo, add an OpenRouter API key, run two commands and complete the AC12
  flow in the browser.

## 12. Change log

### v4 — scope cut to showing slots
- Goal ends at "see valid slots → pick one → get the coordinator's number"; nothing is booked.
- R16, R17, R18 reworded for showing instead of booking; R24 now limits who can *see* slots;
  new R26 (no operational writes) and AC21.
- Moved to spec 002: A2 (auto-approval), R21–R23, R25, AC12a, AC13, and the booking edge cases.
- AC11 no longer records the ETA; AC12 ends with the coordinator's number instead of a booking.

### v2–v3 — review against seed data
- Defined *reschedulable / active shipment* and *baseline ETA* (§5).
- DRV006 also has SHP1021 (Gurugram), so AC9 and AC12 include the "which shipment?" turn.
- AC7 moved from DRV004 to DRV007: under the new definition DRV004 has only one active shipment
  (SHP1004 is already in the yard).
- Added R20a / AC16 so the cancelled-shipment case (DRV008 / SHP1019) still works.
- Added A7: 75-min unloads (SHP1014, SHP1009, SHP1005) in 60-min slots are shown anyway.
- Added tie-break by dock code; AC9 lists the exact expected slots.
- Added past-ETA re-ask (R13 / AC17), phone normalisation (R4), one-email-per-driver (R4a).
- Resolved Q1–Q3: permanent email link (R4b); current slot shown and marked (S5, AC18); minimal,
  easy-to-answer follow-ups with a coordinator hand-off (R13, R13a, AC19, AC20).
