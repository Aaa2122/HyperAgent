from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent.config import AgentMode, Settings
from agent.instruments import (
    XYZ_US_EQUITY_SYMBOLS,
    AssetClass,
    DexDiscoveryStatus,
    ExecutionScope,
    Hip3InstrumentRegistry,
    InstrumentKind,
    UsEquitySessionClock,
    UsEquitySessionStatus,
    VenueMarketStatus,
)
from agent.service import AgentService


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "hip3_xyz_info.json"


class FixtureInfoClient:
    """Offline `/info` double. It intentionally has no exchange method."""

    def __init__(
        self,
        responses: dict[str, Any],
        *,
        failures: set[str] | None = None,
    ) -> None:
        self.responses = responses
        self.failures = failures or set()
        self.requests: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> Any:
        self.requests.append(copy.deepcopy(payload))
        request_type = str(payload.get("type") or "")
        if request_type in self.failures:
            raise RuntimeError(f"fixture failure for {request_type}")
        if request_type not in self.responses:
            raise AssertionError(f"unexpected info request: {payload}")
        if request_type in {"metaAndAssetCtxs", "perpDexStatus"}:
            assert payload == {"type": request_type, "dex": "xyz"}
        return copy.deepcopy(self.responses[request_type])


@pytest.fixture
def xyz_info_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (
            datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            UsEquitySessionStatus.PRE_MARKET,
        ),
        (
            datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc),
            UsEquitySessionStatus.REGULAR,
        ),
        (
            datetime(2026, 7, 13, 21, 0, tzinfo=timezone.utc),
            UsEquitySessionStatus.AFTER_HOURS,
        ),
        (
            datetime(2026, 7, 12, 14, 0, tzinfo=timezone.utc),
            UsEquitySessionStatus.CLOSED,
        ),
    ],
)
def test_us_equity_session_clock_is_explicit_and_dst_aware(
    as_of: datetime, expected: UsEquitySessionStatus
) -> None:
    session = UsEquitySessionClock().at(as_of)

    assert session.status is expected
    assert session.timezone == "America/New_York"
    assert session.local_time.utcoffset() is not None
    assert session.calendar == "weekday_clock_no_holidays"
    assert session.reason


def test_us_equity_session_clock_supports_injected_holidays() -> None:
    as_of = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
    session = UsEquitySessionClock(holidays=[date(2026, 7, 13)]).at(as_of)

    assert session.status is UsEquitySessionStatus.CLOSED
    assert session.reason == "CONFIGURED_MARKET_HOLIDAY"
    assert session.calendar == "weekday_clock_with_configured_holidays"


def test_us_equity_session_clock_rejects_ambiguous_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        UsEquitySessionClock().at(datetime(2026, 7, 13, 10, 0))


def test_xyz_discovery_builds_a_live_registry(
    xyz_info_fixture: dict[str, Any],
) -> None:
    client = FixtureInfoClient(xyz_info_fixture)
    registry = Hip3InstrumentRegistry(client).discover(
        as_of=datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
    )

    assert registry.execution_scope is ExecutionScope.LIVE
    assert registry.venue.status is DexDiscoveryStatus.DISCOVERED
    assert registry.venue.name == "xyz"
    assert registry.venue.full_name == "XYZ"
    assert registry.venue.total_net_deposit == pytest.approx(4_103_492_112.447823)
    assert registry.session.status is UsEquitySessionStatus.REGULAR
    assert tuple(item.symbol for item in registry.instruments) == XYZ_US_EQUITY_SYMBOLS
    assert tuple(item.universe_index for item in registry.instruments) == tuple(
        range(1, 8)
    )

    tsla = registry.get("tsla")
    assert tsla is not None
    assert tsla.instrument_id == "hyperliquid:xyz:TSLA"
    assert tsla.venue_symbol == "xyz:TSLA"
    assert tsla.asset_class is AssetClass.US_EQUITY
    assert tsla.kind is InstrumentKind.HIP3_PERPETUAL
    assert tsla.execution_scope is ExecutionScope.LIVE
    assert tsla.venue_status is VenueMarketStatus.AVAILABLE
    assert tsla.mark_px == 465.13
    assert tsla.paper_eligible is True
    assert tsla.read_only is False
    assert tsla.live_eligible is True

    assert all(item.live_eligible is True for item in registry.instruments)
    assert all(item.paper_eligible is True for item in registry.instruments)
    assert client.requests == [
        {"type": "perpDexs"},
        {"type": "metaAndAssetCtxs", "dex": "xyz"},
        {"type": "perpDexStatus", "dex": "xyz"},
    ]
    assert all(request["type"] != "exchange" for request in client.requests)

    # The public contract is directly JSON serializable for a future API/UI reader.
    serialized = registry.to_dict()
    assert serialized["execution_scope"] == "live"
    assert serialized["instruments"][0]["live_eligible"] is True
    json.dumps(serialized)


