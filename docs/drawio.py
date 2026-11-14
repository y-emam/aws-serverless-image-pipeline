"""Write docs/architecture.drawio (open at https://app.diagrams.net or in VS Code
with the Draw.io extension). Uses draw.io's built-in AWS 2021+ icon set, so the
diagram stays editable by hand.

    python docs/drawio.py
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent / "architecture.drawio"

COLORS = {
    "compute": "#ED7100", "storage": "#7AA116", "integration": "#E7157B", "database": "#C925D1",
    "network": "#8C4FFF", "ml": "#01A88D", "mgmt": "#E7157B", "cost": "#7AA116",
}

# id: (label, resIcon, category, x, y)
NODES = {
    "user": ("Browser", None, None, 20, 380),
    "cdn": ("CloudFront<br>(OAC, 3 behaviors)", "cloudfront", "network", 200, 380),
    "web": ("Frontend bucket", "simple_storage_service", "storage", 380, 560),
    "api": ("HTTP API<br>/api/*", "api_gateway", "network", 380, 380),
    "presign": ("presign", "lambda", "compute", 560, 470),
    "gallery": ("list_images", "lambda", "compute", 560, 330),
    "source": ("Source bucket<br>uploads/ (expire 30d)", "simple_storage_service", "storage", 200, 120),
    "queue": ("SQS queue<br>visibility 180 s", "sqs", "integration", 380, 120),
    "dlq": ("DLQ<br>(after 5 receives)", "sqs", "integration", 380, 0),
    "consumer": ("start_workflow<br>(SQS poller, batch 10)", "lambda", "compute", 560, 120),
    "sfn": ("Step Functions<br>Standard", "step_functions", "integration", 740, 120),
    "validate": ("validate", "lambda", "compute", 900, 120),
    "thumb": ("resize<br>(thumbnail)", "lambda", "compute", 1060, 20),
    "full": ("resize (full)<br>-> watermark", "lambda", "compute", 1060, 120),
    "ai": ("ai_tag", "lambda", "compute", 1060, 220),
    "meta": ("write_metadata", "lambda", "compute", 1220, 120),
    "rek": ("Rekognition<br>DetectLabels", "rekognition", "ml", 1000, 360),
    "bedrock": ("Bedrock<br>Claude Haiku (optional)", "bedrock", "ml", 1140, 360),
    "processed": ("Processed bucket<br>images/ (IA after 30d)", "simple_storage_service", "storage", 1380, 20),
    "table": ("DynamoDB<br>image metadata", "dynamodb", "database", 1380, 220),
    "sns": ("SNS<br>email", "sns", "integration", 1380, 380),
    "cw": ("CloudWatch<br>alarms + logs", "cloudwatch_2", "mgmt", 1220, 500),
    "xray": ("X-Ray", "xray", "mgmt", 1060, 500),
    "budget": ("AWS Budgets<br>$5 / month", "budgets", "cost", 900, 500),
}

# (source, target, label, style-extra)
EDGES = [
    ("user", "cdn", "HTTPS", ""),
    ("cdn", "web", "/", ""),
    ("cdn", "api", "/api/*", ""),
    ("cdn", "processed", "/images/*", "curved=1;"),
    ("api", "presign", "", ""),
    ("api", "gallery", "", ""),
    ("gallery", "table", "", "dashed=1;"),
    ("user", "source", "pre-signed POST", "dashed=1;strokeColor=#2E7D32;"),
    ("source", "queue", "ObjectCreated", ""),
    ("queue", "dlq", "", "dashed=1;strokeColor=#C62828;"),
    ("queue", "consumer", "", ""),
    ("consumer", "sfn", "StartExecution", ""),
    ("sfn", "validate", "", ""),
    ("validate", "thumb", "", ""),
    ("validate", "full", "", ""),
    ("validate", "ai", "", ""),
    ("thumb", "meta", "", ""),
    ("full", "meta", "", ""),
    ("ai", "meta", "", ""),
    ("ai", "rek", "", ""),
    ("ai", "bedrock", "", "dashed=1;"),
    ("thumb", "processed", "", ""),
    ("meta", "table", "COMPLETED", ""),
    ("meta", "sns", "notify", ""),
    ("dlq", "cw", "", "dashed=1;strokeColor=#888888;"),
    ("cw", "sns", "", "dashed=1;strokeColor=#888888;"),
]

ICON = 60


def node_xml(nid, label, icon, cat, x, y):
    if icon is None:
        style = "shape=mxgraph.aws4.user;html=1;verticalLabelPosition=bottom;verticalAlign=top;align=center;fillColor=#232F3E;strokeColor=none;"
    else:
        style = (
            "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;strokeColor=#ffffff;dashed=0;"
            "verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=11;aspect=fixed;"
            f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon};fillColor={COLORS[cat]};"
        )
    return (
        f'<mxCell id="{nid}" value="{escape(label, {chr(34): "&quot;"})}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x + 40}" y="{y + 40}" width="{ICON}" height="{ICON}" as="geometry"/></mxCell>'
    )


def edge_xml(i, src, dst, label, extra):
    style = f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;fontSize=10;{extra}"
    return (
        f'<mxCell id="e{i}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}">'
        '<mxGeometry relative="1" as="geometry"/></mxCell>'
    )


def main():
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    cells.append(
        '<mxCell id="title" value="Serverless Image Processing Pipeline" style="text;html=1;fontSize=20;fontStyle=1;" '
        'vertex="1" parent="1"><mxGeometry x="40" y="-20" width="500" height="30" as="geometry"/></mxCell>'
    )
    cells.append(
        '<mxCell id="wf" value="Step Functions workflow (Parallel branches in the middle column)" '
        'style="rounded=1;dashed=1;fillColor=none;strokeColor=#E7157B;verticalAlign=top;align=left;spacingLeft=8;fontSize=11;html=1;" '
        'vertex="1" parent="1"><mxGeometry x="760" y="40" width="580" height="300" as="geometry"/></mxCell>'
    )
    cells += [node_xml(nid, *spec) for nid, spec in NODES.items()]
    cells += [edge_xml(i, *e) for i, e in enumerate(EDGES)]
    xml = (
        '<mxfile host="app.diagrams.net"><diagram name="Architecture" id="arch">'
        '<mxGraphModel dx="1600" dy="900" grid="1" gridSize="10" page="1" pageWidth="1600" pageHeight="800">'
        f'<root>{"".join(cells)}</root></mxGraphModel></diagram></mxfile>'
    )
    OUT.write_text(xml, encoding="utf-8")
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
