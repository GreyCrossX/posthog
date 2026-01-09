"""
Survey recommendation analyzer using PostHog AI.

This module uses the same ChatAgentRunner that powers Max AI to analyze
funnels, experiments, and feature flags for survey recommendation opportunities.
"""

import uuid

import structlog
from asgiref.sync import async_to_sync

from posthog.schema import AgentMode, AssistantMessage, AssistantTool, HumanMessage

from posthog.models import Team, User
from posthog.tasks.survey_recommendations.queries import get_survey_recommendation_candidates

# Import the tool to trigger registration in CONTEXTUAL_TOOL_NAME_TO_TOOL
import ee.hogai.tools.save_survey_recommendation  # noqa: F401
from ee.hogai.chat_agent.runner import ChatAgentRunner
from ee.models import Conversation

logger = structlog.get_logger(__name__)


SURVEY_RECOMMENDATION_PROMPT = """
I need you to analyze the following insights, experiments, and feature flags to identify opportunities for user surveys.

## Existing Active Recommendations
{existing_recommendations_section}

## Most Viewed Funnels (last 30 days)
{funnels_section}

## Most Viewed Trends (last 30 days)
{trends_section}

## Recently Concluded Experiments (last 60 days)
{concluded_experiments_section}

## Currently Running Experiments
{running_experiments_section}

## Recently Rolled Out Feature Flags (100% rollout)
{rolled_out_flags_section}

## Your Task

For each item above, use the read_data tool to:
1. For funnels: Check conversion rates. Flag funnels with <50% overall conversion as survey candidates.
2. For trends: Look for declining patterns that might indicate user dissatisfaction.
3. For concluded experiments: Identify experiments where understanding user sentiment would be valuable.
4. For running experiments: Suggest in-app surveys to gather qualitative feedback during the experiment.
5. For feature flags: Suggest feedback surveys for recently launched features.

When reviewing existing recommendations:
- **REINFORCE**: If the underlying data still supports the recommendation, keep it and optionally increase its score
- **DISMISS**: If the source insight/experiment/flag no longer warrants a survey (e.g., conversion improved, experiment removed), mark it for dismissal
- **UPDATE**: If the recommendation is still valid but the context has changed, update the reason and score

## Saving Recommendations

**IMPORTANT**: For each recommendation you want to create, update, or dismiss, you MUST use the `save_survey_recommendation` tool.
Call the tool once for each recommendation. Do not just describe your recommendations - actually save them using the tool.

Focus on the top 5 most impactful NEW recommendations, plus any updates to existing ones.
"""


def _format_funnels_section(funnels: list[dict]) -> str:
    if not funnels:
        return "No recently viewed funnels found."

    lines = []
    for f in funnels:
        lines.append(
            f"- **{f['insight_name']}** (ID: {f['short_id']}) - {f['view_count']} views by {f['unique_viewers']} users"
        )
    return "\n".join(lines)


def _format_trends_section(trends: list[dict]) -> str:
    if not trends:
        return "No recently viewed trends found."

    lines = []
    for t in trends:
        lines.append(
            f"- **{t['insight_name']}** (ID: {t['short_id']}) - {t['view_count']} views by {t['unique_viewers']} users"
        )
    return "\n".join(lines)


def _format_experiments_section(experiments: list[dict]) -> str:
    if not experiments:
        return "No experiments found."

    lines = []
    for e in experiments:
        status = "Completed" if e.get("is_complete") else "Running"
        lines.append(f"- **{e['experiment_name']}** (Flag: {e['feature_flag_key']}) - {status}")
    return "\n".join(lines)


def _format_flags_section(flags: list[dict]) -> str:
    if not flags:
        return "No recently rolled out flags found."

    lines = []
    for f in flags:
        lines.append(f"- **{f['flag_name']}** (Key: {f['flag_key']}) - 100% rollout")
    return "\n".join(lines)


