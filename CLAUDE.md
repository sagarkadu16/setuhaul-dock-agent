# SetuHaul Dock Agent — Level 1 (learning build)

## Goal of Level 1 (nothing more)
Demo: load a scenario where TWO drivers (two browser tabs) are late and want the
SAME dock slot. Both chat with the agent. The system sees both requests together,
ranks them by priority, gives the slot to one, and offers the other the next valid slot.
Build only what this demo needs — but design every part so it scales to many drivers.

In scope: email login linked to a driver (by phone) · chat history · input sanitising ·
FastAPI backend · GPT-4o tool-calling agent · Pydantic models · scenario loader
(YAML → working DB) · find valid slots · contested-window decision with priority ·
race-safe booking (BEGIN IMMEDIATE + unique index) · Streamlit chat UI · tests.
Out of scope: ops console, warehouse approval, breakdowns, after-21:00 rule, deployment.

## Level 2 (in progress)
Cognito passwordless auth and the path to deployment. Scope, decisions, steps and a
log of every Level 2 change: docs/level-2.md. Level 1 behaviour must keep working
under AUTH_MODE=local. Mark Level 2 commits `feat(l2):` / `fix(l2):`.

## Architecture rules
- The LLM talks and calls tools. Python decides slots, priority and bookings.
- Driver identity comes from the session, never from LLM output.
- Dependencies one way: ui → api → agent → tools → services → db.
- data/seed/ is read-only; scenarios load into data/working.db.
- "Now" = CURRENT_TIME from config/scenario, never datetime.now().
- Secrets only in .env.

## How to teach me (most important)
I am a senior React/TypeScript dev, intermediate Python, learning to build
production-grade backends. I type or paste every line myself.
1. One small step at a time. Each step = one concept + one small code change.
2. Before code: say what we're building, why, and where it fits in the architecture.
3. Give code in small snippets with the exact file path. After each snippet explain
   the important lines, and say WHY this way, the alternatives, and why not those.
4. Point out production concerns as they come up (validation, errors, security,
   concurrency, logging, testing) — briefly, with what we'd do at scale.
5. After each step: tell me how to verify it (command + expected output), then STOP
   and wait for me to say "done" or paste my result/error.
6. Review what I wrote when I ask; point out bugs and edge cases. Don't rewrite silently.
7. Never jump ahead or bundle several steps. Keep explanations short and concrete.
8. If something is ambiguous, ask me — don't invent requirements.

## Commands
uv sync · uv run pytest -q · uv run python scripts/load_scenario.py two_drivers_one_slot
uv run uvicorn setuhaul.api.main:app --reload · uv run streamlit run ui/app.py