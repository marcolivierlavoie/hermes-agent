# BIF-1527 Discord Gateway Integration / Rollback Notes

## Integration state

The Biff tool-router is integrated with the Discord gateway path behind the existing safe rollout flag:

- Environment flag: `HERMES_BIFF_TOOLSET_ROUTER=1` enables enforcement.
- Config flag: `biff.platforms.discord.toolset_router: true` enables enforcement when the env var is unset.
- Rollback: set `HERMES_BIFF_TOOLSET_ROUTER=0` or set `biff.platforms.discord.toolset_router: false`, then restart the gateway if changing process environment/config for the live service.

## Safety properties verified by tests

- Router disabled preserves the profiled Discord tool surface.
- Router enabled routes short direct replies to no tool surface.
- Router enabled routes quick terminal/tool lookup to the narrow terminal/file lane.
- Biff OS execution prompts use conservative/full fallback rather than under-loading command-room work.
- Explicit memory/writeback prompts preserve memory/session-search/terminal tools.
- Gateway cache signature includes selected per-turn toolsets so narrow and broad tool schemas do not reuse the same cached AIAgent.
- Recall-on-miss ceiling is set before `run_conversation`, widened agents are evicted after recall, and router telemetry records after result/eviction.

## Provider / fallback note

Provider selection and fallback remain owned by the existing runtime provider path. Router telemetry records model/provider labels and outcome for feedback, but router selection does not relax provider safety or alter DeepSeek/OpenAI fallback behavior.

## Production gate

No production restart or enablement is part of this slice. Live rollout should happen only after the focused tests stay green and the operator deliberately enables the flag.
