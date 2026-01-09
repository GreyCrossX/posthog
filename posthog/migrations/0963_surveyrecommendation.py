# Generated manually

import django.db.models.deletion
from django.db import migrations, models

import posthog.models.utils


class Migration(migrations.Migration):
    dependencies = [
        ("posthog", "0962_webanalyticsfilterpreset"),
    ]

    operations = [
        migrations.CreateModel(
            name="SurveyRecommendation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=posthog.models.utils.UUIDT,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "recommendation_type",
                    models.CharField(
                        choices=[
                            ("low_conversion_funnel", "Low conversion funnel"),
                            ("feature_flag_feedback", "Feature flag feedback"),
                            ("experiment_feedback", "Experiment feedback"),
                            ("declining_feature", "Declining feature"),
                        ],
                        max_length=50,
                    ),
                ),
                (
                    "survey_defaults",
                    models.JSONField(help_text="JSON payload that can be used to create a survey via the API"),
                ),
                (
                    "display_context",
                    models.JSONField(help_text="Title, description, and metrics for rendering the recommendation card"),
                ),
                ("score", models.FloatField(default=0.0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("dismissed", "Dismissed"),
                            ("converted", "Converted"),
                        ],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("dismissed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "converted_survey",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="source_recommendations",
                        to="posthog.survey",
                    ),
                ),
                (
                    "source_experiment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="survey_recommendations",
                        to="posthog.experiment",
                    ),
                ),
                (
                    "source_feature_flag",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="survey_recommendations",
                        to="posthog.featureflag",
                    ),
                ),
                (
                    "source_insight",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="survey_recommendations",
                        to="posthog.insight",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="survey_recommendations",
                        to="posthog.team",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="surveyrecommendation",
            index=models.Index(fields=["team", "status"], name="posthog_sur_team_id_8b8d4e_idx"),
        ),
        migrations.AddIndex(
            model_name="surveyrecommendation",
            index=models.Index(
                fields=["team", "recommendation_type"],
                name="posthog_sur_team_id_a1b2c3_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="surveyrecommendation",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_insight__isnull", False), ("status", "active")),
                fields=("team", "source_insight"),
                name="unique_active_insight_recommendation",
            ),
        ),
        migrations.AddConstraint(
            model_name="surveyrecommendation",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_feature_flag__isnull", False), ("status", "active")),
                fields=("team", "source_feature_flag"),
                name="unique_active_flag_recommendation",
            ),
        ),
        migrations.AddConstraint(
            model_name="surveyrecommendation",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_experiment__isnull", False), ("status", "active")),
                fields=("team", "source_experiment"),
                name="unique_active_experiment_recommendation",
            ),
        ),
    ]
