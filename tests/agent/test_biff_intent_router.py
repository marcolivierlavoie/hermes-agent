from agent.biff_intent_router import plan_biff_turn, route_biff_live_intent


def test_direct_question_routes_answer_now_before_bundle_load():
    route = route_biff_live_intent("What folder do I use for Time Machine?")

    assert route.action == "answer_now"
    assert route.allow_bundle_selection is False
    assert route.max_live_tool_calls == 0


def test_broad_board_work_stays_with_biff_without_explicit_handoff():
    route = route_biff_live_intent("Archive every old Kanban card and scan the whole Obsidian workspace.")

    assert route.action == "route_bundle"
    assert route.allow_bundle_selection is True


def test_empty_discord_turn_preserves_execution_tools_for_rollover_recovery():
    plan = plan_biff_turn("")

    assert plan.action == "route_bundle"
    assert plan.runtime == "continuation"
    assert plan.toolset_profile == "base"
    assert plan.allow_bundle_selection is False


def test_quick_status_routes_one_tool_without_bundle_load():
    route = route_biff_live_intent("Can you check gateway status?")

    assert route.action == "one_tool"
    assert route.allow_bundle_selection is False
    assert route.max_live_tool_calls == 1


def test_plain_status_question_routes_one_tool_without_bundle_load():
    route = route_biff_live_intent("What's the status?")

    assert route.action == "one_tool"
    assert route.allow_bundle_selection is False


def test_kanban_status_check_routes_to_read_only_board_lane():
    plan = plan_biff_turn(
        "Status check only: tell me what K-1346 and K-1347 currently say on the Kanban board."
    )

    assert plan.action == "kanban_status"
    assert plan.runtime == "kanban_read"
    assert plan.toolset_profile == "kanban"
    assert plan.allow_bundle_selection is False
    assert plan.requires_current_info is False


def test_bare_kanban_story_number_routes_to_read_only_board_lane():
    for prompt in ("Check 1503", "show BIF-1503", "what's up with 1503?"):
        plan = plan_biff_turn(prompt)

        assert plan.action == "kanban_status"
        assert plan.runtime == "kanban_read"
        assert plan.toolset_profile == "kanban"


def test_non_kanban_number_question_does_not_route_to_board_lane():
    plan = plan_biff_turn("what is 1503 divided by 3?")

    assert plan.action != "kanban_status"
    assert plan.toolset_profile != "kanban"


def test_keep_story_open_stays_with_biff_without_explicit_handoff():
    plan = plan_biff_turn("Keep K-1348 open until we finish live testing.")

    assert plan.action == "kanban_admin"
    assert plan.runtime == "kanban_admin"
    assert plan.toolset_profile == "kanban"
    assert plan.allow_bundle_selection is False
    assert plan.background is False
    assert plan.specialist is None


def test_create_story_stays_with_biff_not_ranger_or_forge_without_explicit_handoff():
    plan = plan_biff_turn("Create a Kanban story for proper routing and move it to todo.")

    assert plan.action == "kanban_admin"
    assert plan.runtime == "kanban_admin"
    assert plan.toolset_profile == "kanban"
    assert plan.specialist is None


