from agent.biff_intent_router import plan_biff_turn, route_biff_live_intent


def test_direct_question_routes_answer_now_before_bundle_load():
    route = route_biff_live_intent("What folder do I use for Time Machine?")

    assert route.action == "answer_now"
    assert route.allow_bundle_selection is False
    assert route.max_live_tool_calls == 0


def test_broad_board_work_routes_ranger_before_bundle_load():
    route = route_biff_live_intent("Archive every old Linear story and scan the whole Obsidian workspace.")

    assert route.action == "ranger_direct"
    assert route.allow_bundle_selection is False


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


def test_keep_story_open_routes_to_ranger_board_admin_lane():
    plan = plan_biff_turn("Keep K-1348 open until we finish live testing.")

    assert plan.action == "ranger_direct"
    assert plan.runtime == "specialist_work"
    assert plan.toolset_profile == "specialist"
    assert plan.allow_bundle_selection is False
    assert plan.background is True
    assert plan.specialist == "ranger"


def test_create_story_routes_to_ranger_not_forge():
    plan = plan_biff_turn("Create a Kanban story for proper routing and move it to todo.")

    assert plan.action == "ranger_direct"
    assert plan.runtime == "specialist_work"
    assert plan.specialist == "ranger"


def test_explicit_fresh_or_online_lookup_routes_quick_web():
    route = route_biff_live_intent("Can you look up deals online for a standing desk?")

    assert route.action == "quick_web"
    assert route.allow_bundle_selection is False
    assert route.max_live_tool_calls == 3


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


def test_recipe_ideas_without_fresh_lookup_stays_answer_now():
    route = route_biff_live_intent("Quick question: what are three simple dinner ideas with chicken and rice?")

    assert route.action == "answer_now"
    assert route.allow_bundle_selection is False


def test_engineering_workflow_routes_to_forge_direct_lane():
    plan = plan_biff_turn("Implement the speed story and add tests.")

    assert plan.action == "forge_direct"
    assert plan.allow_bundle_selection is False
    assert plan.max_live_tool_calls == 2
    assert plan.background is True
    assert plan.specialist == "forge"


def test_engineering_question_with_action_routes_to_forge_direct_lane():
    route = route_biff_live_intent("Can you make the Hermes dashboard into an iOS app?")

    assert route.action == "forge_direct"
    assert route.allow_bundle_selection is False


def test_dashboard_delete_routes_to_forge_direct_lane():
    route = route_biff_live_intent("Delete Cockpit from the Hermes dashboard sidebar.")

    assert route.action == "forge_direct"
    assert route.allow_bundle_selection is False


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



def test_engineering_reply_fix_followup_routes_to_forge_direct_lane():
    plan = plan_biff_turn(
        '[Replying to: "Gateway error: NameError: name max_iterations is not defined"]\n\n'
        "what do you suggest we do to fix this"
    )

    assert plan.action == "forge_direct"
    assert plan.allow_bundle_selection is False
    assert plan.background is True
    assert plan.specialist == "forge"


def test_explicit_forge_request_routes_to_named_specialist():
    plan = plan_biff_turn("Use Forge to fix the gateway routing bug.")

    assert plan.action == "forge_direct"
    assert plan.allow_bundle_selection is False
    assert plan.background is True
    assert plan.specialist == "forge"

def test_explicit_ranger_correction_does_not_route_to_forge_direct_lane():
    plan = plan_biff_turn("this is a task for ranger, not forge")

    assert plan.action == "ranger_direct"
    assert plan.allow_bundle_selection is False
    assert plan.specialist == "ranger"


def test_explicit_quill_and_vex_corrections_route_to_named_specialist():
    assert plan_biff_turn("this is for quill, not forge").specialist == "quill"
    assert plan_biff_turn("this is for vex, not forge").specialist == "vex"
