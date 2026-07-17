from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agent.config import AgentMode
from agent.decision import DecisionProvider, RuleBasedDecisionProvider
from agent.domain import (
    CycleStatus,
    DecisionBundle,
    KillSwitchState,
    ResearchBundle,
    StrategySignal,
    StructuredReason,
)
from agent.execution import ExecutionService
from agent.guardrails import GuardrailEngine
from agent.market import MarketDataProvider
from agent.repository import Repository
from agent.research import NeutralResearchProvider, ResearchProvider
from agent.strategies import Strategy, run_strategies
from llm_schemas import FeatureSheet


class AgentState(TypedDict, total=False):
    cycle_id: str
    mode: str
    status: str
    kill_switch_state: str
    market_source: str
    market_quality_warnings: list[str]
    market_snapshot: dict
    research: dict
    research_status: str
    strategy_signals: list[dict]
    decision: dict
    decision_provenance: str
    decision_status: str
    health_status: str
    guardrail_verdicts: list[dict]
    approved_orders: list[dict]
    executions: list[dict]
    reconciliations: list[dict]
    positions: list[dict]
    incidents: list[dict]
    allow_external_research: bool
    allow_strategist_refresh: bool


@dataclass
class GraphDependencies:
    mode: AgentMode
    equity_usd: float
    repository: Repository
    market: MarketDataProvider
    strategies: list[Strategy]
    research: ResearchProvider
    decisions: DecisionProvider
    guardrails: GuardrailEngine
    execution: ExecutionService
    activity_callback: Callable[[str, str | None], None] | None = None


