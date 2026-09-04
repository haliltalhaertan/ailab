from __future__ import annotations

from lab.ui_live import build_cards, merge_cards


def _start(step_key: str = "iter:1:proposer") -> dict:
    return {
        "ts": "2026-09-03T12:00:00+00:00",
        "type": "agent_start",
        "agent": "Theorist",
        "model": "fake/model",
        "reasoning_effort": "high",
        "step_key": step_key,
        "system_prompt": "system",
        "prompt": "task",
    }


def test_build_cards_reduces_stream_to_one_done_card():
    events = [
        _start(),
        {
            "type": "agent_stream",
            "agent": "Theorist",
            "step_key": "iter:1:proposer",
            "channel": "reasoning",
            "delta": "A",
        },
        {
            "type": "agent_stream",
            "agent": "Theorist",
            "step_key": "iter:1:proposer",
            "channel": "reasoning",
            "delta": "B",
        },
        {
            "type": "agent_stream",
            "agent": "Theorist",
            "step_key": "iter:1:proposer",
            "channel": "content",
            "delta": "draft",
        },
        {
            "type": "llm_call",
            "agent": "Theorist",
            "model": "fake/model",
            "step_key": "iter:1:proposer",
            "provider_reasoning": "AB final",
            "output": "final answer",
            "total_tokens": 123,
            "reasoning_tokens": 80,
            "prompt_tokens": 20,
            "completion_tokens": 103,
            "cached_tokens": 4,
            "cost_usd": 0.002,
            "latency_s": 3.5,
            "finish_reason": "stop",
            "truncated": False,
            "requested_max_tokens": 384000,
            "model_max_completion_tokens": 384000,
            "max_tokens_source": "catalog",
            "catalog_source": "memory",
            "answer_chars": 12,
            "reasoning_effort_requested": "high",
            "reasoning_effort_sent": "high",
            "effort_resolution": "exact",
            "reasoning_max_tokens_sent": None,
        },
        {
            "type": "truncated_retry",
            "agent": "Theorist",
            "step_key": "iter:1:proposer",
            "outcome": "recovered",
        },
    ]

    cards = build_cards(events)

    assert len(cards) == 1
    card = cards[0]
    assert card.step_key == "iter:1:proposer"
    assert card.status == "done"
    assert card.reasoning == "AB final"
    assert card.content == "final answer"
    assert card.total_tokens == 123
    assert card.reasoning_tokens == 80
    assert card.cost_usd == 0.002
    assert card.latency_s == 3.5
    assert card.finish_reason == "stop"
    assert card.requested_max_tokens == 384000
    assert card.model_max_completion_tokens == 384000
    assert card.max_tokens_source == "catalog"
    assert card.catalog_source == "memory"
    assert card.answer_chars == 12
    assert card.reasoning_effort_requested == "high"
    assert card.reasoning_effort_sent == "high"
    assert card.effort_resolution == "exact"
    assert card.truncation_retry == "recovered"


def test_build_cards_without_llm_call_stays_running():
    cards = build_cards(
        [
            _start(),
            {
                "type": "agent_stream",
                "agent": "Theorist",
                "step_key": "iter:1:proposer",
                "channel": "content",
                "delta": "partial",
            },
        ]
    )

    assert len(cards) == 1
    assert cards[0].status == "running"
    assert cards[0].content == "partial"


def test_build_cards_agent_error_marks_card_error():
    cards = build_cards(
        [
            _start(),
            {
                "type": "agent_error",
                "agent": "Theorist",
                "model": "fake/model",
                "step_key": "iter:1:proposer",
                "error": "provider failed",
            },
        ]
    )

    assert len(cards) == 1
    assert cards[0].status == "error"
    assert cards[0].error == "provider failed"


def test_merge_cards_updates_only_new_active_text_and_preserves_done_card():
    done = build_cards(
        [
            _start("iter:1:proposer"),
            {
                "type": "llm_call",
                "agent": "Theorist",
                "model": "fake/model",
                "output": "done",
                "total_tokens": 5,
            },
        ]
    )
    current = build_cards([_start("iter:2:proposer")])
    cards = merge_cards(done + current, [
        {
            "type": "agent_stream",
            "agent": "Theorist",
            "step_key": "iter:2:proposer",
            "channel": "reasoning",
            "delta": "new reasoning",
        }
    ])

    by_key = {card.step_key: card for card in cards}
    assert by_key["iter:1:proposer"].status == "done"
    assert by_key["iter:1:proposer"].content == "done"
    assert by_key["iter:2:proposer"].status == "running"
    assert by_key["iter:2:proposer"].reasoning == "new reasoning"
