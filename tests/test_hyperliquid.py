from __future__ import annotations

import json
import math
import time

import httpx

from agent.hyperliquid import HyperliquidInfoClient, HyperliquidMarketData


def test_hyperliquid_snapshot_and_account_are_read_only_and_typed() -> None:
    now_ms = int(time.time() * 1000)
    bases = {"BTC": 65_000.0, "ETH": 3_400.0, "SOL": 155.0}

    def candles(symbol: str, interval: str) -> list[dict]:
        interval_ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[interval]
        count = {"1h": 12, "4h": 90, "1d": 240}[interval]
        result = []
        phase = {"BTC": 0.0, "ETH": 0.4, "SOL": 0.8}[symbol]
        for index in range(count):
            px = bases[symbol] * (0.90 + index * 0.0005 + 0.002 * math.sin(index / 4 + phase))
            result.append(
                {
                    "t": now_ms - (count - index) * interval_ms,
                    "T": now_ms - (count - index - 1) * interval_ms - 1,
                    "s": symbol,
                    "i": interval,
                    "o": str(px * 0.998),
                    "h": str(px * 1.006),
                    "l": str(px * 0.994),
                    "c": str(px),
                    "v": str(100 + index),
                    "n": 10,
                }
            )
        return result

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        request_type = payload["type"]
        if request_type == "metaAndAssetCtxs":
            return httpx.Response(
                200,
                json=[
                    {"universe": [{"name": symbol} for symbol in bases]},
                    [
                        {
                            "markPx": str(bases[symbol]),
                            "funding": "0.0000125",
                            "openInterest": "1000",
                        }
                        for symbol in bases
                    ],
                ],
            )
        if request_type == "candleSnapshot":
            req = payload["req"]
            return httpx.Response(200, json=candles(req["coin"], req["interval"]))
        if request_type == "l2Book":
            mark = bases[payload["coin"]]
            return httpx.Response(
                200,
                json={
                    "coin": payload["coin"],
                    "time": now_ms,
                    "levels": [
                        [{"px": str(mark * 0.9999), "sz": "1", "n": 1}],
                        [{"px": str(mark * 1.0001), "sz": "1", "n": 1}],
                    ],
                },
            )
        if request_type == "fundingHistory":
            return httpx.Response(
                200,
                json=[{"funding": "0.00001", "time": now_ms - hour * 3} for hour in range(8)],
            )
        if request_type == "clearinghouseState":
            return httpx.Response(
                200,
                json={
                    "marginSummary": {"accountValue": "1000", "totalNtlPos": "250"},
                    "withdrawable": "750",
                    "assetPositions": [{"position": {"coin": "BTC"}}],
                },
            )
        if request_type == "userAbstraction":
            return httpx.Response(200, json="unifiedAccount")
        if request_type == "spotClearinghouseState":
            return httpx.Response(
                200,
                json={
                    "balances": [{"coin": "USDC", "token": 0, "total": "1000", "hold": "50"}],
                    "tokenToAvailableAfterMaintenance": [[0, "900"]],
                },
            )
        return httpx.Response(400, json={"error": "unexpected request"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    info = HyperliquidInfoClient("https://mock.hyperliquid", client=http_client)
    provider = HyperliquidMarketData(
        info,
        network="mainnet",
        account_address="0x" + "1" * 40,
    )

    snapshot = provider.snapshot()
    assert provider.name == "hyperliquid-mainnet"
    assert [asset.symbol for asset in snapshot.assets] == ["BTC", "ETH", "SOL"]
    assert all(asset.data_age_seconds < 5 for asset in snapshot.assets)
    assert all(asset.spread_bps > 0 for asset in snapshot.assets)
    assert all(asset.atr_4h > 0 for asset in snapshot.assets)
    assert -1 <= snapshot.corr_30d_btc_eth <= 1

    account = provider.account_snapshot()
    assert account is not None
    assert account["account_value"] == 1000
    assert account["withdrawable"] == 900
    assert account["account_abstraction"] == "unifiedAccount"
    assert account["collateral_source"] == "spotClearinghouseState"
    assert account["position_count"] == 1
