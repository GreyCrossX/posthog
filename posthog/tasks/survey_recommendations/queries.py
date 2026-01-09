"""
Queries for identifying survey recommendation candidates.

This module provides functions to find insights, experiments, and feature flags
that are actively used and could benefit from user surveys. The actual analysis
of whether a survey would be valuable is delegated to PostHog AI.
"""

from datetime import timedelta
from typing import TypedDict

from django.db.models import Count, Q
from django.utils import timezone

import structlog

from posthog.models import Experiment, FeatureFlag, Insight, Team

logger = structlog.get_logger(__name__)


class InsightCandidate(TypedDict):
    insight_id: int
    insight_name: str
    short_id: str
    view_count: int
    unique_viewers: int
    insight_type: str  # 'funnel', 'trend', 'retention', etc.


class ExperimentCandidate(TypedDict):
    experiment_id: int
    experiment_name: str
    feature_flag_key: str
    start_date: str | None
    end_date: str | None
    is_complete: bool


class FeatureFlagCandidate(TypedDict):
    flag_id: int
    flag_key: str
    flag_name: str
    rollout_percentage: int | None
    is_fully_rolled_out: bool


def get_insight_type(query: dict) -> str:
    """Extract the insight type from a query dict."""
    source = query.get("source", query)
    kind = source.get("kind", "")

    type_map = {
        "FunnelsQuery": "funnel",
        "TrendsQuery": "trend",
        "RetentionQuery": "retention",
        "PathsQuery": "paths",
        "StickinessQuery": "stickiness",
        "LifecycleQuery": "lifecycle",
    }

    return type_map.get(kind, "unknown")


def get_most_viewed_funnels(
    team: Team,
    days: int = 30,
    limit: int = 20,
) -> list[InsightCandidate]:
    """
    Find the most-viewed funnel insights for a team.

    Args:
        team: The team to query for
        days: Number of days to look back (default: 30)
        limit: Maximum number of results (default: 20)

    Returns:
        List of funnel insight candidates, ordered by view_count descending
    """
    cutoff_date = timezone.now() - timedelta(days=days)

    funnel_insights = (
        Insight.objects.filter(
            team=team,
            deleted=False,
            saved=True,
        )
        .filter(Q(query__kind="FunnelsQuery") | Q(query__source__kind="FunnelsQuery") | Q(filters__insight="FUNNELS"))
        .filter(
            insightviewed__last_viewed_at__gte=cutoff_date,
            insightviewed__team=team,
        )
        .annotate(
            view_count=Count("insightviewed", filter=Q(insightviewed__last_viewed_at__gte=cutoff_date)),
            unique_viewers=Count(
                "insightviewed__user",
                filter=Q(insightviewed__last_viewed_at__gte=cutoff_date),
                distinct=True,
            ),
        )
        .order_by("-view_count")[:limit]
    )

    return [
        {
            "insight_id": insight.id,
            "insight_name": insight.name or insight.derived_name or "Untitled",
            "short_id": insight.short_id,
            "view_count": insight.view_count,
            "unique_viewers": insight.unique_viewers,
            "insight_type": "funnel",
        }
        for insight in funnel_insights
    ]


def get_most_viewed_trends(
    team: Team,
    days: int = 30,
    limit: int = 20,
) -> list[InsightCandidate]:
    """
    Find the most-viewed trend insights for a team.

    Args:
        team: The team to query for
        days: Number of days to look back (default: 30)
        limit: Maximum number of results (default: 20)

    Returns:
        List of trend insight candidates, ordered by view_count descending
    """
    cutoff_date = timezone.now() - timedelta(days=days)

    trend_insights = (
        Insight.objects.filter(
            team=team,
            deleted=False,
            saved=True,
        )
        .filter(Q(query__kind="TrendsQuery") | Q(query__source__kind="TrendsQuery") | Q(filters__insight="TRENDS"))
        .filter(
            insightviewed__last_viewed_at__gte=cutoff_date,
            insightviewed__team=team,
        )
        .annotate(
            view_count=Count("insightviewed", filter=Q(insightviewed__last_viewed_at__gte=cutoff_date)),
            unique_viewers=Count(
                "insightviewed__user",
                filter=Q(insightviewed__last_viewed_at__gte=cutoff_date),
                distinct=True,
            ),
        )
        .order_by("-view_count")[:limit]
    )

    return [
        {
            "insight_id": insight.id,
            "insight_name": insight.name or insight.derived_name or "Untitled",
            "short_id": insight.short_id,
            "view_count": insight.view_count,
            "unique_viewers": insight.unique_viewers,
            "insight_type": "trend",
        }
        for insight in trend_insights
    ]


