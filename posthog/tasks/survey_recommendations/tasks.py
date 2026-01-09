"""
Celery tasks for generating survey recommendations.
"""

from django.db.models import Q

import structlog
from celery import shared_task

from posthog.models import Team
from posthog.models.surveys.survey_recommendation import SurveyRecommendation

logger = structlog.get_logger(__name__)

SURVEY_RECOMMENDATIONS_FEATURE_FLAG = "survey-recommendations"


def is_survey_recommendations_enabled(team: Team) -> bool:
    """Check if survey recommendations feature is enabled for a team."""
    from posthog.models.feature_flag import FeatureFlag

    try:
        flag = FeatureFlag.objects.get(
            team=team,
            key=SURVEY_RECOMMENDATIONS_FEATURE_FLAG,
            deleted=False,
        )
        # Check if flag is active and has any rollout
        if not flag.active:
            return False
        groups = flag.filters.get("groups", [])
        return any(g.get("rollout_percentage", 0) > 0 for g in groups) if groups else False
    except FeatureFlag.DoesNotExist:
        return False


@shared_task(ignore_result=True, max_retries=1)
def generate_survey_recommendations_for_team(team_id: int) -> None:
    """
    Generate survey recommendations for a single team.

    This task uses PostHog AI to analyze the team's funnels, experiments,
    and feature flags to identify survey opportunities.
    """
    try:
        team = Team.objects.get(id=team_id)
    except Team.DoesNotExist:
        logger.warning("Team not found for survey recommendations", team_id=team_id)
        return

    # Check feature flag
    if not is_survey_recommendations_enabled(team):
        logger.debug("Survey recommendations not enabled for team", team_id=team_id)
        return

    # Use the team creator or first admin as the user context
    user = team.organization.members.filter(
        organization_membership__level__gte=8  # Admin level
    ).first()

    if not user:
        user = team.created_by

    if not user:
        logger.warning("No user found for survey recommendations", team_id=team_id)
        return

    try:
        from posthog.tasks.survey_recommendations.analyzer import analyze_survey_opportunities

        analysis_result = analyze_survey_opportunities(team, user)

        if analysis_result:
            logger.info(
                "Survey recommendation analysis completed",
                team_id=team_id,
                result_length=len(analysis_result),
            )
        else:
            logger.info("No survey recommendations generated", team_id=team_id)

    except Exception as e:
        logger.exception(
            "Failed to generate survey recommendations",
            team_id=team_id,
            error=str(e),
        )
        raise


@shared_task(ignore_result=True)
def generate_survey_recommendations_for_all_teams() -> None:
    """
    Schedule survey recommendation generation for all active teams.

    This is the periodic task that runs daily to generate recommendations.
    It spawns individual tasks for each team, staggered to avoid overwhelming the AI backend.
    """
    from posthog.caching.utils import active_teams

    team_ids = active_teams()

    logger.info(
        "Scheduling survey recommendations for teams",
        team_count=len(team_ids),
    )

    # Stagger task execution to avoid overwhelming the AI backend
    # Each task is delayed by 30 seconds from the previous one
    for i, team_id in enumerate(team_ids):
        delay_seconds = i * 30  # 30 second intervals
        generate_survey_recommendations_for_team.apply_async(
            args=[team_id],
            countdown=delay_seconds,
        )


@shared_task(ignore_result=True)
def cleanup_stale_recommendations() -> None:
    """
    Clean up stale survey recommendations.

    Marks recommendations as dismissed when:
    - The source insight/experiment/flag has been deleted
    - The recommendation is older than 30 days and never converted
    """
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    cutoff = now - timedelta(days=30)

    # Dismiss recommendations where the source object was deleted
    orphaned_count = (
        SurveyRecommendation.objects.filter(
            status=SurveyRecommendation.Status.ACTIVE,
        )
        .filter(
            # Source insight was deleted
            Q(source_insight__isnull=False, source_insight__deleted=True)
            |
            # Source feature flag was deleted
            Q(source_feature_flag__isnull=False, source_feature_flag__deleted=True)
            |
            # Source experiment's feature flag was deleted (experiments don't have deleted field)
            Q(source_experiment__isnull=False, source_experiment__feature_flag__deleted=True)
        )
        .update(
            status=SurveyRecommendation.Status.DISMISSED,
            dismissed_at=now,
        )
    )

    if orphaned_count:
        logger.info("Dismissed orphaned survey recommendations", count=orphaned_count)

    # Mark old unconverted recommendations as dismissed
    stale_count = SurveyRecommendation.objects.filter(
        status=SurveyRecommendation.Status.ACTIVE,
        created_at__lt=cutoff,
    ).update(
        status=SurveyRecommendation.Status.DISMISSED,
        dismissed_at=now,
    )

    if stale_count:
        logger.info("Dismissed stale survey recommendations", count=stale_count)
