"""Table definition: one table, generic keys and two global secondary indexes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from battle_royale import keys

if TYPE_CHECKING:
    from types_boto3_dynamodb import DynamoDBClient


def create_table(client: DynamoDBClient, table_name: str = keys.DEFAULT_TABLE) -> bool:
    """Creates the table and waits until it is active.

    Returns:
        ``False`` when the table already existed.
    """
    try:
        client.create_table(
            TableName=table_name,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "map", "AttributeType": "S"},
                {"AttributeName": "open_timestamp", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": keys.OPEN_GAMES_INDEX,
                    "KeySchema": [
                        {"AttributeName": "map", "KeyType": "HASH"},
                        {"AttributeName": "open_timestamp", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": keys.INVERTED_INDEX,
                    "KeySchema": [
                        {"AttributeName": "SK", "KeyType": "HASH"},
                        {"AttributeName": "PK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ResourceInUseException":
            return False
        raise
    client.get_waiter("table_exists").wait(TableName=table_name)

    return True


def delete_table(client: DynamoDBClient, table_name: str = keys.DEFAULT_TABLE) -> bool:
    """Deletes the table and waits until it is gone.

    Returns:
        ``False`` when there was no table.
    """
    try:
        client.delete_table(TableName=table_name)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return False
        raise
    client.get_waiter("table_not_exists").wait(TableName=table_name)

    return True
