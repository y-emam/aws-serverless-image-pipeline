"""Architecture diagram as code (official AWS icons via the `diagrams` library).

    python docs/diagram.py          # needs Graphviz `dot` on PATH
    # or, without installing Graphviz:
    docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c \
      "apt-get update -qq && apt-get install -y -qq graphviz >/dev/null && pip install -q diagrams==0.25.1 && python docs/diagram.py"

Writes docs/architecture.png
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.cost import Budgets
from diagrams.aws.database import Dynamodb
from diagrams.aws.devtools import XRay
from diagrams.aws.integration import SNS, SQS, StepFunctions
from diagrams.aws.management import Cloudwatch
from diagrams.aws.ml import Bedrock, Rekognition
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.storage import S3
from diagrams.onprem.client import User

OUT = Path(__file__).resolve().parent / "architecture"

graph_attr = {"fontsize": "20", "pad": "0.4", "nodesep": "0.55", "ranksep": "0.9", "splines": "spline"}
node_attr = {"fontsize": "11"}

with Diagram(
    "Serverless Image Processing Pipeline",
    filename=str(OUT),
    outformat="png",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    user = User("Browser")

    with Cluster("Edge"):
        cdn = CloudFront("CloudFront\n(OAC, 3 behaviors)")

    with Cluster("API"):
        api = APIGateway("HTTP API\n/api/*")
        presign = Lambda("presign")
        gallery = Lambda("list_images")

    with Cluster("Ingest"):
        source = S3("Source bucket\nuploads/ (expire 30d)")
        queue = SQS("SQS queue")
        dlq = SQS("DLQ")
        consumer = Lambda("start_workflow\n(SQS poller)")

    with Cluster("Step Functions Standard workflow"):
        sfn = StepFunctions("pipeline")
        validate = Lambda("validate")
        with Cluster("Parallel"):
            thumb = Lambda("resize\n(thumbnail)")
            full = Lambda("resize (full)\n-> watermark")
            ai = Lambda("ai_tag")
        meta = Lambda("write_metadata")

    with Cluster("AI services"):
        rek = Rekognition("Rekognition\nDetectLabels")
        bedrock = Bedrock("Bedrock\nClaude Haiku\n(optional)")

    with Cluster("Data"):
        processed = S3("Processed bucket\nimages/ (IA after 30d)")
        web = S3("Frontend bucket")
        table = Dynamodb("DynamoDB\nimage metadata")

    with Cluster("Ops"):
        sns = SNS("SNS email")
        cw = Cloudwatch("Alarms + Logs")
        xray = XRay("X-Ray")
        budget = Budgets("Budget $5")

    # request path
    user >> Edge(label="HTTPS") >> cdn
    cdn >> Edge(label="/") >> web
    cdn >> Edge(label="/images/*") >> processed
    cdn >> Edge(label="/api/*") >> api
    api >> [presign, gallery]
    gallery >> table
    user >> Edge(label="pre-signed POST", style="dashed", color="darkgreen") >> source

    # event path
    source >> Edge(label="ObjectCreated") >> queue
    queue >> Edge(label="after 5 receives", style="dashed", color="firebrick") >> dlq
    queue >> Edge(label="batch 10") >> consumer
    consumer >> Edge(label="StartExecution") >> sfn
    consumer >> Edge(label="RECEIVED", style="dotted") >> table

    # workflow
    sfn >> validate >> [thumb, full, ai]
    [thumb, full, ai] >> meta
    ai >> [rek, bedrock]
    [thumb, full] >> processed
    meta >> table
    sfn >> Edge(label="success / failure", color="darkorange") >> sns
    sfn >> Edge(label="FAILED", style="dotted", color="firebrick") >> table

    # ops
    [dlq, sfn] >> Edge(style="dotted", color="gray") >> cw
    cw >> Edge(style="dotted", color="gray") >> sns
    cw - Edge(style="invis") - xray - Edge(style="invis") - budget
