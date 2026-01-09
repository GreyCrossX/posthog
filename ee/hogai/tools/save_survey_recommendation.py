"""
Tool for saving survey recommendations from AI analysis.

This tool allows the AI to save structured survey recommendations
that it generates from analyzing funnels, experiments, and feature flags.
"""

from typing import Literal

from pydantic import BaseModel, Field

from posthog.schema import AssistantTool

from ee.hogai.tool import MaxTool


class SurveyRecommendationAction(BaseModel):
    """The action to take on a recommendation."""

    action: Literal["NEW", "REINFORCE", "UPDATE", "DISMISS"] = Field(
        description="NEW: create new recommendation, REINFORCE: keep existing, UPDATE: modify existing, DISMISS: remove"
    )


class SaveSurveyRecommendationArgs(BaseModel):
    """Arguments for saving a survey recommendation."""

    action: Literal["NEW", "REINFORCE", "UPDATE", "DISMISS"] = Field(
        description="NEW: create new recommendation, REINFORCE: keep existing and increase score, UPDATE: modify existing recommendation, DISMISS: mark recommendation for removal"
    )

    recommendation_type: Literal[
        "LOW_CONVERSION_FUNNEL", "DECLINING_FEATURE", "EXPERIMENT_FEEDBACK", "FEATURE_FLAG_FEEDBACK"
    ] = Field(description="The type of recommendation")

    source_type: Literal["insight", "experiment", "feature_flag"] = Field(description="The type of source object")

    source_id: str = Field(
        description="The identifier of the source: insight short_id, experiment ID, or feature flag key"
    )

    title: str = Field(
        description="A short, descriptive title for this recommendation (e.g., 'Low conversion in Signup Funnel')"
    )

    reason: str = Field(description="Why this survey recommendation is valuable (1-2 sentences)")

    suggested_survey_question: str | None = Field(
        default=None,
        description="The main question the survey should ask users (skip for DISMISS action)",
    )

    score: int = Field(
        ge=0,
        le=100,
        description="Urgency/impact score from 0-100 (higher = more important)",
    )


SAVE_SURVEY_RECOMMENDATION_PROMPT = """
Use this tool to save a survey recommendation based on your analysis.

Call this tool once for each recommendation you want to make. You can make multiple calls to save multiple recommendations.

For the action parameter:
- **NEW**: Create a new recommendation for something not previously recommended
- **REINFORCE**: The existing recommendation is still valid, optionally increase its score
- **UPDATE**: The existing recommendation needs modifications (new reason, score, etc.)
- **DISMISS**: The recommendation is no longer relevant and should be removed

For source_id:
- For insights: use the short_id (e.g., "abc123")
- For experiments: use the experiment name
- For feature flags: use the flag key
""".strip()


