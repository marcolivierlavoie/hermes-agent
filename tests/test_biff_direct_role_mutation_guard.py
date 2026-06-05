import json

from model_tools import handle_function_call


def test_quill_direct_role_blocks_patch(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_DIRECT_ROLE", "quill")
    monkeypatch.delenv("HERMES_BIFF_ALLOW_MUTATION", raising=False)

    out = handle_function_call(
        "patch",
        {"mode": "patch", "patch": "*** Begin Patch\n*** End Patch\n"},
        skip_pre_tool_call_hook=True,
    )
    data = json.loads(out)

    assert "read-only for file mutations" in data["error"]
    assert "Forge" in data["error"]


def test_forge_direct_role_does_not_block_patch(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_DIRECT_ROLE", "forge")

    out = handle_function_call(
        "patch",
        {"mode": "patch", "patch": "*** Begin Patch\n*** End Patch\n"},
        skip_pre_tool_call_hook=True,
    )
    data = json.loads(out)

    assert "read-only for file mutations" not in data.get("error", "")
