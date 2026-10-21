"""
Draws docs/architecture.png: how the backend runs in production.

Diagram as code with mingrammer/diagrams (needs Graphviz: brew install
graphviz). Edit this file, then from the repo root:

    uv run --extra docs python docs/architecture.py
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.general import Users
from diagrams.custom import Custom
from diagrams.gcp.compute import GPU
from diagrams.k8s.podconfig import Secret
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.gitops import ArgoCD
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.logging import Loki
from diagrams.onprem.monitoring import Grafana, Prometheus
from diagrams.onprem.network import Traefik
from diagrams.onprem.tracing import Tempo
from diagrams.onprem.vcs import Github
from diagrams.programming.framework import FastAPI, Vercel
from diagrams.programming.language import Python
from diagrams.saas.cdn import Cloudflare

HERE = Path(__file__).parent
VLLM = str(HERE / "icons" / "vllm.png")

INK = "#1a1a1a"
SAGE = "#6b8f8b"
MUTED = "#8a8a8a"

graph = {
    "fontname": "Helvetica",
    "fontsize": "22",
    "labelloc": "t",
    "pad": "0.6",
    "nodesep": "0.55",
    "ranksep": "0.9",
    "splines": "spline",
    "bgcolor": "white",
}
node = {"fontname": "Helvetica", "fontsize": "12", "fontcolor": INK}
edge = {
    "fontname": "Helvetica",
    "fontsize": "10",
    "fontcolor": "#555555",
    "color": "#9a9a9a",
}


def box(label: str, fill: str, line: str = "#d4d4d8") -> dict:
    return {
        "label": label,
        "fontname": "Helvetica",
        "fontsize": "13",
        "fontcolor": INK,
        "style": "rounded,filled",
        "fillcolor": fill,
        "pencolor": line,
        "penwidth": "1.2",
        "margin": "18",
    }


def flow(label: str = "", color: str = SAGE, style: str = "solid") -> Edge:
    return Edge(label=label, color=color, fontcolor=color, style=style, penwidth="1.6")


with Diagram(
    "yarikama.com — backend architecture",
    filename=str(HERE / "architecture"),
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph,
    node_attr=node,
    edge_attr=edge,
):
    visitors = Users("Visitors")
    site = Vercel("www.yarikama.com\nReact SPA on Vercel")
    edge_cf = Cloudflare("api.yarikama.com\nCloudflare edge cache\n+ Tunnel")

    with Cluster(
        "Home server · single-node k3s · RTX 4060 Laptop 8 GB",
        graph_attr=box(
            "Home server · single-node k3s · RTX 4060 Laptop 8 GB", "#f8f7f4", "#c9c9c4"
        ),
    ):
        traefik = Traefik("cloudflared → Traefik\n/api/v1/ask on its own Service")

        with Cluster("website", graph_attr=box("namespace website", "#ffffff")):
            api = FastAPI(
                "FastAPI API\nCAG prompt · SSE answers\nrate limits · JWT admin"
            )
            worker = Python("Image worker\nWebP variants")
            db = PostgreSQL("PostgreSQL 17\nCloudNativePG")
            redis = Redis("Redis\nrate limits · job queue\nconversation memory")

        with Cluster(
            "llm · one GPU, time-sliced",
            graph_attr=box(
                "namespace llm · one GPU, time-sliced, fixed memory budgets",
                "#eef3f2",
                "#b9cdca",
            ),
        ):
            # Labels above the icon: the API's edges come in from below left.
            ask = Custom(
                "vLLM · Qwen3.5-4B AWQ\nAsk chat · 24k context", VLLM, labelloc="t"
            )
            complete = Custom(
                "vLLM · Qwen3.5-0.8B\nnote autocomplete", VLLM, labelloc="t"
            )
            gpu = GPU("RTX 4060 Laptop\n8 GB")

        # No edge from each pod: Prometheus scrapes them all, Alloy ships
        # every pod's logs, and the API sends traces.
        with Cluster(
            "monitoring",
            graph_attr=box(
                "monitoring · metrics, logs and traces from every pod"
                " · alerts by email",
                "#fbf7ec",
                "#e3d3a8",
            ),
        ):
            prom = Prometheus("Prometheus\n+ Alertmanager")
            loki = Loki("Loki")
            tempo = Tempo("Tempo")
            grafana = Grafana("Grafana")

        with Cluster(
            "gitops", graph_attr=box("GitOps · the cluster is what Git says", "#ffffff")
        ):
            argo = ArgoCD("Argo CD\napplies every app\nsync · self-heal")
            sealed = Secret("Sealed Secrets\nencrypted in Git")

    r2 = Cloudflare("Cloudflare R2\nimages · DB backups")
    with Cluster("GitHub", graph_attr=box("GitHub", "#ffffff")):
        repo = Github("personal-website-back-end")
        ci = GithubActions("CI: lint · test\nimage → GHCR")
        homelab = Github("homelab (private)\nmanifests")

    # Requests
    visitors >> flow() >> site
    visitors >> flow("HTTPS") >> edge_cf
    site >> Edge(style="dashed", color=MUTED, label="fetch / SSE") >> edge_cf
    edge_cf >> flow("Tunnel, no open ports") >> traefik >> flow() >> api

    # The API's dependencies
    api >> flow("SQL") >> db
    api >> flow() >> redis
    api >> flow("chat completions") >> ask
    api >> flow("completions") >> complete
    ask - Edge(color=MUTED, style="dotted") - gpu
    complete - Edge(color=MUTED, style="dotted") - gpu
    redis >> Edge(color=MUTED, label="jobs") >> worker
    worker >> Edge(color=MUTED) >> r2
    api >> Edge(color=MUTED, label="uploads") >> r2
    db >> Edge(color=MUTED, label="WAL + daily backups") >> r2

    # Observability
    [prom, loki, tempo] >> Edge(color="#c4a35a") >> grafana

    # Delivery
    (
        repo
        >> Edge(color=INK)
        >> ci
        >> Edge(color=INK, label="commit image tag")
        >> homelab
    )
    homelab >> Edge(color=INK, label="pulls ≤ 3 min") >> argo
    sealed - Edge(color=MUTED, style="dotted") - argo

    # Layout only: monitoring sits right of the models, GitHub below the
    # visitors, so the picture is wide rather than tall.
    invisible = Edge(style="invis")
    ask >> invisible >> loki
    complete >> invisible >> prom
    repo >> invisible >> argo
