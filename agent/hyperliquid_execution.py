from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL
from hyperliquid.utils.types import Cloid
from pydantic import SecretStr

from agent.domain import ApprovedOrder, ExecutionResult, KillSwitchState
from agent.execution import build_intent_identity
from agent.protection import ProtectionSpec, build_protection_specs
from agent.repository import Repository


def _secret_value(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def _masked_address(address: str) -> str:
    return f"{address[:8]}...{address[-6:]}"


class HyperliquidReadiness:
    """Read-only validation of an account, signer, and usable collateral."""

    def __init__(
        self,
        private_key: SecretStr | str,
        account_address: str,
        *,
        network: str = "testnet",
        api_url: str | None = None,
        info: Info | Any | None = None,
        timeout_seconds: float = 10.0,
    ):
        self.wallet = Account.from_key(_secret_value(private_key))
        self.account_address = account_address
        self.network = network
        default_url = MAINNET_API_URL if network == "mainnet" else TESTNET_API_URL
        self.info = info or Info(
            api_url or default_url,
            skip_ws=True,
            timeout=timeout_seconds,
        )

    def inspect(self) -> dict[str, Any]:
        signer = self.wallet.address
        account = self.account_address
        agents = self.info.extra_agents(account) or []
        role = self.info.user_role(signer)
        state = self.info.user_state(account)
        abstraction = self._abstraction_state(account)
        spot_state = self._spot_state(account)

        signer_is_account = signer.lower() == account.lower()
        signer_is_agent = any(
            str(item.get("address", "")).lower() == signer.lower()
            for item in agents
            if isinstance(item, dict)
        )
        role_payload = str(role).lower()
        role_links_account = "agent" in role_payload and account.lower() in role_payload
        authorized = signer_is_account or signer_is_agent or role_links_account

        margin = state.get("marginSummary", {}) if isinstance(state, dict) else {}
        if abstraction in {"unifiedAccount", "portfolioMargin"}:
            account_value, withdrawable = self._unified_usdc(spot_state)
            collateral_source = "spotClearinghouseState"
        else:
            account_value = float(margin.get("accountValue") or 0)
            withdrawable = (
                float(state.get("withdrawable") or 0)
                if isinstance(state, dict)
                else 0.0
            )
            collateral_source = "clearinghouseState"
        blockers: list[str] = []
        if not authorized:
            blockers.append("SIGNER_NOT_AUTHORIZED")
        if signer_is_account:
            blockers.append("MASTER_KEY_FORBIDDEN_FOR_AUTOMATION")
        if withdrawable <= 0:
            blockers.append(f"{self.network.upper()}_COLLATERAL_UNAVAILABLE")

        role_name = role.get("role") if isinstance(role, dict) else None
        return {
            "network": self.network,
            "account": _masked_address(account),
            "signer": _masked_address(signer),
            "key_valid": True,
            "authorized": authorized,
            "dedicated_api_wallet": not signer_is_account,
            "role": role_name or "unknown",
            "extra_agent_count": len(agents) if isinstance(agents, list) else None,
            "account_value_usd": account_value,
            "withdrawable_usd": withdrawable,
            "available_collateral_usd": withdrawable,
            "account_abstraction": abstraction,
            "collateral_source": collateral_source,
            "ready_for_orders": not blockers,
            "blockers": blockers,
        }

    def _abstraction_state(self, account: str) -> str:
        query = getattr(self.info, "query_user_abstraction_state", None)
        if not callable(query):
            return "disabled"
        result = query(account)
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return str(result.get("abstraction") or result.get("type") or "disabled")
        return "disabled"

    def _spot_state(self, account: str) -> dict[str, Any]:
        query = getattr(self.info, "spot_user_state", None)
        if not callable(query):
            return {}
        result = query(account)
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _unified_usdc(spot_state: dict[str, Any]) -> tuple[float, float]:
        total = 0.0
        hold = 0.0
        for balance in spot_state.get("balances", []):
            if balance.get("coin") == "USDC" or int(balance.get("token", -1)) == 0:
                total = float(balance.get("total") or 0)
                hold = float(balance.get("hold") or 0)
                break
        available = max(0.0, total - hold)
        for item in spot_state.get("tokenToAvailableAfterMaintenance", []):
            if isinstance(item, list) and len(item) == 2 and int(item[0]) == 0:
                available = max(0.0, float(item[1] or 0))
                break
        return total, available


class HyperliquidExecutionService:
    """At-most-once execution using durable intents and deterministic CLOIDs."""

    def __init__(
        self,
        repository: Repository,
        private_key: SecretStr | str,
        account_address: str,
        *,
        max_open_notional_usd: float | None,
        slippage_bps: int,
        max_open_orders_per_cycle: int = 100,
        network: str = "testnet",
        api_url: str | None = None,
        is_cross: bool = False,
        timeout_seconds: float = 10.0,
        exchange: Exchange | Any | None = None,
        info: Info | Any | None = None,
    ):
        self.repository = repository
        self.wallet = Account.from_key(_secret_value(private_key))
        self.account_address = account_address
        self.max_open_notional_usd = max_open_notional_usd
        self.slippage_bps = slippage_bps
        self.max_open_orders_per_cycle = max_open_orders_per_cycle
        self.network = network
        self.is_cross = is_cross
        default_url = MAINNET_API_URL if network == "mainnet" else TESTNET_API_URL
        self.exchange = exchange or Exchange(
            self.wallet,
            api_url or default_url,
            account_address=account_address,
            timeout=timeout_seconds,
        )
        self.info = info or self.exchange.info

    def execute(self, order: ApprovedOrder) -> ExecutionResult:
        if self.repository.current_kill_switch() is not KillSwitchState.RUNNING:
            raise PermissionError("kill-switch changed before submission")
        if (
            order.action == "OPEN"
            and self.max_open_notional_usd is not None
            and order.notional_usd > self.max_open_notional_usd
        ):
            raise PermissionError(
                f"{self.network} open-order notional exceeds the configured cap"
            )

        intent_id, cloid = build_intent_identity(order.decision_key)
        intent, created = self.repository.get_or_create_intent(
            intent_id=intent_id,
            cloid=cloid,
            order=order,
            status="PENDING",
        )
        if not created:
            return ExecutionResult(
                intent_id=intent["intent_id"],
                cloid=intent["cloid"],
                symbol=order.symbol,
                status=intent["status"],
                duplicate_prevented=True,
            )
        if (
            order.action == "OPEN"
            and self.repository.count_open_intents(order.cycle_id)
            > self.max_open_orders_per_cycle
        ):
            self.repository.mark_intent(intent_id, "REJECTED")
            self.repository.add_event(
                "CYCLE_OPEN_ORDER_CAP_REACHED",
                {
                    "intent_id": intent_id,
                    "max_open_orders_per_cycle": self.max_open_orders_per_cycle,
                },
                cycle_id=order.cycle_id,
                severity="WARN",
            )
            return ExecutionResult(
                intent_id=intent_id,
                cloid=cloid,
                symbol=order.symbol,
                status="REJECTED",
            )

        size, limit_px, is_buy = self._wire_values(order)
        if size <= 0:
            self.repository.mark_intent(intent_id, "REJECTED")
            return ExecutionResult(
                intent_id=intent_id,
                cloid=cloid,
                symbol=order.symbol,
                status="REJECTED",
            )

        if order.action == "OPEN":
            try:
                leverage_response = self.exchange.update_leverage(
                    order.leverage,
                    order.symbol,
                    is_cross=self.is_cross,
                )
                if not isinstance(leverage_response, dict) or leverage_response.get("status") != "ok":
                    raise RuntimeError("leverage update rejected")
            except Exception as exc:
                # A leverage update is idempotent account configuration. If its ACK is
                # ambiguous, skip the order and let a future decision try again.
                self.repository.mark_intent(intent_id, "REJECTED")
                self.repository.add_event(
                    "LEVERAGE_UPDATE_FAILED",
                    {"intent_id": intent_id, "error": type(exc).__name__},
                    cycle_id=order.cycle_id,
                    severity="ERROR",
                )
                return ExecutionResult(
                    intent_id=intent_id,
                    cloid=cloid,
                    symbol=order.symbol,
                    status="REJECTED",
                )
            protection_specs = build_protection_specs(order, cloid)
            self.repository.ensure_protective_orders(
                intent_id,
                order.cycle_id,
                protection_specs,
                "PENDING",
            )
        else:
            protection_specs = []

        try:
            if order.action == "OPEN":
                status = self._submit_open_with_protection(
                    order,
                    cloid,
                    size,
                    limit_px,
                    is_buy,
                    protection_specs,
                )
            else:
                response = self.exchange.order(
                    order.symbol,
                    is_buy,
                    size,
                    limit_px,
                    {"limit": {"tif": "Ioc"}},
                    reduce_only=True,
                    cloid=Cloid.from_str(cloid),
                )
                status = self._submission_status(response)
                if status == "FILLED" and order.action == "CLOSE":
                    self._cancel_exchange_protections(order.symbol)
        except Exception as exc:
            # A transport failure may happen after the exchange accepted the order.
            # Never retry here: reconciliation by the same CLOID decides its fate.
            status = "UNKNOWN"
            self.repository.add_event(
                "EXECUTION_SUBMISSION_UNKNOWN",
                {"intent_id": intent_id, "error": type(exc).__name__},
                cycle_id=order.cycle_id,
                severity="ERROR",
            )
            for spec in protection_specs:
                self.repository.mark_protection(spec.protection_id, "UNKNOWN")
        self.repository.mark_intent(intent_id, status)
        return ExecutionResult(
            intent_id=intent_id,
            cloid=cloid,
            symbol=order.symbol,
            status=status,
        )

    def _submit_open_with_protection(
        self,
        order: ApprovedOrder,
        entry_cloid: str,
        size: float,
        limit_px: float,
        is_buy: bool,
        specs: list[ProtectionSpec],
    ) -> str:
        stop = next(spec for spec in specs if spec.kind == "SL")
        take_profits = sorted(
            (spec for spec in specs if spec.kind == "TP"),
            key=lambda spec: spec.level_index,
        )
        primary_tp = take_profits[0] if take_profits else None
        atomic_specs = ([primary_tp] if primary_tp is not None else []) + [stop]
        requests: list[dict[str, Any]] = [
            {
                "coin": order.symbol,
                "is_buy": is_buy,
                "sz": size,
                "limit_px": limit_px,
                "order_type": {"limit": {"tif": "Ioc"}},
                "reduce_only": False,
                "cloid": Cloid.from_str(entry_cloid),
            }
        ]
        for spec in atomic_specs:
            request = self._protection_request(order.symbol, not is_buy, size, spec)
            if request is not None:
                requests.append(request)
            else:
                self.repository.mark_protection(
                    spec.protection_id, "SKIPPED_TOO_SMALL"
                )

        response = self.exchange.bulk_orders(requests, grouping="normalTpsl")
        status = self._submission_status(response)
        if status not in {"FILLED", "OPEN"}:
            terminal = "REJECTED" if status == "REJECTED" else "UNKNOWN"
            for spec in specs:
                self.repository.mark_protection(spec.protection_id, terminal)
            return status

        for spec_index, spec in enumerate(atomic_specs, start=1):
            if self._protection_size(order.symbol, size, spec.size_fraction) > 0:
                child_status = self._child_submission_status(response, spec_index)
                if child_status == "REJECTED" and spec.kind == "SL":
                    retry_request = self._protection_request(
                        order.symbol, not is_buy, size, spec
                    )
                    if retry_request is not None:
                        retry_response = self.exchange.order(**retry_request)
                        child_status = self._submission_status(retry_response)
                self.repository.mark_protection(
                    spec.protection_id,
                    "REJECTED" if child_status == "REJECTED" else "ACTIVE",
                )

        remaining = take_profits[1:]
        if status == "FILLED" and remaining:
            filled_size = self._filled_size(response) or size
            primary_size = (
                self._protection_size(
                    order.symbol,
                    size,
                    primary_tp.size_fraction,
                )
                if primary_tp is not None
                else 0.0
            )
            remaining_capacity = max(0.0, filled_size - min(primary_size, filled_size))
            remaining_sizes = self._allocate_sizes(
                order.symbol,
                remaining_capacity,
                [spec.size_fraction for spec in remaining],
            )
            remaining_requests: list[dict[str, Any]] = []
            submitted_specs: list[ProtectionSpec] = []
            for spec, child_size in zip(remaining, remaining_sizes):
                request = self._protection_request(
                    order.symbol,
                    not is_buy,
                    size,
                    spec,
                    size_override=child_size,
                )
                if request is None:
                    self.repository.mark_protection(
                        spec.protection_id, "SKIPPED_TOO_SMALL"
                    )
                    continue
                remaining_requests.append(request)
                submitted_specs.append(spec)
            if remaining_requests:
                try:
                    tp_response = self.exchange.bulk_orders(
                        remaining_requests,
                        grouping="na",
                    )
                    for index, spec in enumerate(submitted_specs):
                        child_status = self._child_submission_status(
                            tp_response,
                            index,
                        )
                        self.repository.mark_protection(
                            spec.protection_id,
                            "ACTIVE" if child_status != "REJECTED" else "REJECTED",
                        )
                except Exception as exc:
                    for spec in submitted_specs:
                        self.repository.mark_protection(spec.protection_id, "UNKNOWN")
                    self.repository.add_event(
                        "TAKE_PROFIT_SUBMISSION_UNKNOWN",
                        {"intent_id": str(entry_cloid), "error": type(exc).__name__},
                        cycle_id=order.cycle_id,
                        severity="ERROR",
                    )
        return status

    def _protection_request(
        self,
        symbol: str,
        is_buy: bool,
        full_size: float,
        spec: ProtectionSpec,
        size_override: float | None = None,
    ) -> dict[str, Any] | None:
        size = (
            self._round_size(symbol, size_override)
            if size_override is not None
            else self._protection_size(symbol, full_size, spec.size_fraction)
        )
        if size <= 0:
            return None
        trigger_px = self._round_price(symbol, spec.trigger_px)
        return {
            "coin": symbol,
            "is_buy": is_buy,
            "sz": size,
            "limit_px": trigger_px,
            "order_type": {
                "trigger": {
                    "triggerPx": trigger_px,
                    "isMarket": True,
                    "tpsl": spec.kind.lower(),
                }
            },
            "reduce_only": True,
            "cloid": Cloid.from_str(spec.cloid),
        }

    def _protection_size(
        self, symbol: str, full_size: float, fraction: float
    ) -> float:
        coin = self.info.name_to_coin[symbol]
        asset = self.info.coin_to_asset[coin]
        decimals = self.info.asset_to_sz_decimals[asset]
        quantum = Decimal(1).scaleb(-decimals)
        value = (Decimal(str(full_size)) * Decimal(str(fraction))).quantize(
            quantum,
            rounding=ROUND_DOWN,
        )
        return float(value)

    def _round_size(self, symbol: str, raw_size: float) -> float:
        coin = self.info.name_to_coin[symbol]
        asset = self.info.coin_to_asset[coin]
        decimals = self.info.asset_to_sz_decimals[asset]
        quantum = Decimal(1).scaleb(-decimals)
        return float(
            Decimal(str(raw_size)).quantize(quantum, rounding=ROUND_DOWN)
        )

    def _allocate_sizes(
        self,
        symbol: str,
        total_size: float,
        weights: list[float],
    ) -> list[float]:
        if not weights:
            return []
        coin = self.info.name_to_coin[symbol]
        asset = self.info.coin_to_asset[coin]
        decimals = self.info.asset_to_sz_decimals[asset]
        quantum = Decimal(1).scaleb(-decimals)
        total_units = int(
            (
                Decimal(str(total_size)) + quantum / Decimal(1000)
            ).quantize(quantum, rounding=ROUND_DOWN)
            / quantum
        )
        decimal_weights = [Decimal(str(weight)) for weight in weights]
        weight_sum = sum(decimal_weights)
        units: list[int] = []
        allocated = 0
        for index, weight in enumerate(decimal_weights):
            if index == len(decimal_weights) - 1:
                current = max(0, total_units - allocated)
            else:
                current = int(Decimal(total_units) * weight / weight_sum)
                allocated += current
            units.append(current)
        return [float(Decimal(value) * quantum) for value in units]

    def _round_price(self, symbol: str, raw_price: float) -> float:
        coin = self.info.name_to_coin[symbol]
        asset = self.info.coin_to_asset[coin]
        size_decimals = self.info.asset_to_sz_decimals[asset]
        significant = float(f"{raw_price:.5g}")
        return round(significant, 6 - size_decimals)

    def _cancel_exchange_protections(self, symbol: str) -> None:
        active = self.repository.protective_orders(
            symbol=symbol,
            statuses={"PENDING", "ACTIVE", "UNKNOWN"},
        )
        if not active:
            return
        try:
            response = self.exchange.bulk_cancel_by_cloid(
                [
                    {"coin": symbol, "cloid": Cloid.from_str(item["cloid"])}
                    for item in active
                ]
            )
            if not isinstance(response, dict) or response.get("status") != "ok":
                raise RuntimeError("protective cancellation rejected")
            self.repository.cancel_protections(symbol)
        except Exception as exc:
            self.repository.add_event(
                "PROTECTION_CANCEL_FAILED",
                {"symbol": symbol, "error": type(exc).__name__},
                severity="ERROR",
            )

    def reconcile(self) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        for intent in self.repository.unresolved_intents():
            try:
                response = self.info.query_order_by_cloid(
                    self.account_address,
                    Cloid.from_str(intent["cloid"]),
                )
                status = self._reconciled_status(response)
                if status != "UNKNOWN":
                    self.repository.mark_intent(intent["intent_id"], status)
            except Exception as exc:
                status = "UNKNOWN"
                self.repository.add_event(
                    "EXECUTION_RECONCILIATION_FAILED",
                    {"intent_id": intent["intent_id"], "error": type(exc).__name__},
                    cycle_id=intent["cycle_id"],
                    severity="ERROR",
                )
            results.append(
                ExecutionResult(
                    intent_id=intent["intent_id"],
                    cloid=intent["cloid"],
                    symbol=intent["symbol"],
                    status=status,
                )
            )
        filled_cloids: set[str] = set()
        user_fills = getattr(self.info, "user_fills", None)
        if callable(user_fills):
            try:
                filled_cloids = {
                    str(item.get("cloid") or "").lower()
                    for item in user_fills(self.account_address)
                    if isinstance(item, dict) and item.get("cloid")
                }
            except Exception as exc:
                self.repository.add_event(
                    "FILL_RECONCILIATION_FAILED",
                    {"error": type(exc).__name__},
                    severity="WARN",
                )
        for protection in self.repository.protective_orders(
            statuses={"PENDING", "ACTIVE", "UNKNOWN"}
        ):
            if protection["cloid"].lower() in filled_cloids:
                self.repository.mark_protection(
                    protection["protection_id"], "TRIGGERED"
                )
                continue
            try:
                response = self.info.query_order_by_cloid(
                    self.account_address,
                    Cloid.from_str(protection["cloid"]),
                )
                exchange_status = self._reconciled_status(response)
                mapped = {
                    "OPEN": "ACTIVE",
                    "FILLED": "TRIGGERED",
                    "CANCELED": "CANCELED",
                    "REJECTED": "REJECTED",
                }.get(exchange_status)
                if mapped is not None:
                    self.repository.mark_protection(
                        protection["protection_id"], mapped
                    )
            except Exception as exc:
                self.repository.add_event(
                    "PROTECTION_RECONCILIATION_FAILED",
                    {
                        "protection_id": protection["protection_id"],
                        "error": type(exc).__name__,
                    },
                    cycle_id=protection["cycle_id"],
                    severity="ERROR",
                )
        return results

    def positions(self) -> list[dict]:
        state = self.info.user_state(self.account_address)
        positions: list[dict] = []
        for wrapper in state.get("assetPositions", []):
            position = wrapper.get("position", {})
            size = float(position.get("szi") or 0)
            if size == 0:
                continue
            symbol = position.get("coin")
            metadata = self.repository.latest_filled_open_intent(symbol)
            if metadata is None:
                raise RuntimeError(
                    f"position {symbol} has no durable strategy/invalidation metadata"
                )
            payload = metadata["payload"]
            leverage = int(payload.get("leverage", 1))
            notional = abs(float(position.get("positionValue") or 0))
            positions.append(
                {
                    "symbol": symbol,
                    "side": "LONG" if size > 0 else "SHORT",
                    "notional_usd": notional,
                    "leverage": leverage,
                    "margin_used_usd": notional / leverage,
                    "entry_px": float(position.get("entryPx") or 0),
                    "mark_px": (
                        notional / abs(size) if abs(size) > 0 else 0.0
                    ),
                    "unrealized_pnl_usd": float(
                        position.get("unrealizedPnl") or 0
                    ),
                    "roe_pct": float(position.get("returnOnEquity") or 0) * 100,
                    "liquidation_px": float(position.get("liquidationPx") or 0),
                    "invalidation_px": float(payload["invalidation_px"]),
                    "targets": list(payload.get("targets", [])),
                    "opened_at": metadata["created_at"],
                }
            )
        return positions

    def monitor(self, marks: dict[str, float]) -> list[dict]:
        del marks  # Trigger orders execute exchange-side; polling only reconciles state.
        before = {
            item["protection_id"]: item["status"]
            for item in self.repository.protective_orders()
        }
        self.reconcile()
        changes: list[dict] = []
        for item in self.repository.protective_orders():
            previous = before.get(item["protection_id"])
            if previous is not None and previous != item["status"]:
                changes.append(
                    {
                        "protection_id": item["protection_id"],
                        "symbol": item["symbol"],
                        "kind": item["kind"],
                        "from": previous,
                        "to": item["status"],
                    }
                )
        return changes

    def _wire_values(self, order: ApprovedOrder) -> tuple[float, float, bool]:
        coin = self.info.name_to_coin[order.symbol]
        asset = self.info.coin_to_asset[coin]
        size_decimals = self.info.asset_to_sz_decimals[asset]
        quantum = Decimal(1).scaleb(-size_decimals)
        size = (Decimal(str(order.notional_usd)) / Decimal(str(order.mark_px))).quantize(
            quantum,
            rounding=ROUND_DOWN,
        )
        is_buy = (
            order.direction == "LONG"
            if order.action == "OPEN"
            else order.direction == "SHORT"
        )
        slippage = Decimal(self.slippage_bps) / Decimal(10_000)
        multiplier = Decimal(1) + slippage if is_buy else Decimal(1) - slippage
        raw_price = float(Decimal(str(order.mark_px)) * multiplier)
        significant = float(f"{raw_price:.5g}")
        limit_px = round(significant, 6 - size_decimals)
        return float(size), limit_px, is_buy

    @staticmethod
    def _submission_status(response: Any) -> str:
        if not isinstance(response, dict) or response.get("status") != "ok":
            return "REJECTED"
        statuses = response.get("response", {}).get("data", {}).get("statuses", [])
        if not statuses or not isinstance(statuses[0], dict):
            return "UNKNOWN"
        item = statuses[0]
        if "filled" in item:
            return "FILLED"
        if "resting" in item:
            return "OPEN"
        if "error" in item:
            return "REJECTED"
        return "UNKNOWN"

    @staticmethod
    def _filled_size(response: Any) -> float | None:
        if not isinstance(response, dict):
            return None
        statuses = response.get("response", {}).get("data", {}).get("statuses", [])
        if not statuses or not isinstance(statuses[0], dict):
            return None
        filled = statuses[0].get("filled")
        if not isinstance(filled, dict):
            return None
        value = filled.get("totalSz") or filled.get("sz")
        return float(value) if value is not None else None

    @staticmethod
    def _child_submission_status(response: Any, index: int) -> str:
        if not isinstance(response, dict) or response.get("status") != "ok":
            return "REJECTED"
        statuses = response.get("response", {}).get("data", {}).get("statuses", [])
        if index >= len(statuses):
            # Some SDK mocks and older API responses omit trigger child statuses.
            return "UNKNOWN"
        item = statuses[index]
        if not isinstance(item, dict) or "error" in item:
            return "REJECTED"
        return "ACTIVE"

    @staticmethod
    def _reconciled_status(response: Any) -> str:
        if not isinstance(response, dict) or response.get("status") != "order":
            return "UNKNOWN"
        order_status = str(response.get("orderStatus", "")).lower()
        if order_status == "filled":
            return "FILLED"
        if order_status == "open":
            return "OPEN"
        if order_status in {"canceled", "cancelled"}:
            return "CANCELED"
        if order_status.endswith("canceled") or order_status in {"rejected", "expired"}:
            return "REJECTED"
        return "UNKNOWN"


# Backward-compatible imports for existing integrations and tests.
HyperliquidTestnetReadiness = HyperliquidReadiness
HyperliquidTestnetExecutionService = HyperliquidExecutionService
