import logging
import typing
from contextlib import suppress
from decimal import Decimal
from typing import Iterable

from boto3.dynamodb.conditions import Attr, Key
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from flag_engine.context.mappers import map_environment_identity_to_context
from flag_engine.environments.models import EnvironmentModel
from flag_engine.identities.models import IdentityModel
from flag_engine.segments.evaluator import get_context_segments
from rest_framework.exceptions import NotFound

from edge_api.identities.search import EdgeIdentitySearchData
from environments.dynamodb.constants import IDENTITIES_PAGINATION_LIMIT
from environments.dynamodb.wrappers.exceptions import CapacityBudgetExceeded
from util.mappers import map_identity_to_identity_document

from .base import BaseDynamoWrapper
from .environment_wrapper import DynamoEnvironmentWrapper

if typing.TYPE_CHECKING:
    from boto3.dynamodb.conditions import ConditionBase
    from mypy_boto3_dynamodb.type_defs import (
        QueryInputRequestTypeDef,
        QueryOutputTableTypeDef,
        TableAttributeValueTypeDef,
    )

    from environments.identities.models import Identity

logger = logging.getLogger()


class DynamoIdentityWrapper(BaseDynamoWrapper):
    def get_table_name(self) -> str | None:  # type: ignore[override]
        return settings.IDENTITIES_TABLE_NAME_DYNAMO

    def query_items(self, *args, **kwargs) -> "QueryOutputTableTypeDef":  # type: ignore[no-untyped-def]
        return self.table.query(*args, **kwargs)  # type: ignore[union-attr]

    def put_item(self, identity_dict: dict):  # type: ignore[type-arg,no-untyped-def]
        self.table.put_item(Item=identity_dict)  # type: ignore[union-attr]

    def write_identities(self, identities: Iterable["Identity"]):  # type: ignore[no-untyped-def]
        with self.table.batch_writer() as batch:  # type: ignore[union-attr]
            for identity in identities:
                identity_document = map_identity_to_identity_document(identity)
                # Since sort keys can not be greater than 1024
                # https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ServiceQuotas.html#limits-partition-sort-keys
                if len(identity_document["identifier"]) > 1024:  # type: ignore[arg-type]
                    logger.warning(
                        f"Can't migrate identity {identity.id}; identifier too long"
                    )
                    continue
                batch.put_item(Item=identity_document)

    def get_item(self, composite_key: str) -> typing.Optional[dict]:  # type: ignore[type-arg]
        return self.table.get_item(Key={"composite_key": composite_key}).get("Item")  # type: ignore[union-attr]

    def delete_item(self, composite_key: str):  # type: ignore[no-untyped-def]
        self.table.delete_item(Key={"composite_key": composite_key})  # type: ignore[union-attr]

    def delete_all_identities(self, environment_api_key: str):  # type: ignore[no-untyped-def]
        with self.table.batch_writer() as writer:  # type: ignore[union-attr]
            for item in self.iter_all_items_paginated(
                environment_api_key=environment_api_key,
                projection_expression="composite_key",
            ):
                writer.delete_item(Key={"composite_key": item["composite_key"]})

    def get_item_from_uuid(self, uuid: str) -> dict:  # type: ignore[type-arg]
        filter_expression = Key("identity_uuid").eq(uuid)
        query_kwargs = {
            "IndexName": "identity_uuid-index",
            "Limit": 1,
            "KeyConditionExpression": filter_expression,
        }
        try:
            return self.query_items(**query_kwargs)["Items"][0]
        except IndexError:
            raise ObjectDoesNotExist()

    def get_item_from_uuid_or_404(self, uuid: str) -> dict:  # type: ignore[type-arg]
        try:
            return self.get_item_from_uuid(uuid)
        except ObjectDoesNotExist as e:
            raise NotFound() from e

    def get_all_items(
        self,
        environment_api_key: str,
        limit: int,
        start_key: dict[str, "TableAttributeValueTypeDef"] | None = None,
        filter_expression: "ConditionBase | str | None" = None,
        projection_expression: str | None = None,
        return_consumed_capacity: bool = False,
    ) -> "QueryOutputTableTypeDef":
        key_condition_expression = Key("environment_api_key").eq(environment_api_key)
        query_kwargs: "QueryInputRequestTypeDef" = {  # type: ignore[typeddict-item]
            "IndexName": "environment_api_key-identifier-index",
            "KeyConditionExpression": key_condition_expression,  # type: ignore[typeddict-item]
            "Limit": limit,
        }
        if start_key:
            query_kwargs["ExclusiveStartKey"] = start_key
        if filter_expression:
            query_kwargs["FilterExpression"] = filter_expression  # type: ignore[typeddict-item]
        if projection_expression:
            query_kwargs["ProjectionExpression"] = projection_expression
        if return_consumed_capacity:
            # Use `TOTAL` because we don't need per-index/per-table consumed capacity
            query_kwargs["ReturnConsumedCapacity"] = "TOTAL"
        return self.query_items(**query_kwargs)

    def iter_all_items_paginated(
        self,
        environment_api_key: str,
        limit: int = IDENTITIES_PAGINATION_LIMIT,
        projection_expression: str | None = None,
        capacity_budget: Decimal = Decimal("Inf"),
        overrides_only: bool = False,
    ) -> typing.Generator[dict, None, None]:  # type: ignore[type-arg]
        last_evaluated_key = "initial"
        get_all_items_kwargs = {
            "environment_api_key": environment_api_key,
            "limit": limit,
            "projection_expression": projection_expression,
            "return_consumed_capacity": capacity_budget != Decimal("Inf"),
        }
        if overrides_only:
            get_all_items_kwargs["filter_expression"] = Attr("identity_features").ne([])
        capacity_spent = 0
        while last_evaluated_key:
            if capacity_spent >= capacity_budget:
                raise CapacityBudgetExceeded(
                    capacity_budget=capacity_budget,
                    capacity_spent=capacity_spent,  # type: ignore[arg-type]
                )
            query_response = self.get_all_items(
                **get_all_items_kwargs,  # type: ignore[arg-type]
            )
            with suppress(KeyError):
                capacity_spent += query_response["ConsumedCapacity"]["CapacityUnits"]  # type: ignore[assignment]
            for item in query_response["Items"]:
                yield item
            if last_evaluated_key := query_response.get("LastEvaluatedKey"):  # type: ignore[assignment]
                get_all_items_kwargs["start_key"] = last_evaluated_key

    def search_items(
        self,
        environment_api_key: str,
        search_data: EdgeIdentitySearchData,
        limit: int,
        start_key: dict = None,  # type: ignore[type-arg,assignment]
    ) -> "QueryOutputTableTypeDef":
        partition_key_search_expression = Key("environment_api_key").eq(
            environment_api_key
        )
        sort_key_search_expression = getattr(
            Key(search_data.search_attribute), search_data.dynamo_search_method
        )(search_data.search_term)

        query_kwargs = {
            "IndexName": search_data.dynamo_index_name,
            "Limit": limit,
            "KeyConditionExpression": partition_key_search_expression
            & sort_key_search_expression,
        }
        if start_key:
            query_kwargs.update(ExclusiveStartKey=start_key)

        return self.query_items(**query_kwargs)

    def get_segment_ids(
        self,
        identity_pk: str = None,  # type: ignore[assignment]
        identity_model: IdentityModel = None,  # type: ignore[assignment]
    ) -> list:  # type: ignore[type-arg]
        if not (identity_pk or identity_model):
            raise ValueError("Must provide one of identity_pk or identity_model.")

        with suppress(ObjectDoesNotExist):
            identity = identity_model or IdentityModel.model_validate(
                self.get_item_from_uuid(identity_pk)
            )
            environment_wrapper = DynamoEnvironmentWrapper()
            environment = EnvironmentModel.model_validate(
                environment_wrapper.get_item(identity.environment_api_key)
            )
            context = map_environment_identity_to_context(
                environment=environment,
                identity=identity,
                override_traits=None,
            )
            segments = get_context_segments(context, environment.project.segments)
            return [segment.id for segment in segments]

        return []

    def get_identity_overrides_count(self, environment_api_key: str) -> int:
        return sum(
            len({f["feature_id"] for f in identity["identity_features"]})
            for identity in self.iter_all_items_paginated(
                environment_api_key=environment_api_key,
                overrides_only=True,
            )
        )

    def get_identity_override_feature_counts(
        self, environment_api_key: str
    ) -> dict[int, int]:
        feature_to_identity_count: dict[int, int] = {}

        for identity in self.iter_all_items_paginated(
            environment_api_key=environment_api_key,
            overrides_only=True,
        ):
            unique_feature_ids: set[int] = set()

            for feature_override in identity.get("identity_features", []):
                feature_id = feature_override.get("feature", {}).get("id", 0)
                unique_feature_ids.add(feature_id)

            for feature_id in unique_feature_ids:
                feature_to_identity_count[feature_id] = (
                    feature_to_identity_count.get(feature_id, 0) + 1
                )

        return feature_to_identity_count
