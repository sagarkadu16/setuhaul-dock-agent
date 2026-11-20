# Spec 001 — Driver chat MVP

| | |
|---|---|
| Status | Approved v3 — review comments applied, open questions decided |
| Owner | Sagar |
| Depends on | `specs/000-constitution.md` (optional for this MVP) |
| Next | `plan.md` (how), `tasks.md` (steps) |

> This file says **what** the system must do and **why**. It contains no implementation details.
> Anything about libraries, tables or code belongs in `plan.md`.

---

## 1. Problem

Truck drivers heading to the SetuHaul Jaipur Distribution Centre often run late. Today they have no
simple way to say "I'm late" and get a new unloading slot, so they arrive and wait, or miss their dock
entirely. We need a chat where a driver describes the delay in plain English, gets clarifying
questions when needed, sees the slots that actually work for their truck, picks one, and gets it
booked.

## 2. Goal of this MVP

One working end-to-end path:

**log in with email → see past chats → describe a delay → answer follow-up questions → see valid slots
→ pick one → slot is booked in the database → driver gets a confirmation message.**

Speed of learning matters more than completeness. Every later feature (warehouse approval, priority
rules, breakdown handling) builds on this path.

## 3. Actors

| Actor | In this MVP |
|---|---|
| Truck driver | Uses the chat UI |
| Chat agent (LLM) | Talks to the driver, asks follow-up questions, calls tools |
| Booking logic (deterministic code) | Decides which slots are valid and writes the booking |
| Warehouse / ops | **Not involved** — bookings are auto-approved (see A2) |

## 4. Assumptions

- **A1 — Email login is identification, not security.** Anyone who knows an email can log in. This is
  acceptable for a local prototype only.
- **A2 — Auto-approval.** If the booking logic accepts a slot, it is confirmed immediately. No warehouse
  confirmation step in this MVP. An existing appointment that is still *pending confirmation* is
  treated like any other current appointment: rebooking releases it.
- **A3 — Fixed clock.** The data is a snapshot of 4 Aug 2026. The system uses a configurable "current
  time" (default `2026-08-04T09:40:00+05:30`) instead of the real clock. All times the driver says
  ("11:20", "1 pm") are read as that same day in Asia/Kolkata.
- **A4 — One facility in practice.** Behaviour must work for any facility in the data, but demos use
  Jaipur (`FAC-JAI-01`).
- **A5 — English only.** Driver messages in other languages are out of scope.
- **A6 — Slot status already reflects dock events.** Breakdowns and maintenance in the seed data are
  already applied as blocked slots, so the MVP relies on slot status alone.
- **A7 — Long unloads are allowed to overrun.** Some loads need longer to unload than one slot
  (e.g. 75 min in a 60-min slot). In this MVP such a slot is still offered; handling the overrun is
  a later spec (see §8). The finish-by rule (S7) still uses the real unload time.

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
- **R5.** A user can only see and act on their own conversations and their own driver's shipments.

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
  - When offering slots, the agent adds one short line such as "Tell me if you must finish by a
    certain time." instead of asking about constraints.
- **R13a.** If the driver still has not given a clear answer after being asked for the same thing
  twice, the agent stops asking and suggests calling the destination facility's warehouse
  coordinator, giving their phone number (Jaipur: +91-141-5001101).
- **R14.** The agent never asks for information the system already has (load type, weight, facility,
  current slot).
- **R15.** The agent only offers slots returned by the booking logic. It never invents or edits a slot.
- **R16.** The agent books a slot only after the driver clearly chooses one.
- **R17.** The agent tells the driver a slot is booked **only** if the booking step reported success.
- **R18.** If no valid slot exists, the agent says so plainly and does not book anything.
- **R19.** The agent stays on topic (delays, slots, bookings). Off-topic requests get a short polite
  refusal.
- **R20.** Replies are short, simple English suitable for a driver on a phone.
- **R20a.** If the driver has no active shipment, the agent looks at their most recent shipment and
  explains its status (e.g. cancelled → "This shipment was cancelled. Please contact dispatch.").
  It does not offer slots.

### 6.5 Slot rules (decided by code, not the LLM)
A slot is offered for a shipment only if **all** are true:
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

### 6.6 Booking
- **R21.** Booking a slot makes it the shipment's current appointment and releases the shipment's
  previous current appointment, if it has one, as one all-or-nothing change. The released appointment
  stays in history, marked as replaced by the new one.
- **R21a.** If the driver picks the slot they already hold, nothing changes and the agent tells them
  it is already their slot.
- **R22.** Two drivers can never hold the same slot. If two book the same slot at the same moment,
  exactly one succeeds; the other is told the slot was just taken and is offered alternatives.
- **R23.** Booking re-checks all slot rules at the moment of booking (the slot may have changed since it
  was offered).
