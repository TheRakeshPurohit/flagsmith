from django.db import models

from features.import_export.constants import (
    FEATURE_EXPORT_STATUSES,
    FEATURE_IMPORT_STATUSES,
    FEATURE_IMPORT_STRATEGIES,
    MAX_FEATURE_EXPORT_SIZE,
    MAX_FEATURE_IMPORT_SIZE,
    PROCESSING,
)


class FeatureExport(models.Model):
    """
    Stores the representation of an environment's export of
    features between the request for the export and the
    ultimate download. Records are deleted automatically after
    a waiting period.
    """

    # The environment the export came from.
    environment = models.ForeignKey(  # type: ignore[call-arg]
        "environments.Environment",
        related_name="feature_imports",
        on_delete=models.CASCADE,
        swappable=True,
    )

    status = models.CharField(
        choices=FEATURE_EXPORT_STATUSES,
        max_length=50,
        blank=False,
        null=False,
        default=PROCESSING,
    )

    # This is a JSON string of data used for file download
    # once the task has completed assembly. It is null on upload.
    data = models.TextField(
        max_length=MAX_FEATURE_EXPORT_SIZE,
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)


class FeatureImport(models.Model):
    """
    Stores the representation of an environment's import of
    features between upload of a previously exported featureset
    and the processing of the import. Records are deleted
    automatically after a waiting period.
    """

    # The environment the features are being imported to.
    environment = models.ForeignKey(  # type: ignore[call-arg]
        "environments.Environment",
        related_name="feature_exports",
        on_delete=models.CASCADE,
        swappable=True,
    )
    strategy = models.CharField(
        choices=FEATURE_IMPORT_STRATEGIES,
        max_length=50,
        blank=False,
        null=False,
    )
    status = models.CharField(
        choices=FEATURE_IMPORT_STATUSES,
        max_length=50,
        blank=False,
        null=False,
        default=PROCESSING,
    )

    # This is a JSON string of data generated by the export.
    data = models.TextField(max_length=MAX_FEATURE_IMPORT_SIZE)
    created_at = models.DateTimeField(auto_now_add=True)


class FlagsmithOnFlagsmithFeatureExport(models.Model):
    """
    This model is internal to Flagsmith in order to support people
    running their own instances of Flagsmith with exports of the
    feature flags that Flagsmith uses to enable or disable
    features of flagsmith. This model should not be considered
    to be useful by third-party developers.
    """

    feature_export = models.ForeignKey(
        FeatureExport,
        related_name="flagsmith_on_flagsmith",
        on_delete=models.CASCADE,
    )

    created_at = models.DateTimeField(auto_now_add=True)