def test_missing_allowlisted_market_remains_visible_with_not_listed_status(
    xyz_info_fixture: dict[str, Any],
) -> None:
    meta, contexts = xyz_info_fixture["metaAndAssetCtxs"]
    aapl_index = next(
        index
        for index, item in enumerate(meta["universe"])
        if item["name"] == "xyz:AAPL"
    )
    meta["universe"].pop(aapl_index)
    contexts.pop(aapl_index)

    registry = Hip3InstrumentRegistry(FixtureInfoClient(xyz_info_fixture)).discover(
        as_of=datetime(2026, 7, 13, 21, 0, tzinfo=timezone.utc)
    )

    assert tuple(item.symbol for item in registry.instruments) == XYZ_US_EQUITY_SYMBOLS
    assert registry.venue.status is DexDiscoveryStatus.DISCOVERED
    assert registry.session.status is UsEquitySessionStatus.AFTER_HOURS
    aapl = registry.get("AAPL")
    assert aapl is not None
    assert aapl.venue_status is VenueMarketStatus.NOT_LISTED
    assert aapl.venue_status_reason == "TARGET_NOT_PRESENT_IN_DEX_UNIVERSE"
    assert aapl.paper_eligible is False
    assert aapl.live_eligible is False


def test_instrument_statuses_distinguish_halted_delisted_and_missing_data(
    xyz_info_fixture: dict[str, Any],
) -> None:
    meta, contexts = xyz_info_fixture["metaAndAssetCtxs"]
    by_name = {item["name"]: index for index, item in enumerate(meta["universe"])}
    meta["universe"][by_name["xyz:TSLA"]]["isHalted"] = True
    meta["universe"][by_name["xyz:NVDA"]]["isDelisted"] = True
    contexts[by_name["xyz:AAPL"]]["markPx"] = None

    registry = Hip3InstrumentRegistry(FixtureInfoClient(xyz_info_fixture)).discover(
        as_of=datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
    )

    assert registry.get("TSLA").venue_status is VenueMarketStatus.HALTED  # type: ignore[union-attr]
    assert registry.get("NVDA").venue_status is VenueMarketStatus.DELISTED  # type: ignore[union-attr]
    assert registry.get("AAPL").venue_status is VenueMarketStatus.DATA_UNAVAILABLE  # type: ignore[union-attr]
    assert registry.get("MSFT").venue_status is VenueMarketStatus.AVAILABLE  # type: ignore[union-attr]
    assert registry.get("TSLA").paper_eligible is False  # type: ignore[union-attr]


def test_missing_xyz_dex_returns_explicit_placeholders_without_metadata_calls(
    xyz_info_fixture: dict[str, Any],
) -> None:
    xyz_info_fixture["perpDexs"] = [None, {"name": "other"}]
    client = FixtureInfoClient(xyz_info_fixture)

    registry = Hip3InstrumentRegistry(client).discover(
        as_of=datetime(2026, 7, 12, 14, 0, tzinfo=timezone.utc)
    )

    assert registry.venue.status is DexDiscoveryStatus.NOT_DISCOVERED
    assert registry.venue.status_reason == "DEX_NOT_PRESENT_IN_PERP_DEX_CATALOG"
    assert registry.warnings == ("HIP3_DEX_NOT_DISCOVERED",)
    assert len(registry.instruments) == 7
    assert all(
        item.venue_status is VenueMarketStatus.NOT_LISTED
        for item in registry.instruments
    )
    assert registry.session.status is UsEquitySessionStatus.CLOSED
    assert client.requests == [{"type": "perpDexs"}]


def test_info_outage_is_reported_without_dropping_instruments(
    xyz_info_fixture: dict[str, Any],
) -> None:
    client = FixtureInfoClient(xyz_info_fixture, failures={"perpDexs"})

    registry = Hip3InstrumentRegistry(client).discover(
        as_of=datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
    )

    assert registry.venue.status is DexDiscoveryStatus.DATA_UNAVAILABLE
    assert registry.warnings == ("HIP3_DEX_CATALOG_UNAVAILABLE",)
    assert len(registry.instruments) == 7
    assert all(
        item.venue_status is VenueMarketStatus.DATA_UNAVAILABLE
        for item in registry.instruments
    )
    assert all(item.live_eligible is False for item in registry.instruments)


def test_optional_dex_status_outage_does_not_hide_valid_metadata(
    xyz_info_fixture: dict[str, Any],
) -> None:
    client = FixtureInfoClient(xyz_info_fixture, failures={"perpDexStatus"})

    registry = Hip3InstrumentRegistry(client).discover(
        as_of=datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
    )

    assert registry.venue.status is DexDiscoveryStatus.DISCOVERED
    assert registry.venue.status_reason == "DEX_DISCOVERED_STATUS_UNAVAILABLE"
    assert registry.warnings == ("HIP3_DEX_STATUS_UNAVAILABLE",)
    assert all(
        item.venue_status is VenueMarketStatus.AVAILABLE
        for item in registry.instruments
    )


def test_service_exposes_and_caches_the_read_only_registry(
    xyz_info_fixture: dict[str, Any],
) -> None:
    service = AgentService(
        Settings(
            _env_file=None,
            agent_mode=AgentMode.PAPER,
            database_url="sqlite://",
            llm_provider="rules",
            automation_enabled=False,
        )
    )
    client = FixtureInfoClient(xyz_info_fixture)
    service._instrument_registry_client = client

    first = service.instrument_registry()
    second = service.instrument_registry()

    assert first == second
    assert first["execution_scope"] == "live"
    assert len(first["instruments"]) == 7
    assert all(item["live_eligible"] is True for item in first["instruments"])
    assert len(client.requests) == 3
