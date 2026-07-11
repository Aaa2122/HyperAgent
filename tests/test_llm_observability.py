from types import SimpleNamespace

from agent.llm_observability import usage_payload


def test_usage_payload_reads_exact_xai_cost_and_chat_tokens() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            cost_in_usd_ticks=25_000_000,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
            num_server_side_tools_used=2,
        )
    )

    payload = usage_payload(response)

    assert payload["input_tokens"] == 120
    assert payload["cached_tokens"] == 80
    assert payload["output_tokens"] == 30
    assert payload["reasoning_tokens"] == 12
    assert payload["cost_usd"] == 0.0025
    assert payload["tool_usage"]["num_server_side_tools_used"] == 2
