# BIF-1526 Tool Router Telemetry / Feedback Report

This slice adds a sanitized local telemetry stream and report surface for Biff/Discord tool-router decisions.

## Recorded fields

Each Discord turn records:

- route class
- derived confidence tier
- selected toolsets, plus configured toolset count
- memory tier
- recall-on-miss count and sanitized recall metadata
- fallback/full-surface use
- model/provider labels
- prompt/tool-schema size fields when available
- wall-clock latency
- outcome
- prompt fingerprint only (`chars` + `sha256_16`), never raw prompt text

## Local report command

```bash
python scripts/router_feedback_report.py --limit 200
python scripts/router_feedback_report.py --json --limit 200
```

The report groups baseline/off and router/on runs so router-enabled schema/latency/prompt characteristics can be compared against baseline.

## Privacy review

- Raw prompt text is not logged; telemetry stores only prompt fingerprints.
- Raw memory content is fingerprinted/redacted if it appears in nested diagnostic payloads.
- Secret-like keys such as `api_key`, `token`, `secret`, `password`, `credential`, and `auth` are replaced with `[REDACTED]`.
- Long free-form strings are bounded.
- Events are written to the local Hermes runtime directory only.

## Sample report shape

```text
Biff tool-router feedback, last N Discord turns:
- baseline/off: ... turns, p50 ...s, avg schema ... chars, avg prompt ... tokens
- router/on: ... turns, p50 ...s, avg schema ... chars, avg prompt ... tokens
- all outcomes: ...
- routes: ...
- memory tiers: ...
- fallback/full-surface turns: ...
- recall-on-miss events: ...
- privacy: stored prompt fingerprints only; raw prompts, memory content, and secret-like fields are omitted/redacted.
```
