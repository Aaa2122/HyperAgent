from __future__ import annotations

import uuid
import threading
import time
from datetime import datetime, timezone

from llm_checks import LLMLayerConfig

from agent.config import AgentMode, Settings
from agent.db import build_engine, build_session_factory
from agent.decision import GrokDecisionProvider, RuleBasedDecisionProvider
from agent.execution import PaperExecutionService
from agent.graph import GraphDependencies, build_graph
from agent.guardrails import GuardrailEngine
from agent.hyperliquid import HyperliquidInfoClient, HyperliquidMarketData
from agent.hyperliquid_execution import (
    HyperliquidExecutionService,
    HyperliquidReadiness,
)
from agent.market import PaperMarketData
from agent.repository import Repository
from agent.research import GrokXResearchProvider, NeutralResearchProvider
from agent.strategies import MeanReversionStrategy, MomentumStrategy


class AgentService:
    def __init__(self, settings: Settings):
        self.settings = settings
        engine = build_engine(settings.database_url)
        self.repository = Repository(engine, build_session_factory(engine))
        self.repository.initialize()
        self._cycle_lock = threading.Lock()
        self._risk_snapshot: dict = {"status": "INITIALIZING", "positions": []}
        self._readiness_cache: tuple[float, dict] | None = None
        self._analytics_cache: tuple[float, dict] | None = None
        self._last_llm_cycle_at = 0.0
        self._last_llm_marks: dict[str, float] = {}
        self._material_event_pending = False

        self.market = (
            HyperliquidMarketData(
                client=HyperliquidInfoClient(
                    settings.hyperliquid_api_url,
                    timeout_seconds=settings.hyperliquid_timeout_seconds,
                ),
                network=settings.hyperliquid_network,
                account_address=settings.hyperliquid_account_address,
            )
            if settings.market_data_provider == "hyperliquid"
            else PaperMarketData()
        )

        guardrail_config = LLMLayerConfig(max_leverage=settings.max_model_leverage)
        if settings.trading_profile == "experimental":
            experimental_cap = settings.experimental_max_asset_notional_usd
            guardrail_config = guardrail_config.model_copy(
                update={
                    "operational_only": True,
                    "min_stop_atr": 0.15,
                    "max_stop_atr": 6.0,
                    "entry_zone_tolerance_pct": 0.02,
                    "min_plan_conviction": settings.experimental_min_plan_conviction,
                    "min_open_confidence": settings.experimental_min_open_confidence,
                    "max_opens_per_day_per_symbol": 12,
                    "stop_out_cooldown_minutes": 15.0,
                    "max_portfolio_risk_frac": (
                        settings.experimental_max_portfolio_risk_frac
                    ),
                    "max_net_exposure_frac": (
                        settings.experimental_max_net_exposure_frac
                    ),
                    "min_order_notional_usd": 10.0,
                    "market_data_max_age_seconds": 60.0,
                    "max_asset_notional_usd": {
                        "BTC": experimental_cap,
                        "ETH": experimental_cap,
                        "SOL": experimental_cap,
                        "XRP": experimental_cap, "BNB": experimental_cap,
                        "HYPE": experimental_cap, "LINK": experimental_cap,
                        "SUI": experimental_cap,
                    },
                }
            )
        if settings.agent_mode is AgentMode.LIVE:
            # LIVE final profile: the model owns allocation and frequency. Only
            # structural/venue invariants remain (valid stops, fresh data, min lot).
            guardrail_config = guardrail_config.model_copy(
                update={
                    "operational_only": True,
                    "min_order_notional_usd": 50.0,
                    "min_plan_conviction": 0.0,
                    "min_open_confidence": 0.0,
                    "max_opens_per_day_per_symbol": 100_000,
                    "stop_out_cooldown_minutes": 0.0,
                    "max_portfolio_risk_frac": 1.0,
                    "max_net_exposure_frac": 1_000_000.0,
                    "max_asset_notional_usd": {
                        "BTC": 1_000_000_000_000.0,
                        "ETH": 1_000_000_000_000.0,
                        "SOL": 1_000_000_000_000.0,
                        "XRP": 1_000_000_000_000.0,
                        "BNB": 1_000_000_000_000.0,
                        "HYPE": 1_000_000_000_000.0,
                        "LINK": 1_000_000_000_000.0,
                        "SUI": 1_000_000_000_000.0,
                    },
                }
            )

        research = (
            GrokXResearchProvider(
                settings.xai_api_key or "",
                settings.xai_model,
                settings.allowed_x_handles,
                cache_seconds=settings.x_research_cache_seconds,
                recorder=self.repository.record_llm_call,
            )
            if settings.x_search_enabled
            else NeutralResearchProvider()
        )
        decisions = (
            GrokDecisionProvider(
                settings.xai_api_key or "",
                settings.xai_model,
                config=guardrail_config,
                trading_profile=settings.trading_profile,
                strategist_refresh_seconds=settings.strategist_refresh_seconds,
                live_autonomous=settings.agent_mode is AgentMode.LIVE,
                recorder=self.repository.record_llm_call,
            )
            if settings.llm_provider.lower() == "grok"
            else RuleBasedDecisionProvider()
        )
        if isinstance(decisions, GrokDecisionProvider):
            previous_cycles = self.repository.dashboard(limit=1)["cycles"]
            if previous_cycles:
                previous_playbook = (
                    previous_cycles[0].get("state", {})
                    .get("decision", {})
                    .get("playbook")
                )
                decisions.seed_playbook(previous_playbook)
        if isinstance(research, GrokXResearchProvider):
            previous = self.repository.latest_completed_cycle()
            if previous:
                research.seed_cache(previous.get("state", {}).get("research"))
        equity_usd = settings.paper_equity_usd
        if settings.agent_mode in {AgentMode.TESTNET, AgentMode.LIVE}:
            readiness = self.hyperliquid_readiness(fresh=True)
            if not readiness["ready_for_orders"]:
                blockers = ", ".join(readiness["blockers"])
                raise RuntimeError(
                    f"Hyperliquid {settings.hyperliquid_execution_network} is not ready: "
                    f"{blockers}"
                )
            equity_usd = readiness["available_collateral_usd"]
            if settings.agent_mode is AgentMode.LIVE:
                cap = None
                slippage_bps = settings.mainnet_slippage_bps
                max_open_orders_per_cycle = 100
            else:
                cap = settings.testnet_max_order_notional_usd
                slippage_bps = settings.testnet_slippage_bps
                max_open_orders_per_cycle = 100
                guardrail_config = guardrail_config.model_copy(
                    update={
                        "max_asset_notional_usd": {
                            "BTC": cap,
                            "ETH": cap,
                            "SOL": cap,
                            "XRP": cap, "BNB": cap, "HYPE": cap,
                            "LINK": cap, "SUI": cap,
                        }
                    }
                )
            execution = HyperliquidExecutionService(
                self.repository,
                settings.hyperliquid_private_key,
                settings.hyperliquid_account_address,
                max_open_notional_usd=cap,
                slippage_bps=slippage_bps,
                max_open_orders_per_cycle=max_open_orders_per_cycle,
                network=settings.hyperliquid_execution_network,
                api_url=settings.hyperliquid_execution_api_url,
                is_cross=settings.hyperliquid_margin_mode == "cross",
                timeout_seconds=settings.hyperliquid_timeout_seconds,
            )
        else:
            execution = PaperExecutionService(self.repository, settings.agent_mode)
        self.execution = execution
        self.graph_dependencies = GraphDependencies(
                mode=settings.agent_mode,
                equity_usd=equity_usd,
                repository=self.repository,
                market=self.market,
                strategies=[MomentumStrategy(), MeanReversionStrategy()],
                research=research,
                decisions=decisions,
                guardrails=GuardrailEngine(guardrail_config),
                execution=execution,
        )
        self.graph = build_graph(self.graph_dependencies)

    def run_cycle(
        self, *, allow_external_research: bool = True,
        allow_strategist_refresh: bool = True,
    ) -> dict:
        if not self._cycle_lock.acquire(blocking=False):
            raise RuntimeError("a cycle is already running")
        try:
            return self._run_cycle(
                allow_external_research=allow_external_research,
                allow_strategist_refresh=allow_strategist_refresh,
            )
        finally:
            self._cycle_lock.release()

    def _run_cycle(self, *, allow_external_research: bool,
                   allow_strategist_refresh: bool) -> dict:
        if self.settings.agent_mode in {AgentMode.TESTNET, AgentMode.LIVE}:
            readiness = self.hyperliquid_readiness(fresh=True)
            if not readiness["ready_for_orders"]:
                raise RuntimeError(
                    "fresh Hyperliquid readiness failed before cycle: "
                    + ", ".join(readiness["blockers"])
                )
            self.graph_dependencies.equity_usd = readiness[
                "available_collateral_usd"
            ]
        unresolved = self.repository.unresolved_intents()
        if unresolved:
            self.execution.reconcile()
            if self.repository.unresolved_intents():
                raise RuntimeError("startup reconciliation required before a new cycle")
        cycle_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        self.repository.create_cycle(cycle_id, self.settings.agent_mode.value, started_at)
        initial = {
            "cycle_id": cycle_id,
            "mode": self.settings.agent_mode.value,
            "status": "RUNNING",
            "incidents": [],
            "allow_external_research": allow_external_research,
            "allow_strategist_refresh": allow_strategist_refresh,
        }
        try:
            result = self.graph.invoke(
                initial,
                config={"configurable": {"thread_id": cycle_id}},
            )
            serializable = dict(result)
            self.repository.complete_cycle(
                cycle_id, serializable.get("status", "COMPLETED"), serializable
            )
            return serializable
        except Exception as exc:
            self.repository.complete_cycle(
                cycle_id,
                "FAILED",
                initial,
                error=f"{type(exc).__name__}: {exc}",
            )
            self.repository.add_event(
                "CYCLE_FAILED",
                {"error": type(exc).__name__},
                cycle_id=cycle_id,
                severity="ERROR",
            )
            raise

    def run_scheduled_cycle(self) -> dict:
        policy = self.scheduled_cycle_policy()
        if policy["run"]:
            event_policy = self._event_cycle_policy()
            policy.update(event_policy)
            policy["run"] = event_policy["run"]
        if not policy["run"]:
            self.repository.record_llm_call({
                "stage": "cycle_policy", "provider": "system", "model": "deterministic",
                "status": "SKIPPED", "skipped_reason": policy["reason"],
                "response": policy,
            })
            self.repository.add_event("LLM_CYCLE_SKIPPED", policy)
            return {"status": "SKIPPED", "policy": policy}
        result = self.run_cycle(
            allow_external_research=policy["external_research"],
            allow_strategist_refresh=policy["strategist_refresh"],
        )
        self._last_llm_cycle_at = time.monotonic()
        self._last_llm_marks = self.market.marks()
        self._material_event_pending = False
        return result

    def _event_cycle_policy(self) -> dict:
        marks = self.market.marks()
        elapsed = time.monotonic() - self._last_llm_cycle_at
        moves = {
            symbol: abs(price / self._last_llm_marks[symbol] - 1) * 100
            for symbol, price in marks.items()
            if self._last_llm_marks.get(symbol)
        }
        max_move = max(moves.values(), default=0.0)
        due = not self._last_llm_marks or elapsed >= self.settings.trader_max_interval_seconds
        triggered = max_move >= self.settings.trader_move_trigger_pct
        run = due or triggered or self._material_event_pending
        reason = "MAX_INTERVAL" if due else "MARKET_MOVE" if triggered else "MATERIAL_EVENT" if self._material_event_pending else "NO_MATERIAL_CHANGE"
        return {"run": run, "event_reason": reason, "max_move_pct": max_move,
                "max_interval_seconds": self.settings.trader_max_interval_seconds}

    def scheduled_cycle_policy(self) -> dict:
        if self.settings.agent_mode not in {AgentMode.TESTNET, AgentMode.LIVE}:
            return {"run": True, "external_research": True,
                    "strategist_refresh": True, "reason": "NON_LIVE_MODE"}
        readiness = self.hyperliquid_readiness()
        available = float(readiness.get("available_collateral_usd") or 0)
        positions = self.execution.positions()
        if available >= self.settings.min_llm_collateral_usd:
            return {"run": True, "external_research": True,
                    "strategist_refresh": True, "reason": "CAPITAL_AVAILABLE",
                    "available_collateral_usd": available,
                    "threshold_usd": self.settings.min_llm_collateral_usd}
        return {"run": False, "external_research": False,
                "strategist_refresh": False,
                "reason": ("INSUFFICIENT_DEPLOYABLE_CAPITAL_PROTECTIONS_ACTIVE"
                           if positions else "INSUFFICIENT_CAPITAL_NO_POSITION"),
                "available_collateral_usd": available,
                "threshold_usd": self.settings.min_llm_collateral_usd,
                "risk_monitor_continues": True}

    def monitor_risk(self) -> list[dict]:
        marks = self.market.marks()
        changes = self.execution.monitor(marks)
        self._risk_snapshot = self.position_analytics(include_charts=False)
        self._risk_snapshot["status"] = "OK"
        self._risk_snapshot["as_of"] = datetime.now(timezone.utc).isoformat()
        if changes:
            self._material_event_pending = True
            self.repository.add_event(
                "RISK_MONITOR_CHANGES",
                {"changes": changes},
                severity="WARN",
            )
        return changes

    def position_analytics(self, *, include_charts: bool = True) -> dict:
        now_mono = time.monotonic()
        if include_charts and self._analytics_cache and now_mono - self._analytics_cache[0] < 30:
            return self._analytics_cache[1]
        positions = self.execution.positions()
        protections = self.repository.protective_orders()
        now = datetime.now(timezone.utc)
        funding_by_symbol: dict[str, float] = {}
        fills_by_symbol: dict[str, list[dict]] = {}
        if positions and isinstance(self.market, HyperliquidMarketData) and self.market.account_address:
            earliest = min(datetime.fromisoformat(item["opened_at"]) for item in positions)
            if earliest.tzinfo is None:
                earliest = earliest.replace(tzinfo=timezone.utc)
            try:
                records = self.market.client.user_funding(
                    self.market.account_address,
                    int(earliest.timestamp() * 1000),
                    int(now.timestamp() * 1000),
                )
                for record in records:
                    delta = record.get("delta", {})
                    symbol = str(delta.get("coin") or "")
                    funding_by_symbol[symbol] = funding_by_symbol.get(symbol, 0.0) + float(delta.get("usdc") or 0)
            except RuntimeError:
                pass
            try:
                for fill in self.market.client.user_fills(self.market.account_address):
                    symbol = str(fill.get("coin") or "")
                    fills_by_symbol.setdefault(symbol, []).append(fill)
            except RuntimeError:
                pass
        result = []
        for position in positions:
            symbol = position["symbol"]
            mark = float(position.get("mark_px") or 0)
            entry = float(position["entry_px"])
            side = position["side"]
            stop = float(position["invalidation_px"])
            targets = sorted(
                [float(item["trigger_px"]) for item in protections
                 if item["symbol"] == symbol and item["kind"] == "TP"],
                reverse=side == "SHORT",
            ) or [float(value) for value in position.get("targets", [])]
            direction = 1 if side == "LONG" else -1
            initial_risk = max(direction * (entry - stop), 1e-9)
            opened = datetime.fromisoformat(position["opened_at"])
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            age = (now - opened).total_seconds()
            interval = "5m" if age <= 86_400 else "1h" if age <= 604_800 else "4h"
            chart = []
            if include_charts and isinstance(self.market, HyperliquidMarketData):
                try:
                    candles = self.market.client.candles(
                        symbol, interval, int(opened.timestamp() * 1000), int(now.timestamp() * 1000)
                    )
                    chart = [{
                        "time": int(item.get("t", 0)),
                        "price": float(item.get("c", 0)),
                        "open": float(item.get("o", item.get("c", 0))),
                        "high": float(item.get("h", item.get("c", 0))),
                        "low": float(item.get("l", item.get("c", 0))),
                        "close": float(item.get("c", 0)),
                        "volume": float(item.get("v", 0)),
                    } for item in candles]
                except RuntimeError:
                    if self._analytics_cache:
                        previous = next(
                            (item for item in self._analytics_cache[1]["positions"]
                             if item["symbol"] == symbol), None
                        )
                        chart = previous.get("chart", []) if previous else []
            symbol_fills = [
                fill for fill in fills_by_symbol.get(symbol, [])
                if int(fill.get("time") or 0) >= int(opened.timestamp() * 1000)
            ]
            fills_by_cloid: dict[str, list[dict]] = {}
            for fill in symbol_fills:
                fills_by_cloid.setdefault(str(fill.get("cloid") or "").lower(), []).append(fill)
            symbol_protections = [item for item in protections if item["symbol"] == symbol]
            tp_protections = sorted(
                [item for item in symbol_protections if item["kind"] == "TP"],
                key=lambda item: item["level_index"],
            )
            target_stats = []
            for index, target in enumerate(targets):
                protection = next(
                    (item for item in tp_protections if float(item["trigger_px"]) == target),
                    None,
                )
                target_fills = fills_by_cloid.get(
                    str(protection.get("cloid") or "").lower(), []
                ) if protection else []
                filled_size = sum(float(item.get("sz") or 0) for item in target_fills)
                filled_notional = sum(
                    float(item.get("sz") or 0) * float(item.get("px") or 0)
                    for item in target_fills
                )
                average_fill = (
                    filled_notional / filled_size if filled_size else None
                )
                target_stats.append({
                    "level": int(protection["level_index"]) if protection else index + 1,
                    "price": target,
                    "distance_pct": direction * (target / mark - 1) * 100 if mark else 0,
                    "progress_pct": 100.0 if target_fills else max(0.0, min(100.0, direction * (mark - entry) /
                                                 max(direction * (target - entry), 1e-9) * 100)),
                    "reward_r": direction * (target - entry) / initial_risk,
                    "status": "ACHIEVED" if target_fills else str(protection.get("status") if protection else "PLANNED"),
                    "hit_at": datetime.fromtimestamp(min(int(item["time"]) for item in target_fills) / 1000, timezone.utc).isoformat() if target_fills else None,
                    "average_fill_px": average_fill,
                    "filled_size": filled_size,
                    "filled_notional_usd": filled_notional,
                    "realized_pnl_usd": sum(float(item.get("closedPnl") or 0) for item in target_fills),
                    "fees_usd": sum(float(item.get("fee") or 0) for item in target_fills),
                })
            realized_pnl = sum(float(item.get("closedPnl") or 0) for item in symbol_fills)
            trade_fees = sum(float(item.get("fee") or 0) for item in symbol_fills)
            opening_fills = [item for item in symbol_fills if str(item.get("dir") or "").startswith("Open")]
            initial_size = sum(float(item.get("sz") or 0) for item in opening_fills)
            closed_size = sum(float(item.get("sz") or 0) for item in symbol_fills if str(item.get("dir") or "").startswith("Close"))
            realized_net = realized_pnl - trade_fees
            funding_net = funding_by_symbol.get(symbol, 0.0)
            unrealized = float(position.get("unrealized_pnl_usd") or 0)
            liquidation = float(position.get("liquidation_px") or 0)
            result.append({
                **position,
                "interval": interval,
                "chart": chart,
                "distance_to_stop_pct": direction * (mark / stop - 1) * 100 if stop else 0,
                "distance_to_liquidation_pct": abs(mark / liquidation - 1) * 100 if liquidation else None,
                "unrealized_r": direction * (mark - entry) / initial_risk,
                "targets_analytics": target_stats,
                "initial_size": initial_size,
                "closed_size": closed_size,
                "closed_fraction_pct": min(100.0, closed_size / initial_size * 100) if initial_size else 0.0,
                "realized_pnl_usd": realized_pnl,
                "trade_fees_usd": trade_fees,
                "realized_net_pnl_usd": realized_net,
                "funding_net_usd": funding_net,
                "funding_paid_usd": max(0.0, -funding_net),
                "pnl_after_funding_usd": unrealized + funding_net,
                "total_trade_net_pnl_usd": realized_pnl + unrealized + funding_net - trade_fees,
            })
        payload = {"positions": result, "as_of": now.isoformat(),
                   "funding_net_usd": sum(funding_by_symbol.values()),
                   "open_pnl_after_funding_usd": sum(
                       float(item.get("unrealized_pnl_usd") or 0) for item in positions
                   ) + sum(funding_by_symbol.values()),
                   "prompt_used": False,
                   "strategy": "Deterministic exchange-side SL/TP reconciliation"}
        if include_charts:
            self._analytics_cache = (now_mono, payload)
        return payload

    def dashboard(self) -> dict:
        data = self.repository.dashboard()
        data["mode"] = self.settings.agent_mode.value
        data["decision_provider"] = self.settings.llm_provider.lower()
        data["xai_model"] = self.settings.xai_model
        data["trading_profile"] = self.settings.trading_profile
        data["max_model_leverage"] = self.settings.max_model_leverage
        data["automation_enabled"] = self.settings.automation_enabled
        data["cycle_interval_seconds"] = self.settings.cycle_interval_seconds
        data["risk_monitor_interval_seconds"] = (
            self.settings.risk_monitor_interval_seconds
        )
        data["x_search_enabled"] = self.settings.x_search_enabled
        data["paper_equity_usd"] = self.settings.paper_equity_usd
        try:
            data["cost_policy"] = self.scheduled_cycle_policy()
        except Exception as exc:
            data["cost_policy"] = {
                "run": False,
                "external_research": False,
                "strategist_refresh": False,
                "reason": "READINESS_TEMPORARILY_UNAVAILABLE",
                "error": type(exc).__name__,
            }
        data["risk_monitor"] = self._risk_snapshot
        data["market_provider"] = self.market.name
        data["market_quality_warnings"] = self.market.quality_warnings
        data["universe_scan"] = getattr(self.market, "last_universe_scan", [])
        data["hyperliquid_network"] = self.settings.hyperliquid_network
        data["hyperliquid_execution_network"] = (
            self.settings.hyperliquid_execution_network
        )
        data["hyperliquid_account_configured"] = bool(
            self.settings.hyperliquid_account_address
        )
        if isinstance(self.market, HyperliquidMarketData):
            try:
                data["hyperliquid_account"] = self.market.account_snapshot()
            except Exception as exc:
                data["hyperliquid_account"] = None
                data["hyperliquid_account_error"] = type(exc).__name__
        if self.settings.agent_mode in {AgentMode.TESTNET, AgentMode.LIVE}:
            data["positions"] = self.execution.positions()
        return data

    def performance(self) -> dict:
        if not isinstance(self.market, HyperliquidMarketData):
            return {"ranges": {}, "as_of": None}
        return self.market.performance_snapshot() or {"ranges": {}, "as_of": None}

    def hyperliquid_readiness(self, *, fresh: bool = False) -> dict:
        now = time.monotonic()
        if not fresh and self._readiness_cache and now - self._readiness_cache[0] < 30:
            return self._readiness_cache[1]
        if not self.settings.hyperliquid_private_key:
            return {
                "network": self.settings.hyperliquid_execution_network,
                "configured": False,
                "ready_for_orders": False,
                "blockers": ["PRIVATE_KEY_NOT_CONFIGURED"],
            }
        if not self.settings.hyperliquid_account_address:
            return {
                "network": self.settings.hyperliquid_execution_network,
                "configured": False,
                "ready_for_orders": False,
                "blockers": ["ACCOUNT_ADDRESS_NOT_CONFIGURED"],
            }
        result = HyperliquidReadiness(
            self.settings.hyperliquid_private_key,
            self.settings.hyperliquid_account_address,
            network=self.settings.hyperliquid_execution_network,
            api_url=self.settings.hyperliquid_execution_api_url,
            timeout_seconds=self.settings.hyperliquid_timeout_seconds,
        ).inspect()
        result["configured"] = True
        self._readiness_cache = (now, result)
        return result
