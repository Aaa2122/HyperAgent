from __future__ import annotations

from typing import Any


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json(value.model_dump(mode="json"))
    return str(value)


def usage_payload(response: Any) -> dict[str, Any]:
    """Normalize xAI Chat/Responses usage, including exact billed USD ticks."""
    usage = _get(response, "usage", {}) or {}
    input_tokens = int(_get(usage, "input_tokens", _get(usage, "prompt_tokens", 0)) or 0)
    output_tokens = int(
        _get(usage, "output_tokens", _get(usage, "completion_tokens", 0)) or 0
    )
    input_details = _get(
        usage, "input_tokens_details", _get(usage, "prompt_tokens_details", {})
    ) or {}
    output_details = _get(
        usage, "output_tokens_details", _get(usage, "completion_tokens_details", {})
    ) or {}
    ticks = int(_get(usage, "cost_in_usd_ticks", 0) or 0)
    server_usage = _get(response, "server_side_tool_usage", {}) or {}
    if not server_usage:
        server_usage = {
            "num_server_side_tools_used": int(
                _get(usage, "num_server_side_tools_used", 0) or 0
            )
        }
    return {
        "input_tokens": input_tokens,
        "cached_tokens": int(_get(input_details, "cached_tokens", 0) or 0),
        "output_tokens": output_tokens,
        "reasoning_tokens": int(_get(output_details, "reasoning_tokens", 0) or 0),
        "cost_usd": ticks / 10_000_000_000,
        "tool_usage": _json(server_usage),
    }


def llm_record(
    response: Any,
    *,
    cycle_id: str | None,
    stage: str,
    model: str,
    latency_ms: int,
    prompt: Any,
    result: Any,
) -> dict[str, Any]:
    return {
        "cycle_id": cycle_id,
        "stage": stage,
        "provider": "xai",
        "model": model,
        "status": "COMPLETED",
        "latency_ms": latency_ms,
        "prompt": _json(prompt),
        "response": _json(result),
        **usage_payload(response),
    }
