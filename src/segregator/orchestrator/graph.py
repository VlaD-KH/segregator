"""
src/segregator/orchestrator/graph.py
Оркестратор мультиагентного графа Segregator.
Реализует направленный граф выполнения (DAG / StateGraph) с поддержкой условных переходов,
шага +0 (дельта-синхронизация) и шлюза Human-in-the-Loop.
"""

from typing import Callable, Dict, List, Any, Optional
from segregator.orchestrator.state import AccountingGraphState
from segregator.orchestrator.nodes import (
    step0_reconciler_node,
    agent01_ingest_node,
    agent02_accounting_node,
    agent03_tax_node,
    human_review_node,
    agent04_compliance_node,
    human_gate_condition,
)


class StateGraph:
    """
    Легковесный детерминированный движок графа состояний (DAG).
    Полностью совместим по сигнатурам и контрактам с LangGraph StateGraph.
    """

    def __init__(self, state_schema=AccountingGraphState):
        self.state_schema = state_schema
        self.nodes: Dict[str, Callable[[AccountingGraphState], AccountingGraphState]] = {}
        self.edges: Dict[str, str] = {}
        self.conditional_edges: Dict[str, tuple[Callable[[AccountingGraphState], str], Dict[str, str]]] = {}
        self.entry_point: Optional[str] = None

    def add_node(self, name: str, func: Callable[[AccountingGraphState], AccountingGraphState]):
        self.nodes[name] = func

    def set_entry_point(self, name: str):
        self.entry_point = name

    def add_edge(self, source: str, target: str):
        self.edges[source] = target

    def add_conditional_edges(self, source: str, condition_func: Callable[[AccountingGraphState], str], mapping: Dict[str, str]):
        self.conditional_edges[source] = (condition_func, mapping)

    def compile(self):
        return CompiledGraph(self)


class CompiledGraph:
    """Скомпилированный исполняемый граф."""

    def __init__(self, graph: StateGraph):
        self.graph = graph

    def invoke(self, initial_state: AccountingGraphState) -> AccountingGraphState:
        """Пошаговое выполнение графа от точки входа до завершения."""
        current_state = initial_state
        current_node_name = self.graph.entry_point

        while current_node_name and current_node_name != "END":
            # Выполнение функции текущего узла
            node_func = self.graph.nodes.get(current_node_name)
            if not node_func:
                break
                
            current_state = node_func(current_state)

            # Если задача пропущена из-за пустой дельты на шаге 0 -> завершаем
            if current_state.is_delta_empty:
                break

            # Определение следующего перехода
            if current_node_name in self.graph.conditional_edges:
                cond_func, mapping = self.graph.conditional_edges[current_node_name]
                branch_key = cond_func(current_state)
                next_node = mapping.get(branch_key, "END")
            else:
                next_node = self.graph.edges.get(current_node_name, "END")

            current_node_name = next_node

        return current_state


def build_accounting_graph() -> CompiledGraph:
    """
    Фабрика создания эталонного мультиагентного графа Segregator.
    Цепочка: Step +0 -> Agent 01 -> Agent 02 -> Agent 03 -> Human Gate -> Agent 04 -> END.
    """
    workflow = StateGraph(AccountingGraphState)

    # 1. Регистрация узлов
    workflow.add_node("step0_reconciler", step0_reconciler_node)
    workflow.add_node("agent01_ingest", agent01_ingest_node)
    workflow.add_node("agent02_accounting", agent02_accounting_node)
    workflow.add_node("agent03_tax", agent03_tax_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("agent04_compliance", agent04_compliance_node)

    # 2. Определение связей (Edges)
    workflow.set_entry_point("step0_reconciler")
    workflow.add_edge("step0_reconciler", "agent01_ingest")
    workflow.add_edge("agent01_ingest", "agent02_accounting")
    workflow.add_edge("agent02_accounting", "agent03_tax")

    # 3. Условный переход (Conditional Edge) после Agent-03
    workflow.add_conditional_edges(
        "agent03_tax",
        human_gate_condition,
        {
            "human_review": "human_review",
            "continue": "agent04_compliance",
            "end": "END"
        }
    )

    workflow.add_edge("human_review", "END")
    workflow.add_edge("agent04_compliance", "END")

    return workflow.compile()
