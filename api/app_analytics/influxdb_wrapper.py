import json
import logging
import typing
from collections import defaultdict
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.exceptions import InfluxDBError
from influxdb_client.client.flux_table import FluxTable
from influxdb_client.client.write_api import SYNCHRONOUS
from sentry_sdk import capture_exception
from urllib3 import Retry
from urllib3.exceptions import HTTPError

from app_analytics.constants import LABELS
from app_analytics.dataclasses import FeatureEvaluationData, UsageData
from app_analytics.mappers import (
    map_flux_tables_to_feature_evaluation_data,
    map_flux_tables_to_usage_data,
)
from app_analytics.types import Labels

logger = logging.getLogger(__name__)

url = settings.INFLUXDB_URL
token = settings.INFLUXDB_TOKEN
influx_org = settings.INFLUXDB_ORG
read_bucket = settings.INFLUXDB_BUCKET + "_downsampled_15m"

retries = Retry(connect=3, read=3, redirect=3)
# Set a timeout to prevent threads being potentially stuck open due to network weirdness
influxdb_client = InfluxDBClient(
    url=url, token=token, org=influx_org, retries=retries, timeout=3000
)

DEFAULT_DROP_COLUMNS = (
    "organisation",
    "organisation_id",
    "type",
    "project",
    "project_id",
    "environment",
    "environment_id",
    "host",
)

GET_MULTIPLE_EVENTS_LIST_GROUP_CLAUSE = (
    f"|> group(columns: {json.dumps(['resource', *LABELS])}) "
)


def get_range_bucket_mappings(date_start: datetime) -> str:
    now = timezone.now()
    if (now - date_start).days > 10:
        return settings.INFLUXDB_BUCKET + "_downsampled_1h"
    return settings.INFLUXDB_BUCKET + "_downsampled_15m"


class InfluxDBWrapper:
    def __init__(self, name):  # type: ignore[no-untyped-def]
        self.name = name
        self.records = []
        self.write_api = influxdb_client.write_api(write_options=SYNCHRONOUS)

    def add_data_point(
        self,
        field_name: str,
        field_value: str | int | float,
        tags: typing.Mapping[
            str,
            str | int | float,
        ]
        | None = None,
    ) -> None:
        point = Point(self.name)
        point.field(field_name, field_value)

        if tags is not None:
            for tag_key, tag_value in tags.items():
                point = point.tag(tag_key, tag_value)

        self.records.append(point)

    def write(self) -> None:
        try:
            self.write_api.write(bucket=settings.INFLUXDB_BUCKET, record=self.records)
        except (HTTPError, InfluxDBError) as e:
            logger.warning(
                "Failed to write records to Influx: %s",
                str(e),
                exc_info=e,
            )
            logger.debug(
                "Records: %s. Bucket: %s",
                self.records,
                settings.INFLUXDB_BUCKET,
            )

    @staticmethod
    def influx_query_manager(
        date_start: datetime | None = None,
        date_stop: datetime | None = None,
        drop_columns: tuple[str, ...] = DEFAULT_DROP_COLUMNS,
        filters: str = "|> filter(fn:(r) => r._measurement == 'api_call')",
        extra: str = "",
        bucket: str = read_bucket,
    ) -> list[FluxTable]:
        now = timezone.now()
        if date_start is None:
            date_start = now - timedelta(days=30)

        if date_stop is None:
            date_stop = now

        # Influx throws an error for an empty range, so just return a list.
        if date_start == date_stop:
            return []

        query_api = influxdb_client.query_api()
        drop_columns_input = str(list(drop_columns)).replace("'", '"')

        query = (
            f'from(bucket:"{bucket}")'
            f" |> range(start: {date_start.isoformat()}, stop: {date_stop.isoformat()})"
            f" {filters}"
            f" |> drop(columns: {drop_columns_input}) "
            f"{extra}"
        )
        logger.debug("Running query in influx: \n\n %s", query)

        try:
            return query_api.query(org=influx_org, query=query)
        except HTTPError as e:
            capture_exception(e)
            return []


