"""Draw the Step Functions workflow straight from infra/statemachine/pipeline.asl.json,
so the picture can never drift from the deployed definition.

    python docs/workflow_diagram.py           # needs the `graphviz` Python package and Graphviz `dot`
    docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c \
      "apt-get update -qq && apt-get install -y -qq graphviz >/dev/null && pip install -q graphviz && python docs/workflow_diagram.py"

Writes docs/workflow.png
"""

import json
import re
from pathlib import Path

import graphviz

ROOT = Path(__file__).resolve().parent.parent
ASL = ROOT / "infra" / "statemachine" / "pipeline.asl.json"
OUT = ROOT / "docs" / "workflow"

STYLE = {
    "Task": {"shape": "box", "style": "rounded,filled", "fillcolor": "#FFF1E0", "color": "#ED7100"},
    "Choice": {"shape": "diamond", "style": "filled", "fillcolor": "#EEF2FF", "color": "#5B5BF0"},
    "Parallel": {"shape": "box", "style": "rounded,dashed", "color": "#E7157B"},
    "Succeed": {"shape": "oval", "style": "filled", "fillcolor": "#E3F6EA", "color": "#138A4A"},
    "Fail": {"shape": "oval", "style": "filled", "fillcolor": "#FDE8E8", "color": "#C62828"},
}

SERVICE = {
    "lambda:invoke": "Lambda",
    "sns:publish": "SNS publish",
    "dynamodb:updateItem": "DynamoDB UpdateItem",
}


def label(name, state):
    sub = ""
    if state["Type"] == "Task":
        res = state["Resource"].rsplit(":::", 1)[-1]
        sub = SERVICE.get(res, res)
        fn = state.get("Parameters", {}).get("FunctionName", "")
        m = re.search(r"\$\{(\w+)_arn\}", fn)
        if m:
            sub = f"Lambda: {m.group(1)}"
    elif state["Type"] == "Choice":
        sub = "Choice"
    extra = ""
    if state.get("Retry"):
        extra = "\n(retry + backoff)"
    return f"{name}\n{sub}{extra}" if sub else name


def add_states(g, states, prefix=""):
    """Add nodes and edges for one States map; return the id of its start node."""
    for name, st in states.items():
        nid = prefix + name
        if st["Type"] == "Parallel":
            with g.subgraph(name=f"cluster_{nid}") as c:
                c.attr(label=f"{name} (Parallel)", style="rounded,dashed", color="#E7157B", fontcolor="#E7157B")
                starts = []
                for i, br in enumerate(st["Branches"]):
                    with c.subgraph(name=f"cluster_{nid}_{i}") as b:
                        b.attr(label="", style="invis")
                        starts.append(add_states(b, br["States"], prefix=f"{nid}_{i}_"))
            g.node(nid, "", shape="point", width="0.12")
            for s in starts:
                g.edge(nid, s)
            ends = [f"{nid}_{i}_{n}" for i, br in enumerate(st["Branches"]) for n, s in br["States"].items() if s.get("End")]
            join = f"{nid}_join"
            g.node(join, "", shape="point", width="0.12")
            for e in ends:
                g.edge(e, join)
            src = join
        else:
            g.node(nid, label(name, st), **STYLE.get(st["Type"], {}))
            src = nid

        if "Next" in st:
            g.edge(src, prefix + st["Next"])
        for ch in st.get("Choices", []):
            cond = "valid == false" if ch.get("BooleanEquals") is False or "And" in ch else "match"
            g.edge(src, prefix + ch["Next"], label=cond, color="#C62828", fontcolor="#C62828")
        if "Default" in st:
            g.edge(src, prefix + st["Default"], label="valid" if name == "IsValid" else "error")
        for c in st.get("Catch", []):
            g.edge(src, prefix + c["Next"], label="Catch", style="dashed", color="#C62828", fontcolor="#C62828")
    return prefix + next(iter(states))


def main():
    text = re.sub(r"\$\{(partition|table_name|topic_arn|cdn_domain)\}", "x", ASL.read_text(encoding="utf-8"))
    asl = json.loads(text)
    g = graphviz.Digraph("workflow", format="png")
    g.attr(rankdir="TB", fontname="Helvetica", fontsize="12", pad="0.3", nodesep="0.35", ranksep="0.45",
           label="Step Functions workflow (generated from pipeline.asl.json)", labelloc="t")
    g.attr("node", fontname="Helvetica", fontsize="11")
    g.attr("edge", fontname="Helvetica", fontsize="9", color="#5F6880")
    g.node("start", "Start", shape="circle", style="filled", fillcolor="#141824", fontcolor="white", width="0.6")
    g.edge("start", add_states(g, asl["States"]))
    g.render(str(OUT), cleanup=True)
    print("Wrote", OUT.with_suffix(".png"))


if __name__ == "__main__":
    main()
