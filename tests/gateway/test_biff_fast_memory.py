import json

from gateway import biff_fast_memory as fast_memory


class FakeMnemosyne:
    def handle_tool_call(self, tool_name, args):
        assert tool_name == "mnemosyne_memory"
        if args["action"] == "memory_digest":
            return json.dumps(
                {
                    "success": True,
                    "needs_decision": {"pending_candidate_count": 1, "conflict_memory_ids": ["mem-conflict"]},
                    "fyi": {"trusted_memory_count": 7, "active_suppression_count": 2},
                }
            )
        if args["action"] == "recall_policy":
            return json.dumps(
                {
                    "success": True,
                    "authority_order": [
                        "current_user_instruction",
                        "linear_and_secondbrain_source_docs",
                        "mnemosyne_trusted_memory",
                    ],
                }
            )
        raise AssertionError(args)

    def recall(self, query, *, limit, include_suppressed):
        return [
            {
                "memory": {
                    "id": "mem-safe",
                    "content": "Biff uses Discord as the primary live command surface.",
                    "source": "test fixture",
                    "context": "command surface",
                    "sensitivity": "non_sensitive",
                    "current_request_safe": True,
                }
            },
            {
                "memory": {
                    "id": "mem-secret",
                    "content": "token: should-not-leak",
                    "source": "secret fixture",
                    "sensitivity": "sensitive",
                    "current_request_safe": True,
                }
            },
        ]


def test_biff_fast_memory_snapshot_is_llm_free_bounded_and_safe(monkeypatch):
    monkeypatch.setattr(fast_memory, "_provider", lambda: FakeMnemosyne())

    snapshot = fast_memory.build_biff_fast_memory_snapshot({}, query="Discord command surface")

    assert "Biff Fast Memory Snapshot" in snapshot
    assert "LLM-free" in snapshot
    assert "7 trusted" in snapshot
    assert "mem-conflict" in snapshot
    assert "Biff uses Discord" in snapshot
    assert "should-not-leak" not in snapshot
    assert len(snapshot) <= 1400


def test_biff_fast_memory_snapshot_can_be_disabled(monkeypatch):
    monkeypatch.setattr(fast_memory, "_provider", lambda: FakeMnemosyne())
    cfg = {"biff": {"platforms": {"discord": {"fast_memory_context": False}}}}

    assert fast_memory.build_biff_fast_memory_snapshot(cfg) == ""
