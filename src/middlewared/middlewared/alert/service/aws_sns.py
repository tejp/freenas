import boto3

from middlewared.alert.base import *
from middlewared.schema import Dict, Str


class AWSSNSAlertService(ThreadedAlertService):
    title = "AWS SNS"

    schema = Dict(
        "awssns_attributes",
        Str("region"),
        Str("topic_arn"),
        Str("aws_access_key_id"),
        Str("aws_secret_access_key"),
    )

    async def send_sync(self, alerts, gone_alerts, new_alerts):
        client = boto3.client(
            "sns",
            region=self.attributes["region"],
            aws_access_key_id=self.attributes["aws_access_key_id"],
            aws_secret_access_key=self.attributes["aws_secret_access_key"],
        )

        if alerts:
            client.publish(
                TopicArn=self.attributes["topic_arn"],
                Subject="Alerts",
                Message=format_alerts(alerts, gone_alerts, new_alerts),
            )
