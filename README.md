# setuhaul-dock-agent
Learning-first build of an AI agent that mediates truck drivers and warehouse dock slots (SetuHaul FDE challenge). LangGraph + deterministic capacity &amp; commit layer.

## Observability, guardrails and evals

LangSmith tracing of every chat turn and slot decision, an output guardrail that blocks false
"booked" claims, and an eval suite of 9 driver conversations scored in LangSmith.
See [docs/observability-evals.md](docs/observability-evals.md).

```bash
uv run --env-file .env python scripts/run_evals.py   # needs LLM + LangSmith keys in .env
```