class SaveSurveyRecommendationTool(MaxTool):
    name: Literal[AssistantTool.SAVE_SURVEY_RECOMMENDATION] = AssistantTool.SAVE_SURVEY_RECOMMENDATION
    description: str = SAVE_SURVEY_RECOMMENDATION_PROMPT
    args_schema: type[BaseModel] = SaveSurveyRecommendationArgs

    async def _arun_impl(
        self,
        action: str,
        recommendation_type: str,
        source_type: str,
        source_id: str,
        title: str,
        reason: str,
        score: int,
        suggested_survey_question: str | None = None,
    ) -> tuple[str, None]:
        from django.utils import timezone

        from posthog.models import Experiment, FeatureFlag, Insight
        from posthog.models.surveys.survey_recommendation import SurveyRecommendation

        # Map recommendation_type string to enum value
        type_map = {
            "LOW_CONVERSION_FUNNEL": SurveyRecommendation.RecommendationType.LOW_CONVERSION_FUNNEL,
            "DECLINING_FEATURE": SurveyRecommendation.RecommendationType.DECLINING_FEATURE,
            "EXPERIMENT_FEEDBACK": SurveyRecommendation.RecommendationType.EXPERIMENT_FEEDBACK,
            "FEATURE_FLAG_FEEDBACK": SurveyRecommendation.RecommendationType.FEATURE_FLAG_FEEDBACK,
        }
        rec_type = type_map.get(recommendation_type)
        if not rec_type:
            return f"Invalid recommendation_type: {recommendation_type}", None

        # Find the source object
        source_insight = None
        source_experiment = None
        source_feature_flag = None

        if source_type == "insight":
            try:
                source_insight = await Insight.objects.aget(team=self._team, short_id=source_id, deleted=False)
            except Insight.DoesNotExist:
                return f"Insight with short_id '{source_id}' not found", None

        elif source_type == "experiment":
            try:
                source_experiment = await Experiment.objects.aget(team=self._team, name=source_id)
            except Experiment.DoesNotExist:
                # Try by ID if name lookup fails
                try:
                    source_experiment = await Experiment.objects.aget(team=self._team, id=int(source_id))
                except (Experiment.DoesNotExist, ValueError):
                    return f"Experiment '{source_id}' not found", None

        elif source_type == "feature_flag":
            try:
                source_feature_flag = await FeatureFlag.objects.aget(team=self._team, key=source_id, deleted=False)
            except FeatureFlag.DoesNotExist:
                return f"Feature flag with key '{source_id}' not found", None

        else:
            return f"Invalid source_type: {source_type}", None

        # Build display context
        display_context = {
            "title": title,
            "description": reason,
            "source_type": source_type,
            "source_id": source_id,
        }

        # Build survey defaults (basic template for creating a survey)
        survey_defaults = {
            "name": f"Feedback: {title}",
            "type": "popover",
            "questions": [
                {
                    "type": "open",
                    "question": suggested_survey_question or f"What's your experience with this feature?",
                }
            ],
        }

        # Link the insight if this is an insight-based recommendation
        if source_insight:
            survey_defaults["linked_insight_id"] = source_insight.id

        # Handle the action
        if action == "DISMISS":
            # Find and dismiss the existing recommendation
            filter_kwargs = {"team": self._team, "status": SurveyRecommendation.Status.ACTIVE}
            if source_insight:
                filter_kwargs["source_insight"] = source_insight
            elif source_experiment:
                filter_kwargs["source_experiment"] = source_experiment
            elif source_feature_flag:
                filter_kwargs["source_feature_flag"] = source_feature_flag

            updated = await SurveyRecommendation.objects.filter(**filter_kwargs).aupdate(
                status=SurveyRecommendation.Status.DISMISSED,
                dismissed_at=timezone.now(),
            )
            if updated:
                return f"Dismissed recommendation for {source_type} '{source_id}'", None
            return f"No active recommendation found to dismiss for {source_type} '{source_id}'", None

        elif action in ["NEW", "UPDATE", "REINFORCE"]:
            # For UPDATE/REINFORCE, try to find existing recommendation
            existing = None
            if action in ["UPDATE", "REINFORCE"]:
                filter_kwargs = {"team": self._team, "status": SurveyRecommendation.Status.ACTIVE}
                if source_insight:
                    filter_kwargs["source_insight"] = source_insight
                elif source_experiment:
                    filter_kwargs["source_experiment"] = source_experiment
                elif source_feature_flag:
                    filter_kwargs["source_feature_flag"] = source_feature_flag

                try:
                    existing = await SurveyRecommendation.objects.aget(**filter_kwargs)
                except SurveyRecommendation.DoesNotExist:
                    if action == "UPDATE":
                        # Fall back to creating new if update target doesn't exist
                        action = "NEW"
                    elif action == "REINFORCE":
                        return f"No existing recommendation found to reinforce for {source_type} '{source_id}'", None

            if existing:
                # Update existing recommendation
                existing.recommendation_type = rec_type
                existing.display_context = display_context
                existing.survey_defaults = survey_defaults
                if action == "REINFORCE":
                    # Increase score by 10% for reinforcement, capped at 100
                    existing.score = min(100, score + 10)
                else:
                    existing.score = score
                await existing.asave()
                return f"Updated recommendation for {source_type} '{source_id}' (score: {existing.score})", None

            else:
                # Create new recommendation
                recommendation = SurveyRecommendation(
                    team=self._team,
                    recommendation_type=rec_type,
                    survey_defaults=survey_defaults,
                    display_context=display_context,
                    score=score,
                    source_insight=source_insight,
                    source_experiment=source_experiment,
                    source_feature_flag=source_feature_flag,
                )
                await recommendation.asave()
                return f"Created new recommendation for {source_type} '{source_id}' (score: {score})", None

        return f"Invalid action: {action}", None
