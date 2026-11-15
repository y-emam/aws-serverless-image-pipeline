"""Write docs/architecture.drawio (open at https://app.diagrams.net or in VS Code
with the Draw.io extension). Uses draw.io's built-in AWS icon set, so the
diagram stays editable by hand.

    python docs/drawio.py

Layout: request path on top (browser -> CloudFront -> API / buckets), event
path underneath (S3 -> SQS -> Lambda -> Step Functions), data stores on the
right, operations along the bottom.
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent / "architecture.drawio"

ICON = 64
COLORS = {
    "compute": "#ED7100", "storage": "#7AA116", "integration": "#E7157B", "database": "#C925D1",
    "network": "#8C4FFF", "ml": "#01A88D", "mgmt": "#E7157B", "cost": "#7AA116",
}

# id: (label, resIcon, category, x, y)  - x/y is the icon's top-left corner
NODES = {
    "user": ("Browser", None, None, 40, 250),
    "cdn": ("CloudFront<br>OAC, 3 behaviors", "cloudfront", "network", 220, 250),
    "web": ("Frontend bucket", "s3", "storage", 420, 90),
    "api": ("HTTP API<br>/api/*, throttled", "api_gateway", "network", 420, 250),
    "presign": ("presign", "lambda", "compute", 620, 180),
    "gallery": ("list_images", "lambda", "compute", 620, 320),

    "source": ("Source bucket<br>uploads/, expire 30 d", "s3", "storage", 220, 520),
    "queue": ("SQS queue<br>visibility 180 s", "sqs", "integration", 420, 520),
    "dlq": ("DLQ<br>after 5 receives", "sqs", "integration", 420, 700),
    "consumer": ("start_workflow<br>SQS poller, batch 10", "lambda", "compute", 620, 520),
    "sfn": ("Step Functions<br>Standard", "step_functions", "integration", 820, 520),

    "validate": ("validate", "lambda", "compute", 1000, 520),
    "thumb": ("resize<br>thumbnail", "lambda", "compute", 1180, 400),
    "full": ("resize full<br>+ watermark", "lambda", "compute", 1180, 520),
    "ai": ("ai_tag", "lambda", "compute", 1180, 640),
    "meta": ("write_metadata", "lambda", "compute", 1360, 520),
    "rek": ("Rekognition<br>DetectLabels", "rekognition", "ml", 1100, 850),
    "bedrock": ("Bedrock<br>Claude Haiku / Nova Lite", "bedrock", "ml", 1260, 850),

    "processed": ("Processed bucket<br>images/, IA after 30 d", "s3", "storage", 1580, 400),
    "table": ("DynamoDB<br>image metadata", "dynamodb", "database", 1580, 520),
    "sns": ("SNS<br>email", "sns", "integration", 1580, 640),

    "cw": ("CloudWatch<br>10 alarms + logs", "cloudwatch_2", "mgmt", 1580, 850),
    "xray": ("X-Ray", "xray", "mgmt", 820, 850),
    "budget": ("AWS Budgets<br>$5 / month", "budgets", "cost", 620, 850),
}

RIGHT_IN = "exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;"


def c(nid):
    """Centre of a node's icon."""
    x, y = NODES[nid][3], NODES[nid][4]
    return x + ICON / 2, y + ICON / 2


