from typing import Any

import pytest
from unittest.mock import patch

from autoevals.llm import LLMClassifier
from braintrust import EvalCase, Score
from langchain_core.runnables import RunnableConfig

from posthog.schema import AssistantMessage, AssistantToolCallMessage, HumanMessage

from ee.hogai.chat_agent import AssistantGraph
from ee.hogai.django_checkpoint.checkpointer import DjangoCheckpointer
from ee.hogai.eval.base import MaxPublicEval
from ee.hogai.utils.types import AssistantNodeName, AssistantState
from ee.models.assistant import Conversation


class DashboardOperationAccuracy(LLMClassifier):
    """Binary LLM judge for full agent dashboard operations (tests trajectory)."""

    def _normalize(self, output: dict | None, expected: dict | None) -> tuple[dict, dict]:
        """Ensure all keys exist with defaults to avoid Mustache errors."""
        normalized_output = {
            "tool_called": None,
            "action": None,
            "tool_output": None,
            "error": None,
            **(output or {}),
        }
        normalized_expected = {
            "action": None,
            "insight_titles": None,
            "error": None,
            **(expected or {}),
        }
        return normalized_output, normalized_expected

    async def _run_eval_async(self, output: dict | None, expected: dict | None = None, **kwargs):
        if not output:
            return Score(name=self._name(), score=0.0, metadata={"reason": "No output provided"})
        normalized_output, normalized_expected = self._normalize(output, expected)
        return await super()._run_eval_async(normalized_output, normalized_expected, **kwargs)

    def _run_eval_sync(self, output: dict | None, expected: dict | None = None, **kwargs):
        if not output:
            return Score(name=self._name(), score=0.0, metadata={"reason": "No output provided"})
        normalized_output, normalized_expected = self._normalize(output, expected)
        return super()._run_eval_sync(normalized_output, normalized_expected, **kwargs)

    def __init__(self, **kwargs):
        super().__init__(
            name="dashboard_operation_accuracy",
            prompt_template="""
Evaluate if the agent correctly performed the dashboard operation.

<user_request>
{{input}}
</user_request>

<expected>
Action: {{expected.action}}
Expected insight titles (by meaning): {{expected.insight_titles}}
Expected error: {{expected.error}}
</expected>

<actual_output>
Tool called: {{output.tool_called}}
Action: {{output.action}}
Tool output: {{output.tool_output}}
Error: {{output.error}}
</actual_output>

Evaluate:
1. Did the agent call the upsert_dashboard tool?
2. Was the correct action (create/update) chosen?
3. Does the tool output confirm a dashboard was created/updated?
4. Do the insight titles in the tool output match the expected ones BY MEANING? Titles don't need to be exact - they should be semantically equivalent (e.g. "File activity" matches "File interactions", "User journey funnel" matches "Homepage view to signup conversion"). If expected is null/None, skip this check.
5. If error expected, was it returned?

Choose: pass (all requirements met) or fail (any requirement not met)
""".strip(),
            choice_scores={"pass": 1.0, "fail": 0.0},
            model="gpt-5.2",
            max_tokens=2048,
            reasoning_effort="medium",
            **kwargs,
        )


@pytest.fixture
def call_agent_for_dashboard(demo_org_team_user):
    """Run full agent graph with natural language dashboard requests."""
    with (
        patch(
            "ee.hogai.core.agent_modes.presets.product_analytics.has_upsert_dashboard_feature_flag", return_value=True
        ),
        patch("ee.hogai.core.agent_modes.presets.product_analytics.has_agent_modes_feature_flag", return_value=True),
    ):
        _, team, user = demo_org_team_user

        graph = (
            AssistantGraph(team, user)
            .add_edge(AssistantNodeName.START, AssistantNodeName.ROOT)
            .add_root()
            .compile(checkpointer=DjangoCheckpointer())
        )

        async def callable(prompt: str) -> dict:
            conversation = await Conversation.objects.acreate(team=team, user=user)
            initial_state = AssistantState(messages=[HumanMessage(content=prompt)])
            config = RunnableConfig(configurable={"thread_id": conversation.id}, recursion_limit=48)
            raw_state = await graph.ainvoke(initial_state, config)
            state = AssistantState.model_validate(raw_state)

            return _extract_dashboard_result(state)

        yield callable


def _extract_dashboard_result(state: AssistantState) -> dict:
    """Extract dashboard operation result from final state."""
    result: dict[str, Any] = {
        "tool_called": None,
        "action": None,
        "tool_output": None,
        "error": None,
    }

    upsert_tool_call_id: str | None = None

    # Check for tool calls in messages
    for msg in state.messages:
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            for tool_call in msg.tool_calls:
                if tool_call.name == "upsert_dashboard":
                    result["tool_called"] = "upsert_dashboard"
                    result["action"] = tool_call.args.get("action", {}).get("action")
                    upsert_tool_call_id = tool_call.id
                    break

    # Find the tool call result message
    for msg in state.messages:
        if isinstance(msg, AssistantToolCallMessage) and msg.tool_call_id == upsert_tool_call_id:
            result["tool_output"] = msg.content
            if "error" in msg.content.lower() or "failed" in msg.content.lower():
                result["error"] = msg.content
            break

    return result


@pytest.mark.django_db
async def eval_create_dashboard(call_agent_for_dashboard, pytestconfig):
    """Test dashboard creation via full agent with natural language prompts."""

    await MaxPublicEval(
        experiment_name="upsert_dashboard_create",
        task=call_agent_for_dashboard,
        scores=[DashboardOperationAccuracy()],
        data=[
            EvalCase(
                input="I want a dashboard to track user journeys from homepage to signup",
                expected={
                    "action": "create",
                    "insight_titles": ["Homepage view to signup conversion", "User paths starting at homepage"],
                },
            ),
            EvalCase(
                input="Put together a dashboard for file activity metrics",
                expected={
                    "action": "create",
                    "insight_titles": ["File interactions"],
                },
            ),
            EvalCase(
                input="Create a dashboard showing how users navigate the site",
                expected={
                    "action": "create",
                    "insight_titles": ["User paths starting at homepage"],
                },
            ),
        ],
        pytestconfig=pytestconfig,
    )


@pytest.mark.django_db
async def eval_update_dashboard(call_agent_for_dashboard, pytestconfig):
    """Test dashboard updates via full agent with natural language prompts."""

    await MaxPublicEval(
        experiment_name="upsert_dashboard_update",
        task=call_agent_for_dashboard,
        scores=[DashboardOperationAccuracy()],
        data=[
            EvalCase(
                input="Add file stats to my website dashboard",
                expected={
                    "action": "update",
                    "insight_titles": ["File interactions"],
                },
            ),
            EvalCase(
                input="The key metrics dashboard needs a better name, something like 'Website Metrics'",
                expected={
                    "action": "update",
                },
            ),
            EvalCase(
                input="I want to see file activity on the website dashboard",
                expected={
                    "action": "update",
                    "insight_titles": ["File interactions"],
                },
            ),
        ],
        pytestconfig=pytestconfig,
    )
