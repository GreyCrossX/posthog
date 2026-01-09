from posthog.test.base import BaseTest

from posthog.models import FeatureFlag
from posthog.models.surveys.survey_recommendation import SurveyRecommendation
from posthog.tasks.survey_recommendations.tasks import (
    SURVEY_RECOMMENDATIONS_FEATURE_FLAG,
    cleanup_stale_recommendations,
    is_survey_recommendations_enabled,
)


class TestSurveyRecommendationFeatureFlag(BaseTest):
    def test_returns_false_when_flag_does_not_exist(self):
        self.assertFalse(is_survey_recommendations_enabled(self.team))

    def test_returns_false_when_flag_is_inactive(self):
        FeatureFlag.objects.create(
            team=self.team,
            key=SURVEY_RECOMMENDATIONS_FEATURE_FLAG,
            active=False,
            filters={"groups": [{"rollout_percentage": 100}]},
            created_by=self.user,
        )
        self.assertFalse(is_survey_recommendations_enabled(self.team))

    def test_returns_false_when_rollout_is_zero(self):
        FeatureFlag.objects.create(
            team=self.team,
            key=SURVEY_RECOMMENDATIONS_FEATURE_FLAG,
            active=True,
            filters={"groups": [{"rollout_percentage": 0}]},
            created_by=self.user,
        )
        self.assertFalse(is_survey_recommendations_enabled(self.team))

    def test_returns_true_when_flag_is_active_with_rollout(self):
        FeatureFlag.objects.create(
            team=self.team,
            key=SURVEY_RECOMMENDATIONS_FEATURE_FLAG,
            active=True,
            filters={"groups": [{"rollout_percentage": 100}]},
            created_by=self.user,
        )
        self.assertTrue(is_survey_recommendations_enabled(self.team))


class TestCleanupStaleRecommendations(BaseTest):
    def _create_recommendation(self, **kwargs):
        defaults = {
            "team": self.team,
            "recommendation_type": SurveyRecommendation.RecommendationType.LOW_CONVERSION_FUNNEL,
            "survey_defaults": {"name": "Test"},
            "display_context": {"title": "Test"},
            "score": 50,
            "status": SurveyRecommendation.Status.ACTIVE,
        }
        defaults.update(kwargs)
        return SurveyRecommendation.objects.create(**defaults)

    def test_dismisses_recommendations_with_deleted_insight(self):
        from posthog.models import Insight

        insight = Insight.objects.create(team=self.team, deleted=True)
        rec = self._create_recommendation(source_insight=insight)

        cleanup_stale_recommendations()

        rec.refresh_from_db()
        self.assertEqual(rec.status, SurveyRecommendation.Status.DISMISSED)
        self.assertIsNotNone(rec.dismissed_at)

    def test_dismisses_recommendations_with_deleted_feature_flag(self):
        flag = FeatureFlag.objects.create(
            team=self.team,
            key="test-flag",
            deleted=True,
            created_by=self.user,
        )
        rec = self._create_recommendation(source_feature_flag=flag)

        cleanup_stale_recommendations()

        rec.refresh_from_db()
        self.assertEqual(rec.status, SurveyRecommendation.Status.DISMISSED)

    def test_dismisses_old_unconverted_recommendations(self):
        from datetime import timedelta

        from django.utils import timezone

        rec = self._create_recommendation()
        # Backdate the creation time
        SurveyRecommendation.objects.filter(id=rec.id).update(created_at=timezone.now() - timedelta(days=31))

        cleanup_stale_recommendations()

        rec.refresh_from_db()
        self.assertEqual(rec.status, SurveyRecommendation.Status.DISMISSED)

    def test_keeps_recent_recommendations(self):
        rec = self._create_recommendation()

        cleanup_stale_recommendations()

        rec.refresh_from_db()
        self.assertEqual(rec.status, SurveyRecommendation.Status.ACTIVE)