def get_events_for_organisation(
    organisation_id: id,  # type: ignore[valid-type]
    date_start: datetime | None = None,
    date_stop: datetime | None = None,
) -> int:
    """
    Query influx db for usage for given organisation id

    :param organisation_id: an id of the organisation to get usage for
    :return: a number of request counts for organisation
    """
    now = timezone.now()
    if date_start is None:
        date_start = now - timedelta(days=30)

    if date_stop is None:
        date_stop = now

    result = InfluxDBWrapper.influx_query_manager(
        filters=build_filter_string(
            [
                'r._measurement == "api_call"',
                'r["_field"] == "request_count"',
                f'r["organisation_id"] == "{organisation_id}"',
            ]
        ),
        drop_columns=(
            "organisation",
            "project",
            "project_id",
            "environment",
            "environment_id",
        ),
        extra="|> sum()",
        date_start=date_start,
        date_stop=date_stop,
    )

    total = 0
    for table in result:
        for record in table.records:
            total += record.get_value()

    return total


def get_event_list_for_organisation(
    organisation_id: int,
    date_start: datetime | None = None,
    date_stop: datetime | None = None,
) -> tuple[dict[str, list[int]], list[str]]:
    """
    Query influx db for usage for given organisation id

    :param organisation_id: an id of the organisation to get usage for

    :return: a number of request counts for organisation in chart.js scheme
    """
    now = timezone.now()
    if date_start is None:
        date_start = now - timedelta(days=30)

    if date_stop is None:
        date_stop = now

    results = InfluxDBWrapper.influx_query_manager(
        filters=(
            '|> filter(fn:(r) => r._measurement == "api_call") '
            f'|> filter(fn: (r) => r["organisation_id"] == "{organisation_id}")'
        ),
        extra='|> aggregateWindow(every: 24h, fn: sum, timeSrc: "_start")',
        date_start=date_start,
        date_stop=date_stop,
    )
    dataset = defaultdict(list)
    labels = []  # type: ignore[var-annotated]

    date_difference = date_stop - date_start
    required_records = date_difference.days + 1
    for result in results:
        for record in result.records:
            dataset[record["resource"]].append(record["_value"])
            if len(labels) != required_records:
                labels.append(record.values["_time"].strftime("%Y-%m-%d"))
    return dataset, labels


def get_multiple_event_list_for_organisation(
    organisation_id: int,
    project_id: int | None = None,
    environment_id: int | None = None,
    date_start: datetime | None = None,
    date_stop: datetime | None = None,
    labels_filter: Labels | None = None,
) -> list[UsageData]:
    """
    Query influx db for usage for given organisation id

    :param organisation_id: an id of the organisation to get usage for
    :param project_id: optionally filter by project id
    :param environment_id: optionally filter by an environment id

    :return: a number of requests for flags, traits, identities, environment-document
    """
    now = timezone.now()
    if date_start is None:
        date_start = now - timedelta(days=30)

    if date_stop is None:
        date_stop = now

    filters = [
        'r._measurement == "api_call"',
        f'r["organisation_id"] == "{organisation_id}"',
    ]

    if project_id:
        filters.append(f'r["project_id"] == "{project_id}"')

    if environment_id:
        filters.append(f'r["environment_id"] == "{environment_id}"')

    if labels_filter:
        filters += [f'r["{key}"] == "{value}"' for key, value in labels_filter.items()]

    results = InfluxDBWrapper.influx_query_manager(
        date_start=date_start,
        date_stop=date_stop,
        filters=build_filter_string(filters),
        extra=(
            GET_MULTIPLE_EVENTS_LIST_GROUP_CLAUSE
            + '|> aggregateWindow(every: 24h, fn: sum, timeSrc: "_start")'
        ),
    )

    return map_flux_tables_to_usage_data(results)


def get_usage_data(
    organisation_id: int,
    project_id: int | None = None,
    environment_id: int | None = None,
    date_start: datetime | None = None,
    date_stop: datetime | None = None,
    labels_filter: Labels | None = None,
) -> list[UsageData]:
    now = timezone.now()
    if date_start is None:
        date_start = now - timedelta(days=30)

    if date_stop is None:
        date_stop = now

    return get_multiple_event_list_for_organisation(
        organisation_id=organisation_id,
        project_id=project_id,
        environment_id=environment_id,
        date_start=date_start,
        date_stop=date_stop,
        labels_filter=labels_filter,
    )


