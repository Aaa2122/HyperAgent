from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Protocol

from llm_schemas import FeatureSheet


class MarketDataProvider(Protocol):
    name: str
    quality_warnings: list[str]

    def snapshot(self) -> FeatureSheet: ...

    def marks(self) -> dict[str, float]: ...

    def activation_metrics(self) -> dict: ...


class PaperMarketData:
    """Deterministic market stream for PAPER and CI; never submits venue requests."""

    def __init__(self) -> None:
        self.name = "paper-deterministic"
        self.quality_warnings = ["SYNTHETIC_MARKET_DATA"]
        self.tick = 0
        self._last_marks = {"BTC": 65_000.0, "ETH": 3_400.0, "SOL": 155.0}

    def snapshot(self) -> FeatureSheet:
        self.tick += 1
        phase = self.tick / 8.0
        prices = {
            "BTC": 65_000.0 * (1 + 0.0015 * math.sin(phase)),
            "ETH": 3_400.0 * (1 + 0.0010 * math.sin(phase + 0.6)),
            "SOL": 155.0 * (1 + 0.0020 * math.sin(phase + 1.2)),
        }
        self._last_marks = prices
        return FeatureSheet(
            as_of=datetime.now(timezone.utc),
            assets=[
                self._asset(
                    "BTC",
                    prices["BTC"],
                    ret_4h=2.4,
                    adx=34.0,
                    donchian=0.91,
                    ema_distance=1.25,
                    funding=0.008,
                ),
                self._asset(
                    "ETH",
                    prices["ETH"],
                    ret_4h=0.15,
                    adx=14.0,
                    donchian=0.52,
                    ema_distance=0.08,
                    funding=0.004,
                ),
                self._asset(
                    "SOL",
                    prices["SOL"],
                    ret_4h=-2.1,
                    adx=31.0,
                    donchian=0.10,
                    ema_distance=-1.8,
                    funding=0.031,
                ),
            ],
            corr_30d_btc_eth=0.86,
            corr_30d_btc_sol=0.73,
        )

    def marks(self) -> dict[str, float]:
        return dict(self._last_marks)

    def activation_metrics(self) -> dict:
        """Synthetic but deterministic liquidity evidence for PAPER/CI."""

        return {
            "as_of": datetime.now(timezone.utc),
            "source": "paper_deterministic",
            "assets": [
                {
                    "symbol": "BTC",
                    "volume_24h_usd": 1_500_000_000.0,
                    "open_interest_usd": 2_400_000_000.0,
                },
                {
                    "symbol": "ETH",
                    "volume_24h_usd": 900_000_000.0,
                    "open_interest_usd": 1_200_000_000.0,
                },
                {
                    "symbol": "SOL",
                    "volume_24h_usd": 450_000_000.0,
                    "open_interest_usd": 450_000_000.0,
                },
            ],
        }

    @staticmethod
    def _asset(
        symbol: str,
        mark: float,
        *,
        ret_4h: float,
        adx: float,
        donchian: float,
        ema_distance: float,
        funding: float,
    ) -> dict:
        atr = mark * 0.016
        return {
            "symbol": symbol,
            "mark_px": mark,
            "max_leverage": {"BTC": 40, "ETH": 25, "SOL": 20}[symbol],
            "ret_1h_pct": ret_4h / 3.5,
            "ret_4h_pct": ret_4h,
            "ret_1d_pct": ret_4h * 1.7,
            "ret_7d_pct": ret_4h * 3.2,
            "atr_4h": atr,
            "adx_4h": adx,
            "donchian_pos_4h": donchian,
            "dist_ema20_4h_atr": ema_distance,
            "dist_ema200_1d_atr": ema_distance * 1.8,
            "rv_24h_ann_pct": 42.0 if symbol != "SOL" else 66.0,
            "rv_7d_ann_pct": 39.0 if symbol != "SOL" else 61.0,
            "funding_1h_pct": funding,
            "funding_24h_avg_pct": funding * 0.8,
            "oi_usd": {"BTC": 2.4e9, "ETH": 1.2e9, "SOL": 4.5e8}[symbol],
            "oi_change_24h_pct": 7.2 if symbol == "BTC" else 2.0,
            "liq_longs_24h_usd": 12e6,
            "liq_shorts_24h_usd": 19e6,
            "swing_high_4h": mark * 1.035,
            "swing_low_4h": mark * 0.955,
            "vwap_1d": mark * (0.992 if ret_4h > 0 else 1.008),
            "spread_bps": 1.1 if symbol != "SOL" else 2.3,
            "data_age_seconds": 1.0,
            "advisors": [],
        }
