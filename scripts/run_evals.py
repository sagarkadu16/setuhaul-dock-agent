"""Run the eval cases against the real agent and score them.

  uv run --env-file .env python scripts/run_evals.py           # LangSmith experiment
  uv run --env-file .env python scripts/run_evals.py --local   # print results, no LangSmith
  ... --case off_topic                                         # just one case

Uses the real LLM (a few cents per full run). LangSmith mode creates or updates the
dataset "setuhaul-driver-evals" and records an experiment you can compare across runs.
"""

import argparse
import sys

from setuhaul.evals.cases import CASES, SCENARIO
from setuhaul.evals.evaluators import ALL_EVALUATORS
from setuhaul.evals.harness import run_case

DATASET = "setuhaul-driver-evals"
FAIL_UNLESS_TRUE = {"expected_tools_called", "forbidden_tools_not_called",
                    "no_false_booking_shown", "no_leaks", "behaviour_judge"}


def _inputs(case: dict) -> dict:
    return {"case_id": case["id"], "driver_id": case["driver_id"],
            "turns": case["turns"], "scenario": SCENARIO}


def _reference(case: dict) -> dict:
    return {k: case[k] for k in ("expect_tools", "forbid_tools", "forbidden_text", "behaviour")}


def run_local(cases: list[dict]) -> int:
    failures = 0
    for case in cases:
        inputs, ref = _inputs(case), _reference(case)
        outputs = run_case(inputs)
        results = [ev(inputs, outputs, ref) for ev in ALL_EVALUATORS]
        failed = [r for r in results if r["key"] in FAIL_UNLESS_TRUE and not r["score"]]
        failures += bool(failed)
        print(f"\n{'FAIL' if failed else 'PASS'}  {case['id']}")
        print(f"  reply: {outputs['final_reply'][:200]}")
        for r in results:
            mark = "ok " if r["score"] else ("-- " if r["key"] == "guardrail_triggered" else "XX ")
            print(f"  {mark}{r['key']}: {r.get('comment', '')}")
    print(f"\n{len(cases) - failures}/{len(cases)} cases passed")
    return 1 if failures else 0


def sync_dataset(client, cases: list[dict]) -> None:
    """Create the dataset once; add any case ids it doesn't have yet."""
    if client.has_dataset(dataset_name=DATASET):
        dataset = client.read_dataset(dataset_name=DATASET)
    else:
        dataset = client.create_dataset(
            DATASET, description="SetuHaul driver chat: scripted conversations + expectations")
    have = {ex.inputs.get("case_id") for ex in client.list_examples(dataset_id=dataset.id)}
    new = [c for c in cases if c["id"] not in have]
    if new:
        client.create_examples(dataset_id=dataset.id,
                               inputs=[_inputs(c) for c in new],
                               outputs=[_reference(c) for c in new])


def run_langsmith(cases: list[dict], prefix: str) -> int:
    from langsmith import Client, evaluate

    client = Client()
    sync_dataset(client, cases)
    wanted = {c["id"] for c in cases}
    examples = [ex for ex in client.list_examples(dataset_name=DATASET)
                if ex.inputs.get("case_id") in wanted]
    results = evaluate(
        run_case, data=examples, evaluators=ALL_EVALUATORS,
        experiment_prefix=prefix, max_concurrency=0,   # one at a time: shared settings
        metadata={"scenario": SCENARIO}, client=client,
    )
    print(f"\nExperiment: {results.experiment_name}  (LangSmith → Datasets & Experiments → {DATASET})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--local", action="store_true", help="print results, skip LangSmith")
    parser.add_argument("--case", action="append", help="run only this case id (repeatable)")
    parser.add_argument("--prefix", default="driver-agent", help="experiment name prefix")
    args = parser.parse_args()

    cases = [c for c in CASES if not args.case or c["id"] in args.case]
    if not cases:
        sys.exit(f"No case matches {args.case}. Known: {[c['id'] for c in CASES]}")
    return run_local(cases) if args.local else run_langsmith(cases, args.prefix)


if __name__ == "__main__":
    sys.exit(main())
