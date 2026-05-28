"""Regression coverage for Biff gateway run_sync closure scope crashes."""

from __future__ import annotations

import ast
import inspect
import textwrap

from gateway.run import GatewayRunner


def _run_sync_ast() -> ast.FunctionDef:
    source = textwrap.dedent(inspect.getsource(GatewayRunner._run_agent))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_sync":
            return node
    raise AssertionError("GatewayRunner._run_agent no longer defines run_sync")


def test_run_sync_does_not_shadow_outer_platform_key():
    """Biff diagnostics must not crash from a closure-local platform_key.

    K-1388 reproduced as UnboundLocalError after diagnostic code read
    platform_key before a later assignment in run_sync.  Keep platform_key
    resolved in the outer _run_agent scope so future diagnostic insertions do
    not accidentally create the same crash.
    """

    run_sync = _run_sync_ast()
    stores = []
    for node in ast.walk(run_sync):
        if isinstance(node, ast.Name) and node.id == "platform_key" and isinstance(node.ctx, ast.Store):
            stores.append(node.lineno)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name) and node.target.id == "platform_key":
            stores.append(node.lineno)

    assert stores == []


def test_run_sync_initializes_max_iterations_before_agent_construction():
    """Gateway Codex runs should not crash with NameError on max_iterations."""

    run_sync = _run_sync_ast()
    max_assignment_lines = []
    max_use_lines = []

    for node in ast.walk(run_sync):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "max_iterations":
                    max_assignment_lines.append(node.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "max_iterations":
            max_assignment_lines.append(node.lineno)
        elif isinstance(node, ast.keyword) and node.arg == "max_iterations":
            max_use_lines.append(node.value.lineno)

    assert max_assignment_lines, "run_sync must initialize max_iterations"
    assert max_use_lines, "run_sync should pass max_iterations into AIAgent"
    assert min(max_assignment_lines) < min(max_use_lines)


def test_run_sync_sets_toolset_recall_ceiling_before_run_conversation():
    """Selective Biff tool routing must preserve the configured ceiling.

    Recall-on-miss can safely widen a narrowed live tool schema only if the
    gateway gives the agent the platform-configured ceiling before the turn
    enters run_conversation.
    """

    source = textwrap.dedent(inspect.getsource(GatewayRunner._run_agent))
    ceiling_idx = source.index("agent._toolset_recall_ceiling = list(_configured_toolsets)")
    run_idx = source.index("result = agent.run_conversation(")

    assert ceiling_idx < run_idx


def test_run_sync_evicts_cached_agent_after_toolset_recall_events():
    """A recall-widened cached agent must not leak tools into next narrow turn."""

    source = textwrap.dedent(inspect.getsource(GatewayRunner._run_agent))
    run_idx = source.index("result = agent.run_conversation(")
    event_idx = source.index('result.get("toolset_recall_events")')
    evict_idx = source.index("self._evict_cached_agent(session_key)", event_idx)

    assert run_idx < event_idx < evict_idx