async def _format_existing_recommendations_async(team: Team) -> str:
    from posthog.models.surveys.survey_recommendation import SurveyRecommendation

    recommendations = SurveyRecommendation.objects.filter(
        team=team,
        status=SurveyRecommendation.Status.ACTIVE,
    ).select_related("source_insight", "source_feature_flag", "source_experiment")

    if not await recommendations.aexists():
        return "No existing recommendations."

    lines = []
    async for rec in recommendations:
        source = "Unknown"
        if rec.source_insight:
            source = f"Insight: {rec.source_insight.short_id}"
        elif rec.source_feature_flag:
            source = f"Flag: {rec.source_feature_flag.key}"
        elif rec.source_experiment:
            source = f"Experiment: {rec.source_experiment.name}"

        display = rec.display_context or {}
        title = display.get("title", rec.recommendation_type)
        reason = display.get("description", "")

        lines.append(
            f"- **{title}** ({rec.recommendation_type}, Score: {rec.score})\n  Source: {source}\n  Reason: {reason}"
        )

    return "\n".join(lines)


async def build_analysis_prompt_async(team: Team) -> str:
    """Build the prompt for PostHog AI to analyze survey opportunities."""
    candidates = get_survey_recommendation_candidates(team)
    existing_recs = await _format_existing_recommendations_async(team)

    return SURVEY_RECOMMENDATION_PROMPT.format(
        existing_recommendations_section=existing_recs,
        funnels_section=_format_funnels_section(candidates["most_viewed_funnels"]),
        trends_section=_format_trends_section(candidates["most_viewed_trends"]),
        concluded_experiments_section=_format_experiments_section(candidates["concluded_experiments"]),
        running_experiments_section=_format_experiments_section(candidates["running_experiments"]),
        rolled_out_flags_section=_format_flags_section(candidates["rolled_out_flags"]),
    )


async def analyze_survey_opportunities_async(team: Team, user: User) -> str | None:
    """
    Use PostHog AI to analyze survey opportunities for a team.

    This creates an internal conversation and uses the same ChatAgentRunner
    that powers Max AI to analyze funnels, experiments, and flags.

    Args:
        team: The team to analyze
        user: The user context for the analysis (typically team creator or system user)

    Returns:
        The AI's analysis as a string, or None if analysis failed
    """
    prompt = await build_analysis_prompt_async(team)

    # Create an internal conversation for this analysis
    conversation = await Conversation.objects.acreate(
        team=team,
        user=user,
        type=Conversation.Type.ASSISTANT,
        is_internal=True,  # Mark as internal so it doesn't show in user's conversation list
    )

    try:
        message = HumanMessage(content=prompt, id=str(uuid.uuid4()))

        # Pass save_survey_recommendation as a contextual tool so the AI can use it
        # This tool is internal-only and not available in normal Max AI conversations
        contextual_tools = {AssistantTool.SAVE_SURVEY_RECOMMENDATION.value: {}}

        runner = ChatAgentRunner(
            team=team,
            conversation=conversation,
            new_message=message,
            user=user,
            is_new_conversation=True,
            agent_mode=AgentMode.PRODUCT_ANALYTICS,  # Start in product analytics mode for insight analysis
            use_checkpointer=False,  # Don't persist checkpoints for background analysis
            is_agent_billable=False,  # Don't bill for internal analysis
            contextual_tools=contextual_tools,  # Make save_survey_recommendation tool available
        )

        final_response: str | None = None

        async for _event_type, message in runner.astream(
            stream_message_chunks=False,
            stream_subgraphs=False,
            stream_first_message=False,
            stream_only_assistant_messages=True,
        ):
            if isinstance(message, AssistantMessage) and message.content:
                final_response = message.content

        return final_response

    except Exception as e:
        logger.exception(
            "Failed to analyze survey opportunities",
            team_id=team.id,
            error=str(e),
        )
        return None

    finally:
        # Clean up the internal conversation
        await conversation.adelete()


def analyze_survey_opportunities(team: Team, user: User) -> str | None:
    """
    Synchronous wrapper for analyze_survey_opportunities_async.

    Use this from Celery tasks or other sync contexts.
    """
    return async_to_sync(analyze_survey_opportunities_async)(team, user)
