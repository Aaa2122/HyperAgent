from __future__ import annotations

import hashlib
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.domain import ApprovedOrder


PROTECTION_NAMESPACE = uuid.UUID("d3f381ad-dd73-4ea7-8b2d-cb1a556f4fc5")


class ProtectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protection_id: str
    cloid: str
    symbol: str
    direction: Literal["LONG", "SHORT"]
    kind: Literal["SL", "TP"]
    level_index: int = Field(ge=0, le=4)
    trigger_px: float = Field(gt=0)
    size_fraction: float = Field(gt=0, le=1)
    original_notional_usd: float = Field(gt=0)


def target_allocations(count: int) -> list[float]:
    if count <= 0:
        return []
    return {
        1: [1.0],
        2: [0.60, 0.40],
        3: [0.50, 0.30, 0.20],
        4: [0.40, 0.30, 0.20, 0.10],
    }[min(count, 4)]


def _identity(parent_cloid: str, kind: str, index: int, trigger_px: float) -> tuple[str, str]:
    raw = f"{parent_cloid}:{kind}:{index}:{trigger_px:.8f}"
    protection_uuid = uuid.uuid5(PROTECTION_NAMESPACE, raw)
    cloid = "0x" + hashlib.sha256(protection_uuid.bytes).digest()[:16].hex()
    return str(protection_uuid), cloid


def build_protection_specs(order: ApprovedOrder, parent_cloid: str) -> list[ProtectionSpec]:
    specs: list[ProtectionSpec] = []
    if order.place_stop_order:
        protection_id, cloid = _identity(
            parent_cloid, "SL", 0, order.invalidation_px
        )
        specs.append(ProtectionSpec(
            protection_id=protection_id,
            cloid=cloid,
            symbol=order.symbol,
            direction=order.direction,
            kind="SL",
            level_index=0,
            trigger_px=order.invalidation_px,
            size_fraction=1.0,
            original_notional_usd=order.notional_usd,
        ))
    targets = order.targets[:len(order.take_profit_fractions)]
    for index, (trigger_px, fraction) in enumerate(
        zip(targets, order.take_profit_fractions),
        start=1,
    ):
        protection_id, cloid = _identity(parent_cloid, "TP", index, trigger_px)
        specs.append(
            ProtectionSpec(
                protection_id=protection_id,
                cloid=cloid,
                symbol=order.symbol,
                direction=order.direction,
                kind="TP",
                level_index=index,
                trigger_px=trigger_px,
                size_fraction=fraction,
                original_notional_usd=order.notional_usd,
            )
        )
    return specs
