
from __future__ import annotations

import sys

from plan_execute.graph import build_plan_execute_graph

DEFAULT_TASK = "What is the current share price of Apple?"


def main(task: str) -> None:
    graph = build_plan_execute_graph()
    config = {"recursion_limit": 50}

    print(f"\n=== GÖREV ===\n{task}\n")
    for event in graph.stream({"input": task}, config=config):
        for node, update in event.items():
            print(f"--- [{node}] ---")
            if "plan" in update:
                for i, step in enumerate(update["plan"], 1):
                    print(f"  {i}. {step}")
            if "past_steps" in update:
                for step, result in update["past_steps"]:
                    print(f"  ✓ {step}\n    → {result}")
            if update.get("response"):
                print(f"\n=== NİHAİ CEVAP ===\n{update['response']}")
            print()


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or DEFAULT_TASK
    main(task)
