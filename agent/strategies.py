from __future__ import annotations

from typing import Protocol

from agent.domain import StrategySignal
from llm_schemas import AssetFeatures, FeatureSheet


class Strategy(Protocol):
    strategy_id: str

    def evaluate(self, asset: AssetFeatures) -> StrategySignal: ...


class MomentumStrategy:
    strategy_id = "naive_momentum"

    def evaluate(self, asset: AssetFeatures) -> StrategySignal:
        long_setup = asset.ret_4h_pct > 0.8 and asset.adx_4h >= 24 and asset.donchian_pos_4h >= 0.75
        short_setup = (
            asset.ret_4h_pct < -0.8 and asset.adx_4h >= 24 and asset.donchian_pos_4h <= 0.25
        )
        if long_setup:
            score = min(1.0, 0.45 + asset.adx_4h / 100 + asset.ret_4h_pct / 10)
            direction = "LONG"
        elif short_setup:
            score = -min(1.0, 0.45 + asset.adx_4h / 100 + abs(asset.ret_4h_pct) / 10)
            direction = "SHORT"
        else:
            score = 0.0
            direction = "FLAT"
        return StrategySignal(
            strategy_id=self.strategy_id,
            symbol=asset.symbol,
            direction=direction,
            score=score,
            conviction=abs(score),
            rationale=(
                f"ret4h={asset.ret_4h_pct:.2f}%, ADX={asset.adx_4h:.1f}, "
                f"Donchian={asset.donchian_pos_4h:.2f}"
            ),
        )


class MeanReversionStrategy:
    strategy_id = "naive_mean_reversion"

    def evaluate(self, asset: AssetFeatures) -> StrategySignal:
        if asset.adx_4h < 22 and asset.dist_ema20_4h_atr >= 1.4:
            direction, score = "SHORT", -min(0.8, asset.dist_ema20_4h_atr / 3)
        elif asset.adx_4h < 22 and asset.dist_ema20_4h_atr <= -1.4:
            direction, score = "LONG", min(0.8, abs(asset.dist_ema20_4h_atr) / 3)
        else:
            direction, score = "FLAT", 0.0
        return StrategySignal(
            strategy_id=self.strategy_id,
            symbol=asset.symbol,
            direction=direction,
            score=score,
            conviction=abs(score),
            rationale=(f"distance EMA20={asset.dist_ema20_4h_atr:.2f} ATR, ADX={asset.adx_4h:.1f}"),
        )


def run_strategies(feature_sheet: FeatureSheet, strategies: list[Strategy]) -> list[StrategySignal]:
    return [strategy.evaluate(asset) for asset in feature_sheet.assets for strategy in strategies]
