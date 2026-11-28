"""Evals: scripted driver conversations, run against the real agent and scored.

cases.py      what to try and what "good" means (the dataset)
harness.py    runs one case end to end on a fresh copy of the scenario database
evaluators.py scores a run: code checks (tools, no false booking, no leaks) + an LLM judge
"""