def build_graph(deps: GraphDependencies):
    def activity(phase: str, detail: str | None = None) -> None:
        if deps.activity_callback:
            deps.activity_callback(phase, detail)

    def check_killswitch(state: AgentState) -> dict:
        activity("PREPARING", "Vérification des conditions d’exécution")
        current = deps.repository.current_kill_switch()
        deps.repository.add_event(
            "KILL_SWITCH_READ",
            {"door": "cycle_entry", "state": current.value},
            cycle_id=state["cycle_id"],
        )
        return {"kill_switch_state": current.value}

    def route_after_killswitch(state: AgentState) -> Literal["ingest", "finalize"]:
        return "ingest" if state["kill_switch_state"] == "RUNNING" else "finalize"

    def ingest(_: AgentState) -> dict:
        activity("MARKET_DATA", "Synchronisation des prix et de la liquidité")
        return {
            "market_source": deps.market.name,
            "market_quality_warnings": deps.market.quality_warnings,
            "market_snapshot": deps.market.snapshot().model_dump(mode="json"),
        }

    def research_node(state: AgentState) -> dict:
        activity("RESEARCH", "Recherche de catalyseurs et signaux externes")
        feature_sheet = FeatureSheet.model_validate(state["market_snapshot"])
        try:
            bundle = deps.research.research(
                feature_sheet,
                cycle_id=state["cycle_id"],
                allow_refresh=state.get("allow_external_research", True),
            )
            return {
                "research": bundle.model_dump(mode="json"),
                "research_status": "NOMINAL",
            }
        except Exception as exc:
            deps.repository.add_event(
                "RESEARCH_FAILED",
                {"error": type(exc).__name__, "detail": str(exc)[:1000]},
                cycle_id=state["cycle_id"],
                severity="WARN",
            )
            neutral = NeutralResearchProvider().research(feature_sheet)
            return {
                "research": neutral.model_dump(mode="json"),
                "research_status": "DEGRADED",
                "incidents": [{"type": "RESEARCH_FAILED", "safe_fallback": "FLAT"}],
            }

    def strategies_node(state: AgentState) -> dict:
        activity("ANALYSIS", "Calcul des signaux techniques et du régime")
        sheet = FeatureSheet.model_validate(state["market_snapshot"])
        signals = run_strategies(sheet, deps.strategies)
        return {"strategy_signals": [s.model_dump(mode="json") for s in signals]}

    def decide_node(state: AgentState) -> dict:
        activity("DECISION", "Construction et révision du plan de trading")
        sheet = FeatureSheet.model_validate(state["market_snapshot"])
        signals = [StrategySignal.model_validate(s) for s in state["strategy_signals"]]
        research = ResearchBundle.model_validate(state["research"])
        positions = deps.execution.positions()
        try:
            bundle = deps.decisions.decide(
                sheet,
                signals,
                research,
                positions,
                equity_usd=deps.equity_usd,
                cycle_id=state["cycle_id"],
                allow_strategist_refresh=state.get("allow_strategist_refresh", True),
            )
        except Exception as exc:
            # I6: an LLM failure cannot fall through to a risk-increasing action.
            baseline = RuleBasedDecisionProvider().decide(
                sheet,
                signals,
                research,
                positions,
                equity_usd=deps.equity_usd,
            )
            held = baseline.trader.model_copy(
                update={
                    "decisions": [
                        d.model_copy(
                            update={
                                "action": "HOLD",
                                "direction": None,
                                "size_frac": 0.0,
                                "rationale": "LLM failure: fail-closed HOLD for this cycle.",
                            }
                        )
                        for d in baseline.trader.decisions
                    ]
                }
            )
            reason = StructuredReason(
                code="DECISION_PROVIDER_FAILED",
                message="Decision generation failed; every asset was forced to HOLD.",
                impact="BLOCKS",
                evidence={
                    "error_type": type(exc).__name__,
                    "error_detail": str(exc)[:500],
                },
            )
            bundle = baseline.model_copy(
                update={
                    "trader": held,
                    "provider": "safe-hold",
                    "provenance": "SAFE_HOLD",
                    "status": "DEGRADED",
                    "reasons": [*baseline.reasons, reason][:8],
                }
            )
            deps.repository.add_event(
                "DECISION_FAILED",
                {
                    "error": type(exc).__name__,
                    "detail": str(exc)[:1000],
                    "fallback": "HOLD",
                    "provenance": "SAFE_HOLD",
                    "status": "DEGRADED",
                },
                cycle_id=state["cycle_id"],
                severity="ERROR",
            )
        result = {
            "decision": bundle.model_dump(mode="json"),
            "decision_provenance": bundle.provenance,
            "decision_status": bundle.status,
        }
        if bundle.status == "DEGRADED":
            result["incidents"] = [
                *state.get("incidents", []),
                {
                    "type": "DECISION_DEGRADED",
                    "provenance": bundle.provenance,
                    "reason_codes": [item.code for item in bundle.reasons],
                },
            ]
        return result

    def guardrails_node(state: AgentState) -> dict:
        activity("VALIDATION", "Simulation des conséquences opérationnelles")
        sheet = FeatureSheet.model_validate(state["market_snapshot"])
        decision = DecisionBundle.model_validate(state["decision"])
        verdicts, approved = deps.guardrails.evaluate(
            state["cycle_id"],
            sheet,
            decision,
            deps.execution.positions(),
            deps.equity_usd,
        )
        deps.repository.add_event(
            "GUARDRAILS_EVALUATED",
            {"verdicts": [v.model_dump(mode="json") for v in verdicts]},
            cycle_id=state["cycle_id"],
        )
        return {
            "guardrail_verdicts": [v.model_dump(mode="json") for v in verdicts],
            "approved_orders": [o.model_dump(mode="json") for o in approved],
        }

    def pre_execution_gate(state: AgentState) -> dict:
        activity("EXECUTION", "Contrôle final avant envoi des ordres")
        current = deps.repository.current_kill_switch()
        deps.repository.add_event(
            "KILL_SWITCH_READ",
            {"door": "pre_execution", "state": current.value},
            cycle_id=state["cycle_id"],
        )
        return {"kill_switch_state": current.value}

    def route_after_gate(state: AgentState) -> Literal["execute", "reconcile"]:
        if state["kill_switch_state"] == "RUNNING" and state.get("approved_orders"):
            return "execute"
        return "reconcile"

    def execute_node(state: AgentState) -> dict:
        activity("EXECUTION", "Envoi et confirmation des ordres")
        from agent.domain import ApprovedOrder

        results = [
            deps.execution.execute(ApprovedOrder.model_validate(order))
            for order in state.get("approved_orders", [])
        ]
        return {"executions": [result.model_dump(mode="json") for result in results]}

    def reconcile(_: AgentState) -> dict:
        activity("RECONCILIATION", "Synchronisation des positions et protections")
        reconciled = deps.execution.reconcile()
        return {
            "reconciliations": [item.model_dump(mode="json") for item in reconciled],
            "positions": deps.execution.positions(),
        }

    def finalize(state: AgentState) -> dict:
        activity("FINALIZING", "Finalisation et persistance du cycle")
        if state.get("kill_switch_state") != KillSwitchState.RUNNING.value:
            status = CycleStatus.SKIPPED.value
        elif (
            state.get("research_status") == "DEGRADED"
            or state.get("decision_status") == "DEGRADED"
            or bool(state.get("incidents"))
        ):
            status = CycleStatus.DEGRADED.value
        else:
            status = CycleStatus.COMPLETED.value
        return {
            "status": status,
            "health_status": ("DEGRADED" if status == CycleStatus.DEGRADED.value else "NOMINAL"),
            "positions": deps.execution.positions(),
        }

    builder = StateGraph(AgentState)
    builder.add_node("check_killswitch", check_killswitch)
    builder.add_node("ingest", ingest)
    builder.add_node("research", research_node)
    builder.add_node("strategies", strategies_node)
    builder.add_node("decide", decide_node)
    builder.add_node("guardrails", guardrails_node)
    builder.add_node("pre_execution_gate", pre_execution_gate)
    builder.add_node("execute", execute_node)
    builder.add_node("reconcile", reconcile)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "check_killswitch")
    builder.add_conditional_edges("check_killswitch", route_after_killswitch)
    builder.add_edge("ingest", "research")
    builder.add_edge("research", "strategies")
    builder.add_edge("strategies", "decide")
    builder.add_edge("decide", "guardrails")
    builder.add_edge("guardrails", "pre_execution_gate")
    builder.add_conditional_edges("pre_execution_gate", route_after_gate)
    builder.add_edge("execute", "reconcile")
    builder.add_edge("reconcile", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=InMemorySaver())
