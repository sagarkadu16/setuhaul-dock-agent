# Spec 002 — Book a slot from the chat

| | |
|---|---|
| Status | Placeholder — holds items moved out of spec 001 v4; not reviewed |
| Owner | Sagar |
| Depends on | [`../001-driver-chat-mvp/spec.md`](../001-driver-chat-mvp/spec.md) (slots are shown) |

> Starts where spec 001 stops: the driver has seen valid slots and picked one. This spec makes the
> chat book it. Requirement IDs are kept from spec 001 so history is easy to follow.
> Before work starts, re-check every criterion against the seed data, as was done for spec 001.

---

## Goal

**…see valid slots → pick one → slot is booked in the database → driver gets a confirmation message.**

## Moved from spec 001

### Assumption
- **A2 — Auto-approval.** If the booking logic accepts a slot, it is confirmed immediately. No
  warehouse confirmation step. An existing appointment that is still *pending confirmation* is
  treated like any other current appointment: rebooking releases it.

### Requirements
- **R16.** The agent books a slot only after the driver clearly chooses one.
- **R17.** The agent tells the driver a slot is booked **only** if the booking step reported success.
- **R21.** Booking a slot makes it the shipment's current appointment and releases the shipment's
  previous current appointment, if it has one, as one all-or-nothing change. The released appointment
  stays in history, marked as replaced by the new one.
- **R21a.** If the driver picks the slot they already hold, nothing changes and the agent tells them
  it is already their slot.
- **R22.** Two drivers can never hold the same slot. If two book the same slot at the same moment,
  exactly one succeeds; the other is told the slot was just taken and is offered alternatives.
- **R23.** Booking re-checks all slot rules at the moment of booking (the slot may have changed since
  it was offered).
- **R24.** A driver cannot book for a shipment that is not theirs or not reschedulable.
- **R25.** The new arrival time is recorded as soon as the driver confirms it, whether or not a slot
  is later booked.
- R26 from spec 001 (no operational writes) is replaced by the above.

### Acceptance criteria (as written for spec 001 v3)

| ID | Covers | Given | When | Then |
|---|---|---|---|---|
| AC11b | R25 | SHP1015 (reefer), clock 16:25 | ETA 18:30, no slots | ETA 18:30 is recorded |
| AC12 | R16-R17, R21 | DRV006, slots shown after "reaching 11:20" | "I'll take the 12 o'clock one on D2" | SLOT-JAI-019 booked; old appointment APT1006 released and kept in history; confirmation names dock D2 and 12:00 |
| AC12a | R21a | SHP1006 holds a 12:00 D2 booking | driver picks 12:00 D2 again | no change; agent says it is already their slot |
| AC13 | R22 | 10 bookings for the same slot at the same instant | — | exactly 1 succeeds, 9 told "slot taken" |
| AC14 | R24 | DRV006 logged in | tries to book for SHP1014 | refused |
| AC14a | R24 | DRV004 logged in | tries to book for SHP1004 (waiting in yard) | refused |

### Edge cases
- The driver picks a slot that was not offered → treated as a request; booking still validates it (R23).
- The driver sends the same message twice → no double booking (R22).
- The LLM fails after a booking succeeded → the driver still gets a confirmation built by code, never "sorry".
- The shipment has no current appointment (e.g. SHP1012, SHP1016, SHP1018) → booking simply creates one; nothing to release.

### Design already worked out
`../001-driver-chat-mvp/plan.md` v1 (git history, commit `062fc38`) has the booking design: one
`BEGIN IMMEDIATE` transaction, update old appointment before inserting the new one, the seed's
`ux_active_appointment_per_slot` index as a backstop, and a 10-thread race test.

## Open questions
- Should a warehouse confirmation step come first (spec 001 §9), or keep auto-approval?
- Should showing a slot hold it for a few minutes?