def get_recently_concluded_experiments(
    team: Team,
    days: int = 60,
    limit: int = 10,
) -> list[ExperimentCandidate]:
    """
    Find experiments that concluded in the last N days.

    These are good candidates for follow-up surveys to understand
    why users behaved differently in each variant.

    Args:
        team: The team to query for
        days: Number of days to look back (default: 60)
        limit: Maximum number of results (default: 10)

    Returns:
        List of recently concluded experiments
    """
    cutoff_date = timezone.now() - timedelta(days=days)

    experiments = (
        Experiment.objects.filter(
            team=team,
            end_date__gte=cutoff_date,
            end_date__lte=timezone.now(),
        )
        .select_related("feature_flag")
        .order_by("-end_date")[:limit]
    )

    return [
        {
            "experiment_id": exp.id,
            "experiment_name": exp.name,
            "feature_flag_key": exp.feature_flag.key if exp.feature_flag else "",
            "start_date": exp.start_date.isoformat() if exp.start_date else None,
            "end_date": exp.end_date.isoformat() if exp.end_date else None,
            "is_complete": True,
        }
        for exp in experiments
    ]


def get_running_experiments(
    team: Team,
    limit: int = 10,
) -> list[ExperimentCandidate]:
    """
    Find experiments that are currently running.

    Args:
        team: The team to query for
        limit: Maximum number of results (default: 10)

    Returns:
        List of running experiments
    """
    now = timezone.now()

    experiments = (
        Experiment.objects.filter(
            team=team,
            start_date__lte=now,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gt=now))
        .select_related("feature_flag")
        .order_by("-start_date")[:limit]
    )

    return [
        {
            "experiment_id": exp.id,
            "experiment_name": exp.name,
            "feature_flag_key": exp.feature_flag.key if exp.feature_flag else "",
            "start_date": exp.start_date.isoformat() if exp.start_date else None,
            "end_date": exp.end_date.isoformat() if exp.end_date else None,
            "is_complete": False,
        }
        for exp in experiments
    ]


def get_recently_rolled_out_flags(
    team: Team,
    days: int = 30,
    limit: int = 10,
) -> list[FeatureFlagCandidate]:
    """
    Find feature flags that were recently rolled out to 100%.

    These are good candidates for surveys to understand user reactions
    to new features.

    Args:
        team: The team to query for
        days: Number of days to look back (default: 30)
        limit: Maximum number of results (default: 10)

    Returns:
        List of recently rolled out feature flags
    """
    cutoff_date = timezone.now() - timedelta(days=days)

    flags = (
        FeatureFlag.objects.filter(
            team=team,
            deleted=False,
            active=True,
        )
        .filter(
            # Flags modified recently that are fully rolled out
            updated_at__gte=cutoff_date,
            filters__groups__0__rollout_percentage=100,
        )
        .exclude(
            # Exclude flags used for experiments
            experiments__isnull=False,
        )
        .order_by("-updated_at")[:limit]
    )

    results: list[FeatureFlagCandidate] = []
    for flag in flags:
        rollout = None
        if flag.filters and "groups" in flag.filters and flag.filters["groups"]:
            first_group = flag.filters["groups"][0]
            rollout = first_group.get("rollout_percentage")

        results.append(
            {
                "flag_id": flag.id,
                "flag_key": flag.key,
                "flag_name": flag.name or flag.key,
                "rollout_percentage": rollout,
                "is_fully_rolled_out": rollout == 100,
            }
        )

    return results


def get_survey_recommendation_candidates(team: Team) -> dict:
    """
    Gather all candidates for survey recommendations.

    This returns a summary of funnels, experiments, and feature flags
    that are good candidates for survey recommendations. The actual
    analysis is done by PostHog AI.

    Args:
        team: The team to query for

    Returns:
        Dict with categorized candidates
    """
    return {
        "most_viewed_funnels": get_most_viewed_funnels(team, days=30, limit=10),
        "most_viewed_trends": get_most_viewed_trends(team, days=30, limit=10),
        "concluded_experiments": get_recently_concluded_experiments(team, days=60, limit=5),
        "running_experiments": get_running_experiments(team, limit=5),
        "rolled_out_flags": get_recently_rolled_out_flags(team, days=30, limit=5),
    }
