import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import typer
from rich.console import Console

from piki.wiki import WIKI_DIR

console = Console()


def _parse_graph() -> dict:
    if not WIKI_DIR.exists():
        return {"nodes": [], "edges": []}

    nodes = {}
    edges = []

    CATEGORY = {
        "decisions": ("#f59e0b", "decision"),
        "repos":     ("#3b82f6", "repo"),
        "concepts":  ("#10b981", "concept"),
        "meta":      ("#6b7280", "meta"),
    }

    for md in WIKI_DIR.rglob("*.md"):
        rel = md.relative_to(WIKI_DIR)
        if rel.parts[0] in (".git",):
            continue

        node_id = str(rel.with_suffix(""))
        color, category = CATEGORY.get(rel.parts[0], ("#8b5cf6", "general"))

        try:
            text = md.read_text(errors="ignore")
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    text = text[end + 4:].lstrip("\n")

            title = next(
                (line[2:].strip() for line in text.splitlines() if line.startswith("# ")),
                node_id,
            )
            nodes[node_id] = {
                "id": node_id,
                "label": title,
                "category": category,
                "color": color,
                "content": text[:600],
            }

            # [[wiki-link]] 문법
            for link in re.findall(r'\[\[([^\]]+)\]\]', text):
                edges.append({"source": node_id, "target": link.strip()})

            # 관련: 섹션의 링크만 파싱
            related_match = re.search(r'관련:\s*\n((?:[ \t]*[-*]\s*.*\n?)*)', text)
            if related_match:
                for link in re.findall(r'\(([^)]+\.md)\)', related_match.group(1)):
                    target = re.sub(r'#.*$', '', link).strip()
                    try:
                        target_id = str((md.parent / target).resolve().relative_to(WIKI_DIR).with_suffix(""))
                        edges.append({"source": node_id, "target": target_id})
                    except ValueError:
                        pass

        except Exception:
            pass

    valid = set(nodes)
    seen = set()
    deduped = []
    for e in edges:
        key = (e["source"], e["target"])
        if key not in seen and e["source"] in valid and e["target"] in valid and e["source"] != e["target"]:
            seen.add(key)
            deduped.append(e)

    return {"nodes": list(nodes.values()), "edges": deduped}


def _build_html(graph: dict) -> str:
    data = json.dumps(graph)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>piki graph</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f1117;color:#e2e8f0;font-family:-apple-system,monospace;display:flex;height:100vh;overflow:hidden}}
#graph{{flex:1}}
#sidebar{{width:320px;background:#1a1d27;border-left:1px solid #2d3748;padding:20px;overflow-y:auto;display:flex;flex-direction:column;gap:12px}}
h1{{font-size:13px;color:#718096;font-weight:400;letter-spacing:.05em}}
#node-title{{font-size:15px;font-weight:600}}
#node-content{{font-size:11px;color:#a0aec0;line-height:1.7;white-space:pre-wrap}}
#search{{background:#2d3748;border:1px solid #4a5568;color:#e2e8f0;padding:8px 12px;border-radius:6px;font-size:12px;width:100%;outline:none}}
#search:focus{{border-color:#667eea}}
#stats{{font-size:11px;color:#718096}}
.legend{{display:flex;flex-direction:column;gap:5px}}
.li{{display:flex;align-items:center;gap:8px;font-size:11px;color:#a0aec0}}
.dot{{width:9px;height:9px;border-radius:50%}}
.link{{stroke:#2d3748;stroke-opacity:.5}}
.node{{cursor:pointer}}
.node circle{{stroke-width:1.5;transition:r .15s}}
.node text{{fill:#a0aec0;font-size:10px;pointer-events:none}}
hr{{border:none;border-top:1px solid #2d3748}}
</style>
</head>
<body>
<svg id="graph"></svg>
<div id="sidebar">
  <h1>PIKI GRAPH</h1>
  <input id="search" placeholder="페이지 검색…" type="text"/>
  <div id="stats"></div>
  <div class="legend">
    <div class="li"><div class="dot" style="background:#f59e0b"></div>decisions</div>
    <div class="li"><div class="dot" style="background:#3b82f6"></div>repos</div>
    <div class="li"><div class="dot" style="background:#10b981"></div>concepts</div>
    <div class="li"><div class="dot" style="background:#8b5cf6"></div>general</div>
    <div class="li"><div class="dot" style="background:#6b7280"></div>meta</div>
  </div>
  <hr/>
  <div id="node-title" style="color:#4a5568">노드를 클릭하세요</div>
  <div id="node-content"></div>
</div>
<script>
const data={data};
const svg=d3.select("#graph");
const g=svg.append("g");
svg.attr("width","100%").attr("height","100%");
const W=()=>svg.node().clientWidth, H=()=>svg.node().clientHeight;

svg.call(d3.zoom().scaleExtent([.1,4]).on("zoom",e=>g.attr("transform",e.transform)));

const sim=d3.forceSimulation(data.nodes)
  .force("link",d3.forceLink(data.edges).id(d=>d.id).distance(90))
  .force("charge",d3.forceManyBody().strength(-250))
  .force("center",d3.forceCenter(W()/2,H()/2))
  .force("collide",d3.forceCollide(22));

const link=g.append("g").selectAll("line").data(data.edges).join("line").attr("class","link");

const node=g.append("g").selectAll(".node").data(data.nodes).join("g").attr("class","node")
  .call(d3.drag()
    .on("start",(e,d)=>{{if(!e.active)sim.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y;}})
    .on("drag",(e,d)=>{{d.fx=e.x;d.fy=e.y;}})
    .on("end",(e,d)=>{{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}}));

const radius=d=>d.category==="repo"?13:d.category==="decision"?11:9;
node.append("circle").attr("r",radius).attr("fill",d=>d.color)
  .attr("stroke",d=>d3.color(d.color).darker(.8));
node.append("text").attr("dy","1.6em").attr("text-anchor","middle")
  .text(d=>d.label.length>16?d.label.slice(0,16)+"…":d.label);

node.on("click",(e,d)=>{{
  document.getElementById("node-title").textContent=d.label;
  document.getElementById("node-title").style.color=d.color;
  document.getElementById("node-content").textContent=d.content;
}});

sim.on("tick",()=>{{
  link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
      .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
  node.attr("transform",d=>`translate(${{d.x}},${{d.y}})`);
}});

document.getElementById("stats").textContent=`${{data.nodes.length}} pages · ${{data.edges.length}} links`;

document.getElementById("search").addEventListener("input",e=>{{
  const q=e.target.value.toLowerCase();
  const match=d=>!q||d.label.toLowerCase().includes(q)||d.id.toLowerCase().includes(q);
  node.select("circle").attr("opacity",d=>match(d)?1:.12);
  node.select("text").attr("opacity",d=>match(d)?1:.12);
}});

window.addEventListener("resize",()=>sim.force("center",d3.forceCenter(W()/2,H()/2)).restart());
</script>
</body>
</html>"""


def show(
    port: int = typer.Option(7979, "--port", "-p", help="Local port."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically."),
):
    """Open graph-wiki as an interactive graph view in the browser."""
    if not WIKI_DIR.exists():
        console.print("[red]Wiki not set up.[/] Run [bold]piki setup[/] first.")
        raise typer.Exit(1)

    console.print("[dim]Building graph...[/]")
    graph = _parse_graph()
    html = _build_html(graph)
    console.print(f"[green]✓[/] {len(graph['nodes'])} pages · {len(graph['edges'])} links")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    console.print(f"[bold]Graph UI[/] → [cyan]{url}[/]  [dim]Ctrl+C to stop[/]")

    if not no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/]")