# (source, target, label, style, waypoints)
EDGES = [
    # request path
    ("user", "cdn", "HTTPS", RIGHT_IN, []),
    ("cdn", "api", "/api/*", RIGHT_IN, []),
    ("cdn", "web", "/", "exitX=0.5;exitY=0;entryX=0;entryY=0.5;", [(252, 122)]),
    ("cdn", "processed", "/images/*  (OAC)", "exitX=0.5;exitY=0;entryX=0.5;entryY=0;", [(252, 40), (1612, 40)]),
    ("api", "presign", "POST /api/uploads", "exitX=1;exitY=0.5;entryX=0;entryY=0.5;", [(540, 282), (540, 212)]),
    ("api", "gallery", "GET /api/images", "exitX=1;exitY=0.5;entryX=0;entryY=0.5;", [(540, 282), (540, 352)]),
    ("gallery", "table", "Scan", "exitX=1;exitY=0.5;entryX=0;entryY=0.25;dashed=1;", [(1500, 352), (1500, 536)]),
    ("user", "source", "pre-signed POST<br>(direct upload)", "exitX=0;exitY=0.5;entryX=0;entryY=0.5;dashed=1;strokeColor=#2E7D32;fontColor=#2E7D32;", [(20, 282), (20, 552)]),

    # event path
    ("source", "queue", "ObjectCreated", RIGHT_IN, []),
    ("queue", "dlq", "", "exitX=0;exitY=0.85;entryX=0;entryY=0.5;dashed=1;strokeColor=#C62828;", [(400, 574), (400, 732)]),
    ("queue", "consumer", "", RIGHT_IN, []),
    ("consumer", "sfn", "StartExecution", RIGHT_IN, []),
    ("sfn", "validate", "", RIGHT_IN, []),

    # workflow
    ("validate", "thumb", "", "exitX=1;exitY=0.5;entryX=0;entryY=0.5;", [(1110, 552), (1110, 432)]),
    ("validate", "full", "", RIGHT_IN, []),
    ("validate", "ai", "", "exitX=1;exitY=0.5;entryX=0;entryY=0.5;", [(1110, 552), (1110, 672)]),
    ("thumb", "meta", "", "exitX=1;exitY=0.5;entryX=0;entryY=0.5;", [(1290, 432), (1290, 552)]),
    ("full", "meta", "", RIGHT_IN, []),
    ("ai", "meta", "", "exitX=1;exitY=0.5;entryX=0;entryY=0.5;", [(1290, 672), (1290, 552)]),
    ("ai", "rek", "", "exitX=0;exitY=1;entryX=0.5;entryY=0;", [(1180, 800), (1132, 800)]),
    ("ai", "bedrock", "", "exitX=1;exitY=1;entryX=0.5;entryY=0;dashed=1;", [(1244, 800), (1292, 800)]),
    ("thumb", "processed", "thumb.jpg", "exitX=1;exitY=0.25;entryX=0;entryY=0.5;", [(1500, 416), (1500, 432)]),
    ("meta", "table", "COMPLETED", RIGHT_IN, []),
    ("meta", "sns", "notify", "exitX=1;exitY=0.75;entryX=0;entryY=0.5;", [(1460, 568), (1460, 672)]),

    # ops
    ("cw", "sns", "alarms", "exitX=1;exitY=0.5;entryX=1;entryY=0.5;dashed=1;strokeColor=#888888;fontColor=#888888;", [(1740, 882), (1740, 672)]),
]


def node_xml(nid, label, icon, cat, x, y):
    if icon is None:
        style = ("shape=mxgraph.aws4.user;html=1;verticalLabelPosition=bottom;verticalAlign=top;align=center;"
                 "fillColor=#232F3E;strokeColor=none;fontSize=12;")
    else:
        style = (
            "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;strokeColor=#ffffff;dashed=0;"
            "verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=11;aspect=fixed;"
            f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon};fillColor={COLORS[cat]};"
        )
    return (
        f'<mxCell id="{nid}" value="{escape(label, {chr(34): "&quot;"})}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{ICON}" height="{ICON}" as="geometry"/></mxCell>'
    )


def edge_xml(i, src, dst, label, extra, points):
    style = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;fontSize=10;"
             f"labelBackgroundColor=#ffffff;strokeColor=#545B64;{extra}")
    pts = ""
    if points:
        pts = '<Array as="points">' + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in points) + "</Array>"
    return (
        f'<mxCell id="e{i}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}">'
        f'<mxGeometry relative="1" as="geometry">{pts}</mxGeometry></mxCell>'
    )


def box(cid, label, x, y, w, h, color, dashed=True, valign="top"):
    style = (f"rounded=1;arcSize=4;{'dashed=1;' if dashed else ''}fillColor=none;strokeColor={color};"
             f"verticalAlign={valign};spacingBottom=6;align=left;spacingLeft=10;spacingTop=4;fontSize=12;fontStyle=1;fontColor={color};html=1;")
    return (f'<mxCell id="{cid}" value="{escape(label)}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def main():
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    cells.append(
        '<mxCell id="title" value="Serverless Image Processing Pipeline on AWS" '
        'style="text;html=1;fontSize=22;fontStyle=1;fontColor=#232F3E;" vertex="1" parent="1">'
        '<mxGeometry x="40" y="-30" width="700" height="36" as="geometry"/></mxCell>'
    )
    # background boxes first so they sit behind the icons
    cells.append(box("lane_req", "Request path", 190, 70, 560, 350, "#8C4FFF"))
    cells.append(box("lane_evt", "Event path", 190, 480, 760, 320, "#E7157B"))
    cells.append(box("wf", "Step Functions workflow (Parallel: thumbnail | full + watermark | AI)", 970, 370, 500, 380, "#E7157B", dashed=False))
    cells.append(box("data", "Data and notifications", 1550, 370, 180, 440, "#C925D1", valign="bottom"))
    cells.append(box("ops", "Operations", 590, 820, 320, 125, "#545B64"))
    cells += [node_xml(nid, *spec) for nid, spec in NODES.items()]
    cells += [edge_xml(i, *e) for i, e in enumerate(EDGES)]
    xml = (
        '<mxfile host="app.diagrams.net"><diagram name="Architecture" id="arch">'
        '<mxGraphModel dx="1800" dy="1000" grid="1" gridSize="10" page="1" pageWidth="1800" pageHeight="1000" background="#ffffff">'
        f'<root>{"".join(cells)}</root></mxGraphModel></diagram></mxfile>'
    )
    OUT.write_text(xml, encoding="utf-8")
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
