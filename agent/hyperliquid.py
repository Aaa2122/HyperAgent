from __future__ import annotations

import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import httpx

from llm_schemas import FeatureSheet


SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "BNB", "HYPE", "LINK", "SUI")


class HyperliquidInfoClient:
    """Read-only client for POST /info. It intentionally exposes no exchange route."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def post(self, payload: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.post(f"{self.base_url}/info", json=payload)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"Hyperliquid transient status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(0.2 * (2**attempt))
        raise RuntimeError("Hyperliquid Info API unavailable") from last_error

    def meta_and_asset_contexts(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        payload = self.post({"type": "metaAndAssetCtxs"})
        if not isinstance(payload, list) or len(payload) != 2:
            raise ValueError("invalid metaAndAssetCtxs response")
        return payload[0], payload[1]

    def candles(
        self, coin: str, interval: str, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        result = self.post(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            }
        )
        if not isinstance(result, list):
            raise ValueError(f"invalid candles response for {coin}/{interval}")
        return result

    def l2_book(self, coin: str) -> dict[str, Any]:
        result = self.post({"type": "l2Book", "coin": coin})
        if not isinstance(result, dict):
            raise ValueError(f"invalid l2Book response for {coin}")
        return result

    def funding_history(self, coin: str, start_ms: int, end_ms: int) -> list[dict]:
        result = self.post(
            {
                "type": "fundingHistory",
                "coin": coin,
                "startTime": start_ms,
                "endTime": end_ms,
            }
        )
        return result if isinstance(result, list) else []

    def user_funding(self, user: str, start_ms: int, end_ms: int) -> list[dict]:
        result = self.post(
            {
                "type": "userFunding",
                "user": user,
                "startTime": start_ms,
                "endTime": end_ms,
            }
        )
        return result if isinstance(result, list) else []

    def user_fills(self, user: str) -> list[dict]:
        """Recent account fills, including TP/SL cloids and realized P&L."""
        result = self.post(
            {"type": "userFills", "user": user, "aggregateByTime": True}
        )
        return result if isinstance(result, list) else []

    def clearinghouse_state(self, user: str) -> dict[str, Any]:
        result = self.post({"type": "clearinghouseState", "user": user})
        if not isinstance(result, dict):
            raise ValueError("invalid clearinghouseState response")
        return result

    def spot_clearinghouse_state(self, user: str) -> dict[str, Any]:
        result = self.post({"type": "spotClearinghouseState", "user": user})
        if not isinstance(result, dict):
            raise ValueError("invalid spotClearinghouseState response")
        return result

    def user_abstraction_state(self, user: str) -> str:
        result = self.post({"type": "userAbstraction", "user": user})
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return str(result.get("abstraction") or result.get("type") or "disabled")
        return "disabled"

    def all_mids(self) -> dict[str, float]:
        result = self.post({"type": "allMids"})
        if not isinstance(result, dict):
            raise ValueError("invalid allMids response")
        return {
            symbol: float(value)
            for symbol, value in result.items()
            if symbol in SYMBOLS
        }

    def portfolio(self, user: str) -> list[Any]:
        result = self.post({"type": "portfolio", "user": user})
        if not isinstance(result, list):
            raise ValueError("invalid portfolio response")
        return result


class HyperliquidMarketData:
    def __init__(
        self,
        client: HyperliquidInfoClient,
        network: str,
        account_address: str | None = None,
    ) -> None:
        self.client = client
        self.network = network
        self.name = f"hyperliquid-{network}"
        self.quality_warnings = [
            "OI_CHANGE_24H_UNAVAILABLE",
            "AGGREGATE_LIQUIDATIONS_UNAVAILABLE",
        ]
        self.last_universe_scan: list[dict[str, Any]] = []
        self.account_address = account_address
        self._account_cache: tuple[float, dict[str, Any]] | None = None
        self._performance_cache: tuple[float, dict[str, Any]] | None = None

    def snapshot(self) -> FeatureSheet:
        requested_at = datetime.now(timezone.utc)
        now_ms = int(requested_at.timestamp() * 1000)
        meta, contexts = self.client.meta_and_asset_contexts()
        universe = meta.get("universe", [])
        context_by_symbol = {
            item.get("name"): {
                **contexts[index],
                "maxLeverage": item.get("maxLeverage", 20),
            }
            for index, item in enumerate(universe)
            if index < len(contexts) and item.get("name") in SYMBOLS
        }
        symbols = tuple(symbol for symbol in SYMBOLS if symbol in context_by_symbol)
        if len(symbols) < 3:
            raise ValueError(f"Hyperliquid universe only returned {symbols}")

        hour = 60 * 60 * 1000
        day = 24 * hour
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures: dict[tuple[str, str], Any] = {}
            for symbol in symbols:
                futures[(symbol, "1h")] = pool.submit(
                    self.client.candles, symbol, "1h", now_ms - 12 * hour, now_ms
                )
                futures[(symbol, "4h")] = pool.submit(
                    self.client.candles, symbol, "4h", now_ms - 55 * day, now_ms
                )
                futures[(symbol, "1d")] = pool.submit(
                    self.client.candles, symbol, "1d", now_ms - 240 * day, now_ms
                )
                futures[(symbol, "book")] = pool.submit(self.client.l2_book, symbol)
                futures[(symbol, "funding")] = pool.submit(
                    self.client.funding_history, symbol, now_ms - day, now_ms
                )
            fetched = {key: future.result() for key, future in futures.items()}

        computed = {
            symbol: self._asset_features(
                symbol,
                context_by_symbol[symbol],
                now_ms,
                candles_1h=fetched[(symbol, "1h")],
                candles_4h=fetched[(symbol, "4h")],
                candles_1d=fetched[(symbol, "1d")],
                book=fetched[(symbol, "book")],
                funding=fetched[(symbol, "funding")],
            )
            for symbol in symbols
        }

        ranked = []
        for symbol, features in computed.items():
            score = (
                abs(float(features["ret_4h_pct"]))
                + min(float(features["adx_4h"]), 60.0) / 60.0
                + min(float(features["oi_usd"]) / 500_000_000.0, 1.0)
                - min(float(features["spread_bps"]) / 5.0, 1.0)
            )
            ranked.append({
                "symbol": symbol, "score": score,
                "spread_bps": float(features["spread_bps"]),
                "ret_4h_pct": float(features["ret_4h_pct"]),
                "oi_usd": float(features["oi_usd"]),
            })
        ranked.sort(key=lambda item: item["score"], reverse=True)
        selected = {"BTC", "ETH", "SOL"}
        selected.update(item["symbol"] for item in ranked if item["symbol"] not in selected and len(selected) < 5)
        self.last_universe_scan = [
            {**item, "selected": item["symbol"] in selected,
             "reason": "CORE" if item["symbol"] in {"BTC", "ETH", "SOL"} else "TOP_SCORE" if item["symbol"] in selected else "BELOW_CUTOFF"}
            for item in ranked
        ]

        daily_returns = {symbol: computed[symbol].pop("_daily_returns") for symbol in symbols}
        return FeatureSheet(
            as_of=requested_at,
            assets=[computed[symbol] for symbol in symbols if symbol in selected],
            corr_30d_btc_eth=_correlation(
                daily_returns["BTC"][-30:], daily_returns["ETH"][-30:]
            ),
            corr_30d_btc_sol=_correlation(
                daily_returns["BTC"][-30:], daily_returns["SOL"][-30:]
            ),
        )

    def marks(self) -> dict[str, float]:
        return self.client.all_mids()

    def account_snapshot(self) -> dict[str, Any] | None:
        if not self.account_address:
            return None
        now = time.monotonic()
        if self._account_cache and now - self._account_cache[0] < 30:
            return self._account_cache[1]
        raw = self.client.clearinghouse_state(self.account_address)
        abstraction = self.client.user_abstraction_state(self.account_address)
        summary = raw.get("marginSummary", {})
        account_value = _float(summary.get("accountValue"))
        withdrawable = _float(raw.get("withdrawable"))
        collateral_source = "clearinghouseState"
        if abstraction in {"unifiedAccount", "portfolioMargin"}:
            spot = self.client.spot_clearinghouse_state(self.account_address)
            account_value, withdrawable = _unified_usdc(spot)
            collateral_source = "spotClearinghouseState"
        sanitized = {
            "network": self.network,
            "address": self.account_address,
            "account_value": account_value,
            "total_notional": _float(summary.get("totalNtlPos")),
            "withdrawable": withdrawable,
            "position_count": len(raw.get("assetPositions", [])),
            "account_abstraction": abstraction,
            "collateral_source": collateral_source,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
        self._account_cache = (now, sanitized)
        return sanitized

    def performance_snapshot(self) -> dict[str, Any] | None:
        if not self.account_address:
            return None
        now = time.monotonic()
        if self._performance_cache and now - self._performance_cache[0] < 5:
            return self._performance_cache[1]
        payload = {
            str(name): data
            for name, data in self.client.portfolio(self.account_address)
            if isinstance(name, str) and isinstance(data, dict)
        }
        ranges: dict[str, Any] = {}
        for label, source in {
            "day": "perpDay",
            "week": "perpWeek",
            "month": "perpMonth",
            "all": "perpAllTime",
        }.items():
            data = payload.get(source, {})
            pnl_points = [
                {"time": int(point[0]), "value": _float(point[1])}
                for point in data.get("pnlHistory", [])
                if isinstance(point, list) and len(point) == 2
            ]
            ranges[label] = {
                "pnl": pnl_points,
                "volume_usd": _float(data.get("vlm")),
                "current_pnl_usd": pnl_points[-1]["value"] if pnl_points else 0.0,
            }
        result = {
            "ranges": ranges,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
        self._performance_cache = (now, result)
        return result

    def _asset_features(
        self,
        symbol: str,
        context: dict[str, Any],
        now_ms: int,
        *,
        candles_1h: list[dict[str, Any]],
        candles_4h: list[dict[str, Any]],
        candles_1d: list[dict[str, Any]],
        book: dict[str, Any],
        funding: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if len(candles_4h) < 45 or len(candles_1d) < 30 or len(candles_1h) < 2:
            raise ValueError(f"insufficient candle history for {symbol}")

        mark = _float(context.get("markPx"))
        if mark <= 0:
            raise ValueError(f"invalid mark price for {symbol}")
        c1h = _closes(candles_1h)
        c4h = _closes(candles_4h)
        c1d = _closes(candles_1d)
        atr_4h = _atr(candles_4h, 14)
        atr_1d = _atr(candles_1d, 14)
        highs_20 = [_float(c["h"]) for c in candles_4h[-20:]]
        lows_20 = [_float(c["l"]) for c in candles_4h[-20:]]
        channel_high, channel_low = max(highs_20), min(lows_20)
        channel_width = max(channel_high - channel_low, 1e-12)
        returns_4h = _log_returns(c4h)
        daily_returns = _log_returns(c1d)
        best_bid, best_ask, book_time = _best_book(book)
        spread_bps = max(0.0, (best_ask - best_bid) / ((best_ask + best_bid) / 2) * 10_000)
        data_age = max(0.0, (now_ms - book_time) / 1000)
        funding_values = [_float(item.get("funding")) * 100 for item in funding]

        return {
            "symbol": symbol,
            "mark_px": mark,
            "max_leverage": int(context.get("maxLeverage") or 20),
            "ret_1h_pct": _return_pct(c1h, 1),
            "ret_4h_pct": _return_pct(c4h, 1),
            "ret_1d_pct": _return_pct(c4h, 6),
            "ret_7d_pct": _return_pct(c4h, 42),
            "atr_4h": atr_4h,
            "adx_4h": _adx(candles_4h, 14),
            "donchian_pos_4h": min(1.0, max(0.0, (mark - channel_low) / channel_width)),
            "dist_ema20_4h_atr": (mark - _ema(c4h, 20)) / max(atr_4h, 1e-12),
            "dist_ema200_1d_atr": (mark - _ema(c1d, min(200, len(c1d)))) / max(atr_1d, 1e-12),
            "rv_24h_ann_pct": _annualized_vol(returns_4h[-6:], periods_per_year=6 * 365),
            "rv_7d_ann_pct": _annualized_vol(returns_4h[-42:], periods_per_year=6 * 365),
            "funding_1h_pct": _float(context.get("funding")) * 100,
            "funding_24h_avg_pct": (
                statistics.fmean(funding_values)
                if funding_values
                else _float(context.get("funding")) * 100
            ),
            "oi_usd": _float(context.get("openInterest")) * mark,
            "oi_change_24h_pct": 0.0,
            "liq_longs_24h_usd": 0.0,
            "liq_shorts_24h_usd": 0.0,
            "swing_high_4h": channel_high,
            "swing_low_4h": channel_low,
            "vwap_1d": _vwap(candles_4h[-6:]),
            "spread_bps": spread_bps,
            "data_age_seconds": data_age,
            "advisors": [],
            "_daily_returns": daily_returns,
        }


def _float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def _unified_usdc(spot_state: dict[str, Any]) -> tuple[float, float]:
    total = 0.0
    hold = 0.0
    for balance in spot_state.get("balances", []):
        if balance.get("coin") == "USDC" or int(balance.get("token", -1)) == 0:
            total = _float(balance.get("total"))
            hold = _float(balance.get("hold"))
            break
    available = max(0.0, total - hold)
    for item in spot_state.get("tokenToAvailableAfterMaintenance", []):
        if isinstance(item, list) and len(item) == 2 and int(item[0]) == 0:
            available = max(0.0, _float(item[1]))
            break
    return total, available


def _closes(candles: list[dict[str, Any]]) -> list[float]:
    return [_float(candle["c"]) for candle in candles]


def _return_pct(values: list[float], periods: int) -> float:
    if len(values) <= periods or values[-periods - 1] <= 0:
        return 0.0
    return (values[-1] / values[-periods - 1] - 1) * 100


def _log_returns(values: list[float]) -> list[float]:
    return [math.log(values[i] / values[i - 1]) for i in range(1, len(values)) if values[i - 1] > 0]


def _true_ranges(candles: list[dict[str, Any]]) -> list[float]:
    ranges: list[float] = []
    for index in range(1, len(candles)):
        high = _float(candles[index]["h"])
        low = _float(candles[index]["l"])
        previous_close = _float(candles[index - 1]["c"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return ranges


def _atr(candles: list[dict[str, Any]], period: int) -> float:
    ranges = _true_ranges(candles)
    return statistics.fmean(ranges[-period:]) if ranges else 0.0


def _adx(candles: list[dict[str, Any]], period: int) -> float:
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(1, len(candles)):
        up = _float(candles[index]["h"]) - _float(candles[index - 1]["h"])
        down = _float(candles[index - 1]["l"]) - _float(candles[index]["l"])
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    tr = sum(_true_ranges(candles)[-period:])
    if tr <= 0:
        return 0.0
    plus_di = 100 * sum(plus_dm[-period:]) / tr
    minus_di = 100 * sum(minus_dm[-period:]) / tr
    denom = plus_di + minus_di
    return 0.0 if denom == 0 else 100 * abs(plus_di - minus_di) / denom


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _annualized_vol(returns: list[float], periods_per_year: int) -> float:
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns) * math.sqrt(periods_per_year) * 100


def _correlation(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    if size < 3:
        return 0.0
    x, y = left[-size:], right[-size:]
    mean_x, mean_y = statistics.fmean(x), statistics.fmean(y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    denom_x = math.sqrt(sum((a - mean_x) ** 2 for a in x))
    denom_y = math.sqrt(sum((b - mean_y) ** 2 for b in y))
    if denom_x == 0 or denom_y == 0:
        return 0.0
    return min(1.0, max(-1.0, numerator / (denom_x * denom_y)))


def _vwap(candles: list[dict[str, Any]]) -> float:
    weighted = 0.0
    volume = 0.0
    for candle in candles:
        current_volume = _float(candle.get("v"))
        typical = (_float(candle["h"]) + _float(candle["l"]) + _float(candle["c"])) / 3
        weighted += typical * current_volume
        volume += current_volume
    return weighted / volume if volume > 0 else _float(candles[-1]["c"])


def _best_book(book: dict[str, Any]) -> tuple[float, float, int]:
    levels = book.get("levels", [])
    if len(levels) != 2 or not levels[0] or not levels[1]:
        raise ValueError("empty Hyperliquid L2 book")
    bid = max(_float(level["px"]) for level in levels[0])
    ask = min(_float(level["px"]) for level in levels[1])
    return bid, ask, int(book.get("time", 0))
