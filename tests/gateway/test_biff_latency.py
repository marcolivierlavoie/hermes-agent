from pathlib import Path

from gateway.biff_latency import (
    load_biff_latency_records,
    record_biff_latency,
    render_biff_latency_report,
    summarize_biff_latency,
)


def test_record_biff_latency_persists_only_discord(tmp_path: Path):
    path = tmp_path / "latency.jsonl"

    record_biff_latency(
        platform="slack",
        chat_id="c",
        session_id="s",
        model="m",
        response_time=9,
        wall_metrics={},
        path=path,
    )
    record_biff_latency(
        platform="discord",
        chat_id="c",
        session_id="s",
        model="m",
        response_time=3.5,
        wall_metrics={"wall_time": 3.5, "agent_loop_time": 3.0},
        api_calls=2,
        last_prompt_tokens=12000,
        path=path,
        now=1000,
    )

    records = load_biff_latency_records(path=path)
    assert len(records) == 1
    assert records[0]["platform"] == "discord"
    assert records[0]["response_time"] == 3.5
    assert records[0]["agent_loop_time"] == 3.0


def test_biff_latency_summary_and_report_show_p50_p95_and_slowest(tmp_path: Path):
    path = tmp_path / "latency.jsonl"
    for i, seconds in enumerate([1, 2, 3, 4, 20]):
        record_biff_latency(
            platform="discord",
            chat_id="c",
            session_id=f"s{i}",
            model="m",
            response_time=seconds,
            wall_metrics={"wall_time": seconds, "agent_loop_time": seconds - 0.1},
            api_calls=i + 1,
            last_prompt_tokens=5000 + i,
            prompt_budget_applied=1 if i else 0,
            prompt_budget_tokens=10000,
            prompt_budget_omitted_messages=i,
            path=path,
            now=1000 + i,
        )

    summary = summarize_biff_latency(load_biff_latency_records(path=path))
    assert summary["count"] == 5
    assert summary["p50"] == 3
    assert summary["p95"] > 15

    report = render_biff_latency_report(path=path)
    assert "p50: 3.0s" in report
    assert "p95:" in report
    assert "prompt target: 5/5 recent turns <= 10,000 tokens" in report
    assert "live prompt budget trimmed 10 old messages across 4 turns" in report
    assert "Slowest recent turns:" in report
    assert "mostly model/tool work" in report
