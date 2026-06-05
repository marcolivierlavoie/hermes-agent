"""Regression tests for BIF-650/BIF-654 production exposure."""

from toolsets import _HERMES_CORE_TOOLS


def test_biff_household_tool_is_exposed_to_default_live_biff_surface():
    """The household product must be reachable from live Biff, not only CLI."""
    assert "biff_household" in _HERMES_CORE_TOOLS
