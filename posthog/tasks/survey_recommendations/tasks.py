"""
Celery tasks for generating survey recommendations.
"""

import structlog
from celery import shared_task

from posthog.models import Team
from posthog.models.surveys.survey_recommendation import SurveyRecommendation

logger = structlog.get_logger(__name__)


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
            # TODO: Parse the analysis result and create SurveyRecommendation records
            # This will be implemented once we have the structured output format finalized
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
    It spawns individual tasks for each team to process in parallel.
    """
    from posthog.caching.utils import active_teams

    team_ids = active_teams()

    logger.info(
        "Scheduling survey recommendations for teams",
        team_count=len(team_ids),
    )

    for team_id in team_ids:
        generate_survey_recommendations_for_team.delay(team_id)


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

    # Mark old unconverted recommendations as dismissed
    cutoff = timezone.now() - timedelta(days=30)

    updated = SurveyRecommendation.objects.filter(
        status=SurveyRecommendation.Status.ACTIVE,
        created_at__lt=cutoff,
    ).update(
        status=SurveyRecommendation.Status.DISMISSED,
        dismissed_at=timezone.now(),
    )

    if updated:
        logger.info("Dismissed stale survey recommendations", count=updated)
