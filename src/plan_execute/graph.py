"""Plan-Execute StateGraph'ının kurulumu."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from plan_execute.nodes import (
    execute_step,
    plan_step,
    replan_step,
    should_end,
)
from plan_execute.state import PlanExecute


def build_plan_execute_graph():
    """Derlenmiş Plan-Execute grafiğini döndürür.

    Akış:  START → planner → executor → replanner → (executor | END)
    """
    workflow = StateGraph(PlanExecute)

    workflow.add_node("planner", plan_step)
    workflow.add_node("executor", execute_step)
    workflow.add_node("replanner", replan_step)

    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "executor")
    workflow.add_edge("executor", "replanner")
    workflow.add_conditional_edges(
        "replanner",
        should_end,
        {"executor": "executor", "__end__": END},
    )

    return workflow.compile()
