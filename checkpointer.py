# DynamoDB-backed checkpoint persistence for the LangGraph app.
# Self-provisions the table on import so no manual console/CLI step is needed.
import boto3
from botocore.exceptions import ClientError
from langgraph_checkpoint_aws import DynamoDBSaver
from config import AWS_REGION, DYNAMODB_TABLE_NAME


def ensure_table_exists(table_name: str, region: str):
    """Creates the checkpoint table if it doesn't already exist. Matches the schema
    AWS's own DynamoDBSaver docs specify: composite key PK (thread_id) / SK
    (checkpoint_id), pay-per-request billing (no capacity planning needed)."""
    ddb = boto3.client("dynamodb", region_name=region)
    try:
        ddb.describe_table(TableName=table_name)
        return  # table already exists, nothing to do
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise  # some other AWS error — don't swallow it

    ddb.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    # Wait for the table to become active before the graph tries to use it.
    ddb.get_waiter("table_exists").wait(TableName=table_name)


# Run once at import time — self-heals on first execution, no-op on every run after.

checkpointer = DynamoDBSaver(
    table_name=DYNAMODB_TABLE_NAME,
    region_name=AWS_REGION,
)
