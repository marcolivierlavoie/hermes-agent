from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path('/Users/marco/.hermes/scripts/biff_role_invoke.py')


def load_role_invoke():
    if not SCRIPT.exists():
        pytest.skip('Biff role invocation script is not installed on this host')
    spec = importlib.util.spec_from_file_location('biff_role_invoke_under_test', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ops_summary_includes_operational_prose_without_raw_log_spam():
    module = load_role_invoke()

    summary = module.format_ops_summary(
        role='forge',
        state='done',
        task='K-1382',
        goal='Implement clean #biff-ops summaries and keep #hermes usable.',
        next_step='Vex should simulate Forge, Vex, and Ranger paths.',
        progress='Updated summary templates for start, progress, blockers, and done.',
        blockers='',
        evidence='Focused tests passed: 3 simulated roles.',
    )

    assert summary.startswith('K-1382 — Forge completed role run')
    assert 'Goal: Implement clean #biff-ops summaries and keep #hermes usable.' in summary
    assert 'State: Role run completed; this is completion of the specialist process, not final board closure.' in summary
    assert 'Progress: Updated summary templates for start, progress, blockers, and done.' in summary
    assert 'Next: Vex should simulate Forge, Vex, and Ranger paths.' in summary
    assert 'Evidence: Focused tests passed: 3 simulated roles.' in summary
    assert 'stdout' not in summary.lower()
    assert 'stderr' not in summary.lower()
    assert 'Traceback' not in summary
    assert len(summary) < 1200


def test_ops_progress_defaults_to_disabled(monkeypatch):
    module = load_role_invoke()

    monkeypatch.delenv('HERMES_BIFF_OPS_PROGRESS_ENABLED', raising=False)
    monkeypatch.delenv('HERMES_BIFF_ROLE_PROGRESS_INTERVAL', raising=False)

    interval, enabled = module.role_progress_settings()

    assert enabled is False
    assert interval == 120


@pytest.mark.parametrize('role,state', [('forge', 'started'), ('vex', 'working'), ('ranger', 'blocked')])
def test_ops_summary_covers_role_paths_and_blockers(role: str, state: str):
    module = load_role_invoke()

    summary = module.format_ops_summary(
        role=role,
        state=state,
        task='role_123',
        goal='Keep Marco informed with useful operational prose.',
        next_step='Continue the safe queue.',
        progress='Role selected and task is running in its own channel.',
        blockers='Needs Vex verification.' if state == 'blocked' else '',
        evidence='',
    )

    expected_headline_state = 'completed role run' if state == 'done' else state
    assert f'— {role.title()} {expected_headline_state}' in summary
    assert 'Goal:' in summary
    assert 'State:' in summary
    assert 'Next:' in summary
    if state == 'blocked':
        assert 'Blockers: Needs Vex verification.' in summary
    else:
        assert 'Blockers:' not in summary


def test_ops_summary_suppresses_long_transcripts_and_secrets():
    module = load_role_invoke()
    noisy = 'line\n' * 400 + 'sk-tes...7890 and stderr dump'

    summary = module.format_ops_summary(
        role='vex',
        state='done',
        task='K-1383',
        goal=noisy,
        next_step=noisy,
        progress=noisy,
        blockers=noisy,
        evidence=noisy,
    )

    assert len(summary) <= 1200
    assert '***' not in summary
    assert 'stderr dump' not in summary
    assert 'line\nline\nline\nline\nline\n' not in summary


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('PASS\nVerified the production path.', 'PASS'),
        ('Vex decision: PASS\nEvidence follows.', 'PASS'),
        ('BLOCKED\nThe explanation says it would PASS after a restart.', 'BLOCKED'),
        ('Decision: BLOCKED\nUseful evidence exists.', 'BLOCKED'),
        ('Evidence: focused tests PASS but no decision line.', 'UNKNOWN'),
        ('PASS\nBLOCKED\nContradictory top-level output.', 'UNKNOWN'),
        ('', 'UNKNOWN'),
    ],
)
def test_vex_decision_parser_requires_explicit_top_level_decision(text: str, expected: str):
    module = load_role_invoke()

    assert module.parse_vex_decision(text) == expected


@pytest.mark.parametrize(
    ('vex_stdout', 'expected_exit'),
    [
        ('PASS\nFocused checks passed.', 0),
        ('BLOCKED\nUseful evidence exists; this would PASS after one more fix.', 2),
        ('Focused checks passed but no explicit decision.', 2),
    ],
)
def test_vex_role_process_only_unlocks_on_explicit_pass(monkeypatch, vex_stdout: str, expected_exit: int):
    module = load_role_invoke()

    class FakeProcess:
        returncode = 0

        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return self.returncode

        def communicate(self):
            return vex_stdout, ''

    monkeypatch.setattr(module.subprocess, 'Popen', FakeProcess)
    monkeypatch.setattr(module, 'post', lambda *args, **kwargs: 'dry-run')
    monkeypatch.setattr(
        module.sys,
        'argv',
        ['biff_role_invoke.py', 'vex', 'verify the controller gate', '--dry-run'],
    )

    assert module.main() == expected_exit


def test_ops_summary_strips_entire_code_fences_diffs_and_log_lines():
    module = load_role_invoke()
    adversarial = '''Semantic blocker: Vex found controller closure can still proceed.
```python
print("do not leak fenced body")
secret_body = "this should be gone"
```
diff --git a/gateway/run.py b/gateway/run.py
@@ -1,2 +1,2 @@
- old raw code
+ new raw code
[INFO] stdout raw worker transcript
ERROR stack trace body
Keep semantic next step only.'''

    summary = module.format_ops_summary(
        role='vex',
        state='blocked',
        task='K-test',
        goal=adversarial,
        next_step=adversarial,
        progress=adversarial,
        blockers=adversarial,
        evidence=adversarial,
    )

    assert 'Semantic blocker: Vex found controller closure can still proceed.' in summary
    assert 'Keep semantic next step only.' in summary
    assert 'do not leak fenced body' not in summary
    assert 'secret_body' not in summary
    assert 'diff --git' not in summary
    assert '@@' not in summary
    assert 'old raw code' not in summary
    assert 'new raw code' not in summary
    assert 'stdout raw worker transcript' not in summary
    assert 'stack trace body' not in summary