def get_multiple_event_list_for_feature(
    environment_id: int,
    feature_name: str,
    date_start: datetime | None = None,
    aggregate_every: str = "24h",
    labels_filter: Labels | None = None,
) -> list[FeatureEvaluationData]:
    """
    Get aggregated request data for the given feature in a given environment across
    all time, aggregated into time windows of length defined by the period argument.

    :param environment_id: an id of the environment to get usage for
    :param feature_name: the name of the feature to get usage for
    :param date_start: the influx datetime period to filter on
    :param aggregate_every: the influx time period to aggregate the data by, e.g. 24h

    :return: a list of dicts with feature and request count in a specific environment
    """
    now = timezone.now()
    if date_start is None:
        date_start = now - timedelta(days=30)

    filters = (
        '|> filter(fn:(r) => r._measurement == "feature_evaluation") '
        '|> filter(fn: (r) => r["_field"] == "request_count") '
        f'|> filter(fn: (r) => r["environment_id"] == "{environment_id}") '
        f'|> filter(fn: (r) => r["feature_id"] == "{feature_name}")'
    )

    if labels_filter:
        filters += " " + build_filter_string(
            [f'r["{key}"] == "{value}"' for key, value in labels_filter.items()]
        )

    results = InfluxDBWrapper.influx_query_manager(
        date_start=date_start,
        filters=filters,
        extra=(
            f"|> group(columns: {json.dumps(LABELS)}) "
            f'|> aggregateWindow(every: {aggregate_every}, fn: sum, createEmpty: false, timeSrc: "_start") '
            '|> yield(name: "sum")'
        ),
    )

    return map_flux_tables_to_feature_evaluation_data(results)


def get_feature_evaluation_data(
    feature_name: str,
    environment_id: int,
    period_days: int = 30,
    labels_filter: Labels | None = None,
) -> list[FeatureEvaluationData]:
    date_start = timezone.now() - timedelta(days=period_days)
    return get_multiple_event_list_for_feature(
        feature_name=feature_name,
        environment_id=environment_id,
        date_start=date_start,
        labels_filter=labels_filter,
    )


def get_top_organisations(
    date_start: datetime | None = None, limit: str = ""
) -> dict[int, int]:
    """
    Query influx db top used organisations

    :param date_start: Start of the date range for top organisations
    :param limit: limit for query


    :return: top organisations in descending order based on api calls.
    """
    now = timezone.now()
    if date_start is None:
        date_start = now - timedelta(days=30)

    if limit:
        limit = f"|> limit(n:{limit})"

    bucket = get_range_bucket_mappings(date_start)
    results = InfluxDBWrapper.influx_query_manager(
        date_start=date_start,
        bucket=bucket,
        filters='|> filter(fn:(r) => r._measurement == "api_call") \
                    |> filter(fn: (r) => r["_field"] == "request_count")',
        drop_columns=("_start", "_stop", "_time"),
        extra='|> group(columns: ["organisation"]) \
              |> sum() \
              |> group() \
              |> sort(columns: ["_value"], desc: true) '
        + limit,
    )

    dataset = {}

    for result in results:
        for record in result.records:
            try:
                org_id = int(record.values["organisation"].partition("-")[0])
                dataset[org_id] = record.get_value()
            except ValueError:
                logger.warning(
                    "Bad InfluxDB data found with organisation %s"
                    % record.values["organisation"].partition("-")[0]
                )

    return dataset


def get_current_api_usage(
    organisation_id: int,
    date_start: datetime,
) -> int:
    """
    Query influx db for api usage

    :param organisation_id: filtered organisation
    :param date_range: data range for current api usage window

    :return: number of current api calls
    """
    bucket = read_bucket
    results = InfluxDBWrapper.influx_query_manager(
        date_start=date_start,
        bucket=bucket,
        filters=build_filter_string(
            [
                'r._measurement == "api_call"',
                'r["_field"] == "request_count"',
                f'r["organisation_id"] == "{organisation_id}"',
            ]
        ),
        drop_columns=("_start", "_stop", "_time"),
        extra='|> sum() \
               |> group() \
               |> sort(columns: ["_value"], desc: true) ',
    )

    for result in results:
        # Return zero if there are no API calls recorded.
        if len(result.records) == 0:
            return 0

        return sum(r.get_value() for r in result.records)

    return 0


def build_filter_string(filter_expressions: typing.List[str]) -> str:
    return "|> ".join(
        ["", *[f"filter(fn: (r) => {exp})" for exp in filter_expressions]]
    )
