from posthog.test.base import BaseTest

from asgiref.sync import async_to_sync

from posthog.models import Experiment, FeatureFlag, Insight
from posthog.models.surveys.survey_recommendation import SurveyRecommendation

from ee.hogai.tools.save_survey_recommendation import SaveSurveyRecommendationTool


class TestSaveSurveyRecommendationTool(BaseTest):
    def setUp(self):
        super().setUp()
        self.tool = SaveSurveyRecommendationTool(
            team=self.team,
            user=self.user,
        )

    def _run_tool(self, **kwargs):
        defaults = {
            "action": "NEW",
            "recommendation_type": "LOW_CONVERSION_FUNNEL",
            "source_type": "insight",
            "source_id": "test123",
            "title": "Test Recommendation",
            "reason": "Test reason",
            "score": 50,
        }
        defaults.update(kwargs)
        return async_to_sync(self.tool._arun_impl)(**defaults)

    def test_creates_new_recommendation_for_insight(self):
        insight = Insight.objects.create(team=self.team, short_id="test123")

        result, _ = self._run_tool(source_id=insight.short_id)

        self.assertIn("Created new recommendation", result)
        rec = SurveyRecommendation.objects.get(source_insight=insight)
        self.assertEqual(rec.score, 50)
        self.assertEqual(rec.status, SurveyRecommendation.Status.ACTIVE)

    def test_returns_error_for_nonexistent_insight(self):
        result, _ = self._run_tool(source_id="nonexistent")

        self.assertIn("not found", result)
        self.assertEqual(SurveyRecommendation.objects.count(), 0)

    def test_reinforce_increases_existing_score(self):
        insight = Insight.objects.create(team=self.team, short_id="test123")
        SurveyRecommendation.objects.create(
            team=self.team,
            source_insight=insight,
            recommendation_type=SurveyRecommendation.RecommendationType.LOW_CONVERSION_FUNNEL,
            survey_defaults={},
            display_context={},
            score=50,
            status=SurveyRecommendation.Status.ACTIVE,
        )

        result, _ = self._run_tool(action="REINFORCE", source_id=insight.short_id)

        self.assertIn("Reinforced", result)
        rec = SurveyRecommendation.objects.get(source_insight=insight)
        self.assertEqual(rec.score, 60)  # 50 + 10

    def test_reinforce_caps_score_at_100(self):
        insight = Insight.objects.create(team=self.team, short_id="test123")
        SurveyRecommendation.objects.create(
            team=self.team,
            source_insight=insight,
            recommendation_type=SurveyRecommendation.RecommendationType.LOW_CONVERSION_FUNNEL,
            survey_defaults={},
            display_context={},
            score=95,
            status=SurveyRecommendation.Status.ACTIVE,
        )

        result, _ = self._run_tool(action="REINFORCE", source_id=insight.short_id)

        rec = SurveyRecommendation.objects.get(source_insight=insight)
        self.assertEqual(rec.score, 100)  # Capped at 100

    def test_dismiss_marks_recommendation_as_dismissed(self):
        insight = Insight.objects.create(team=self.team, short_id="test123")
        SurveyRecommendation.objects.create(
            team=self.team,
            source_insight=insight,
            recommendation_type=SurveyRecommendation.RecommendationType.LOW_CONVERSION_FUNNEL,
            survey_defaults={},
            display_context={},
            score=50,
            status=SurveyRecommendation.Status.ACTIVE,
        )

        result, _ = self._run_tool(action="DISMISS", source_id=insight.short_id)

        self.assertIn("Dismissed", result)
        rec = SurveyRecommendation.objects.get(source_insight=insight)
        self.assertEqual(rec.status, SurveyRecommendation.Status.DISMISSED)
        self.assertIsNotNone(rec.dismissed_at)

    def test_update_falls_back_to_new_if_not_exists(self):
        insight = Insight.objects.create(team=self.team, short_id="test123")

        result, _ = self._run_tool(action="UPDATE", source_id=insight.short_id)

        self.assertIn("Created new recommendation", result)
        self.assertEqual(SurveyRecommendation.objects.count(), 1)

    def test_creates_recommendation_for_feature_flag(self):
        flag = FeatureFlag.objects.create(
            team=self.team,
            key="test-flag",
            created_by=self.user,
        )

        result, _ = self._run_tool(
            source_type="feature_flag",
            source_id=flag.key,
            recommendation_type="FEATURE_FLAG_FEEDBACK",
        )

        self.assertIn("Created new recommendation", result)
        rec = SurveyRecommendation.objects.get(source_feature_flag=flag)
        self.assertEqual(rec.recommendation_type, SurveyRecommendation.RecommendationType.FEATURE_FLAG_FEEDBACK)

    def test_creates_recommendation_for_experiment(self):
        flag = FeatureFlag.objects.create(
            team=self.team,
            key="exp-flag",
            created_by=self.user,
        )
        experiment = Experiment.objects.create(
            team=self.team,
            name="Test Experiment",
            feature_flag=flag,
            created_by=self.user,
        )

        result, _ = self._run_tool(
            source_type="experiment",
            source_id=experiment.name,
            recommendation_type="EXPERIMENT_FEEDBACK",
        )

        self.assertIn("Created new recommendation", result)
        rec = SurveyRecommendation.objects.get(source_experiment=experiment)
        self.assertEqual(rec.recommendation_type, SurveyRecommendation.RecommendationType.EXPERIMENT_FEEDBACK)