- **R24.** A driver cannot book for a shipment that is not theirs or not reschedulable.
- **R25.** The new arrival time is recorded as soon as the driver confirms it, whether or not a slot
  is later booked.

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
| AC9 | R15, S2, S6, ordering | DRV006, SHP1006 chosen | "reaching 11:20, any slot after 12?" | offered, in order: D2 12:00, D4 12:00, D1 13:00, D2 13:00, D3 13:00 — all standard docks |
| AC10 | S3, A7 | SHP1014 (21,000 kg, 75-min unload) | asks for slots | only D2 / D4 slots; none on D1 or D3 (20,000 kg max) |
| AC11 | S2, S4, R18 | SHP1015 (reefer), clock 16:25 | ETA 18:30 | no slots offered; agent says none available; ETA 18:30 is recorded |
| AC12 | R13, R16-R17, R21 | DRV006 logged in | delay report → agent asks which shipment → "the Jaipur one" → "reaching 11:20" → slots offered → "I'll take the 12 o'clock one on D2" | slot SLOT-JAI-019 booked; old appointment APT1006 released and kept in history; confirmation names dock D2 and 12:00 |
| AC12a | R21a | SHP1006 holds a 12:00 D2 booking | driver picks 12:00 D2 again | no change; agent says it is already their slot |
| AC13 | R22 | 10 bookings for the same slot at the same instant | — | exactly 1 succeeds, 9 told "slot taken" |
| AC14 | R24 | DRV006 logged in | tries to book for SHP1014 | refused |
| AC14a | R24 | DRV004 logged in | tries to book for SHP1004 (waiting in yard) | refused |
| AC15 | R19 | logged in | "write me a poem" | short polite refusal, no tool calls |
| AC16 | R20a | DRV008 (all shipments cancelled) | "what time is my slot?" | agent says SHP1019 is cancelled and to contact dispatch; no slots offered |
| AC17 | R13 | DRV006, SHP1006, clock 09:40 | "reaching 9:00" | agent asks for the arrival time again |
| AC18 | S5, Q2 | DRV001, SHP1017 (current slot D1 12:00) | "reaching 11:30" | offered, in order: D1 12:00 marked "your current slot", D2 12:00, D4 12:00, D1 13:00, D2 13:00 |
| AC19 | R13 | DRV006 | "the Jaipur one, reaching 11:20" as the first message | no follow-up question; slots offered straight away |
| AC20 | R13a | DRV013 | answers "not sure" twice to the arrival-time question | agent stops asking and gives the Jaipur warehouse coordinator's number |

## 8. Edge cases

- The driver changes their mind after seeing the slots ("actually make it 1 pm") → offer slots again; book nothing until they choose.
- The driver picks a slot that was not offered → treated as a request; booking still validates it (R23).
- The driver sends the same message twice → no double booking (R22 covers bookings; a duplicate chat message is just stored twice).
- The LLM service is down or times out → the driver sees "Sorry, I can't respond right now, please try again"; nothing is booked.
- The shipment is cancelled (SHP1019) → covered by R20a / AC16.
- The shipment has no current appointment (e.g. SHP1012, SHP1016, SHP1018) → booking simply creates one; nothing to release.
- The driver's shipment is already in the yard or at a dock (e.g. SHP1004, SHP1002) → not reschedulable; agent tells them to speak to the gate / dispatch.

## 9. Out of scope (later specs)

- Warehouse confirmation and the ops console
- Priority between shipments (critical vs low)
- Dock breakdown and maintenance *events* (beyond the blocked slots already in the data), unloading overruns, after-21:00 approval
- Unloads longer than a slot (offered anyway in this MVP, see A7)
- Rescheduling trucks already in the yard or at a dock
- Real WhatsApp integration, passwords/OTP, multiple languages
- Deploying anywhere other than a laptop

## 10. Decisions (formerly open questions)

- **Q1.** Can a user unlink or relink their email? **No** — see R4b.
- **Q2.** Should the agent offer the driver's current slot if it is still valid? **Yes**, marked
  "your current slot" — see S5 and AC18.
- **Q3.** How many follow-up questions? **As few as possible, each easy to answer** — only shipment
  and arrival time are ever asked, answers are suggested, and after two unclear answers to the same
  question the agent hands over to dispatch — see R13, R13a, AC19, AC20.

## 11. Definition of done

- Every acceptance criterion has a passing automated test, except AC7, AC8, AC15, AC16, AC17, AC19 and AC20, which
  need the real LLM and are checked manually and listed in the README. (Their deterministic parts —
  e.g. AC16's "no active shipment" lookup — still get automated tests.)
- A new person can clone the repo, add an OpenAI key, run two commands and complete the AC12 flow in
  the browser.

## 12. Changes from v1 (review against seed data)

- Defined *reschedulable / active shipment* and *baseline ETA* (§5).
- DRV006 also has SHP1021 (Gurugram), so AC9 and AC12 now include the "which shipment?" turn.
- AC7 moved from DRV004 to DRV007: under the new definition DRV004 has only one active shipment
  (SHP1004 is already in the yard).
- Added R20a / AC16 so the cancelled-shipment case (DRV008 / SHP1019) still works.
- Added A7: 75-min unloads (SHP1014, SHP1009, SHP1005) in 60-min slots are offered anyway; AC10 now has a defined result.
- Added tie-break by dock code; AC9 lists the exact expected slots.
- Added R21a (re-picking own slot), past-ETA re-ask (R13 / AC17), phone normalisation (R4),
  one-email-per-driver (R4a), and ETA recording timing (R25).
- Noted that pending-confirmation appointments are released on rebooking (A2) and that slot status
  already reflects dock events (A6).
- Resolved Q1–Q3: permanent email link (R4b); current slot offered and marked (S5, AC18); minimal,
  easy-to-answer follow-ups with a dispatch hand-off (R13, R13a, AC19, AC20). The "finish by"
  question is no longer asked.
- Status: Approved — ready for `plan.md`.