def test_kanban_admin_turns_get_native_board_tool_profile():
    for prompt in (
        "move story K-1503 to todo",
        "move 1503 to todo",
        "close BIF-1503 after verification",
        "Biff needs native kanban admin access",
        "add a story to the board for the runtime fix",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.action == "kanban_admin"
        assert plan.runtime == "kanban_admin"
        assert plan.toolset_profile == "kanban"
        assert plan.allow_bundle_selection is False


def test_explicit_fresh_or_online_lookup_routes_quick_web():
    route = route_biff_live_intent("Can you look up deals online for a standing desk?")

    assert route.action == "quick_web"
    assert route.allow_bundle_selection is False
    assert route.max_live_tool_calls == 3


def test_live_sports_score_routes_quick_web():
    route = route_biff_live_intent("What's the score for the Habs game btw?")

    assert route.action == "quick_web"
    assert route.allow_bundle_selection is False
    assert route.max_live_tool_calls == 3


def test_casual_right_now_without_current_lookup_does_not_route_quick_web():
    for prompt in (
        "Let's fix the router right now.",
        "Stay tactical right now.",
        "Can you help me decide what to do right now?",
        "The runtime and gateway should stay stable right now.",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.action != "quick_web"
        assert plan.runtime != "web_lookup"
        assert plan.toolset_profile != "web"


def test_current_right_now_lookup_still_routes_quick_web():
    for prompt in (
        "What's happening in Montreal right now?",
        "Is Costco open right now near me?",
        "What's the weather right now?",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.action == "quick_web"
        assert plan.runtime == "web_lookup"
        assert plan.requires_current_info is True


def test_bare_url_routes_quick_web_not_answer_now():
    plan = plan_biff_turn(
        "Can you see this thread https://www.reddit.com/r/hermesagent/comments/1tlyfob/best_localfirst_ai_memory_assistant_second_brain/"
    )

    assert plan.action == "quick_web"
    assert plan.toolset_profile == "web"
    assert plan.requires_current_info is True
    assert plan.allow_bundle_selection is False


def test_link_inspection_words_route_quick_web():
    route = route_biff_live_intent("Can you inspect this link and summarize it?")

    assert route.action == "quick_web"
    assert route.allow_bundle_selection is False


def test_vision_requests_route_to_narrow_vision_lane():
    for prompt in (
        "Grant yourself vision analyze",
        "analyze this screenshot",
        "what's in this attachment?",
    ):
        route = plan_biff_turn(prompt)

        assert route.action == "vision_analyze"
        assert route.runtime == "vision_lookup"
        assert route.toolset_profile == "vision"
        assert route.allow_bundle_selection is False


def test_recipe_ideas_without_fresh_lookup_stays_answer_now():
    route = route_biff_live_intent("Quick question: what are three simple dinner ideas with chicken and rice?")

    assert route.action == "answer_now"
    assert route.allow_bundle_selection is False


def test_engineering_workflow_stays_with_biff_without_explicit_handoff():
    plan = plan_biff_turn("Implement the speed story and add tests.")

    assert plan.action == "route_bundle"
    assert plan.allow_bundle_selection is True
    assert plan.background is False
    assert plan.specialist is None


def test_do_phase_work_stays_with_biff_and_is_not_misread_as_direct_question():
    plan = plan_biff_turn("Ok so do phase 2a yourself biff")

    assert plan.action == "route_bundle"
    assert plan.allow_bundle_selection is True
    assert plan.background is False
    assert plan.specialist is None


def test_waiting_for_forge_complaint_is_not_specialist_approval():
    plan = plan_biff_turn("Well I guess now I have to wait for forge to do 2a")

    assert plan.runtime != "specialist_work"
    assert plan.background is False
    assert plan.specialist is None


def test_engineering_question_with_action_stays_with_biff_without_explicit_handoff():
    route = route_biff_live_intent("Can you make the Hermes dashboard into an iOS app?")

    assert route.action == "route_bundle"
    assert route.allow_bundle_selection is True


def test_dashboard_delete_stays_with_biff_without_explicit_handoff():
    route = route_biff_live_intent("Delete Cockpit from the Hermes dashboard sidebar.")

    assert route.action == "route_bundle"
    assert route.allow_bundle_selection is True


def test_short_follow_up_actions_continue_prior_work_context():
    for prompt in (
        "Do it",
        "Do it properly now.",
        "Do 1",
        "Let me know when done",
        "Do what you have to do",
        "Now continue and don't stop until done",
        "finish 1306",
        "COMPLETE 1306",
    ):
        route = route_biff_live_intent(prompt)

        assert route.action == "route_bundle"
        assert route.allow_bundle_selection is True


def test_refresh_resume_routes_to_context_recovery_without_kanban_assumption():
    for prompt in (
        "the chat refreshed again so I can't see your progress",
        "where did we leave off?",
        "pick up the non-Kanban thing",
        "resume",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.action == "resume_context"
        assert plan.runtime == "context_resume"
        assert plan.toolset_profile == "resume"
        assert plan.allow_bundle_selection is False
        assert plan.max_live_tool_calls == 3


def test_action_follow_up_with_extra_question_routes_to_implementation_lane():
    route = route_biff_live_intent("do it. Btw I do see that it hit the OpenAI API. could mini be too weak?")

    assert route.action == "route_bundle"
    assert route.allow_bundle_selection is True


def test_non_engineering_workflow_can_still_route_bundle():
    route = route_biff_live_intent("Think through the corrected preference.")

    assert route.action == "route_bundle"
    assert route.allow_bundle_selection is True


def test_docs_and_memory_work_routes_to_quill():
    plan = plan_biff_turn("Update Mnemosyne and document the corrected routing contract in Obsidian.")

    assert plan.action == "route_bundle"
    assert plan.allow_bundle_selection is True
    assert plan.specialist is None


def test_research_routes_to_quill_not_forge():
    plan = plan_biff_turn("Research the options and write up the recommendation.")

    assert plan.action == "route_bundle"
    assert plan.specialist is None


def test_qa_validation_routes_to_vex_not_forge():
    plan = plan_biff_turn("Have Vex QA the dashboard change and verify the live UI actually works.")

    assert plan.action == "vex_direct"
    assert plan.allow_bundle_selection is False
    assert plan.specialist == "vex"



def test_engineering_reply_fix_followup_stays_with_biff_without_explicit_handoff():
    plan = plan_biff_turn(
        '[Replying to: "Gateway error: NameError: name max_iterations is not defined"]\n\n'
        "what do you suggest we do to fix this"
    )

    assert plan.action == "route_bundle"
    assert plan.allow_bundle_selection is True
    assert plan.background is False
    assert plan.specialist is None


def test_explicit_forge_request_routes_to_named_specialist():
    plan = plan_biff_turn("Use Forge to fix the gateway routing bug.")

    assert plan.action == "forge_direct"
    assert plan.allow_bundle_selection is False
    assert plan.background is True
    assert plan.specialist == "forge"


def test_stop_forge_stays_in_biff_controller_not_forge():
    plan = plan_biff_turn("Can you stop Forge please?")

    assert plan.action == "one_tool"
    assert plan.allow_bundle_selection is False
    assert plan.background is False
    assert plan.specialist is None


def test_review_message_for_forge_does_not_dispatch_to_forge():
    plan = plan_biff_turn(
        "So I have a message for Forge can you read it before sending it to him: "
        "Do not run hermes gateway restart."
    )

    assert plan.action == "answer_now"
    assert plan.allow_bundle_selection is False
    assert plan.background is False
    assert plan.specialist is None


def test_specialist_misroute_feedback_stays_with_biff_controller():
    for prompt in (
        "you sent my question to forge again",
        "why did you send that to Forge?",
        "don't route this to forge",
        "Biff routed my prompt through Vex again",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.action == "answer_now"
        assert plan.runtime == "direct_answer"
        assert plan.allow_bundle_selection is False
        assert plan.background is False
        assert plan.specialist is None


def test_specialist_keyword_mentions_without_assignment_do_not_dispatch():
    for prompt in (
        "I feel like Forge might be overkill here.",
        "Ranger will still be needed if we use the native dispatcher, right?",
        "Quill is probably just documentation, not execution.",
        "Vex verification seems important before done.",
        "Vex should verify this before done.",
        "Ranger should create those cards.",
        "This is for Ranger, not Forge.",
        "The native Kanban dispatcher may have too much access.",
        "The runtime and gateway should stay stable during this change.",
        "The gateway change seems risky.",
        "The runtime change seems risky.",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.runtime != "specialist_work"
        assert plan.background is False
        assert plan.specialist is None


def test_role_keyword_with_clear_assignment_still_dispatches():
    cases = {
        "Ask Forge to fix the gateway routing bug.": "forge",
        "Have Vex verify the live UI actually works.": "vex",
        "Route this to Ranger for board cleanup.": "ranger",
        "Dispatch this to Vex for verification.": "vex",
        "Delegate this to Forge for implementation.": "forge",
        "Send this to Quill for the runbook.": "quill",
    }
    for prompt, expected_role in cases.items():
        plan = plan_biff_turn(prompt)

        assert plan.runtime == "specialist_work"
        assert plan.background is True
        assert plan.specialist == expected_role



def test_restart_done_followup_stays_with_biff_controller_continuation():
    for prompt in (
        "I'll just say restart done",
        "restart done",
        "gateway restart done",
        "I restarted the gateway",
        "gateway restarted",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.action == "route_bundle"
        assert plan.runtime == "continuation"
        assert plan.allow_bundle_selection is True
        assert plan.background is False
        assert plan.specialist is None


def test_ranger_correction_does_not_dispatch_without_explicit_handoff_verb():
    plan = plan_biff_turn("this is a task for ranger, not forge")

    assert plan.action == "route_bundle"
    assert plan.allow_bundle_selection is True
    assert plan.specialist is None


def test_quill_and_vex_corrections_do_not_dispatch_without_explicit_handoff_verb():
    assert plan_biff_turn("this is for quill, not forge").specialist is None
    assert plan_biff_turn("this is for vex, not forge").specialist is None


def test_explicit_moment_coach_triggers_route_to_mental_health_runtime():
    for prompt in (
        "coach me through this",
        "distortion check",
        "help me step back",
        "I am spiraling",
        "I need perspective",
        "mental health check",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.action == "mental_health_coach"
        assert plan.runtime == "mental_health_moment"
        assert plan.toolset_profile == "mental_health"
        assert plan.allow_bundle_selection is False
        assert plan.max_live_tool_calls == 0
        assert plan.background is False
        assert plan.specialist is None
        assert plan.to_dict()["moment_semantics"] == "decline_safe_opt_in"
        assert "mechanism_map" in plan.to_dict()
        assert all("private" not in label.lower() for label in plan.to_dict()["mechanism_map"])


def test_ambient_high_confidence_activation_returns_opt_in_not_forced_coaching():
    plan = plan_biff_turn("I am looping hard, activated, and everything feels urgent right now.")

    assert plan.action == "mental_health_opt_in"
    assert plan.runtime == "mental_health_routing_prompt"
    assert plan.toolset_profile == "mental_health"
    assert plan.allow_bundle_selection is False
    assert plan.max_live_tool_calls == 0
    assert plan.background is False
    assert plan.specialist is None
    assert plan.to_dict()["moment_semantics"] == "opt_in_routing_only"


def test_decline_or_stay_tactical_does_not_route_to_coaching():
    for prompt in (
        "I am spiraling but don't coach me; just help me draft the reply.",
        "mental health check later, stay tactical right now",
        "not coaching, just tell me the next practical step",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.action == "answer_now"
        assert plan.runtime == "direct_answer"
        assert plan.allow_bundle_selection is False
        assert plan.to_dict()["moment_semantics"] == "declined_or_tactical"


def test_daily_ritual_handoff_routes_to_dashboard_ritual_entry():
    for prompt in (
        "start the daily ritual",
        "open the mental health daily ritual",
        "take me to the ritual page",
    ):
        plan = plan_biff_turn(prompt)

        assert plan.action == "ritual_entry"
        assert plan.runtime == "dashboard_ritual_entry"
        assert plan.toolset_profile == "dashboard"
        assert plan.allow_bundle_selection is False
        assert plan.max_live_tool_calls == 0
        assert plan.background is False
        assert plan.specialist is None
        assert plan.to_dict()["dashboard_handoff"] == "BIF-1425 ritual page"
