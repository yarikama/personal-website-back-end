"""
Build the ask chat's system prompt from the live site, exactly as the
backend does, for evaluating models outside the backend.

Uses the public API (published content only, the same the backend puts in
the prompt) and the backend's own rendering, so the prompt matches
production byte for byte, apart from the order of documents created in the
same instant.

    PYTHONPATH=app uv run python eval/ask/build_prompt.py > prompt.json

With --summarize, the notes go in as summaries and prompt.json carries
what run.py needs for the two calls: the notes' full texts and the request
that asks which to read (ASK_SUMMARIZE_NOTES in production).
"""

import json
import subprocess
import sys
from datetime import date
from types import SimpleNamespace

from core import config
from services import ask

LIMIT = 100  # the API's page size cap

API = "https://api.yarikama.com/api/v1"
# Stands for each evaluation question in the request that chooses notes.
QUESTION = "{question}"


def get(path: str):
    # curl: Cloudflare answers 403 to urllib's default User-Agent.
    out = subprocess.run(
        ["curl", "-sf", f"{API}{path}"], capture_output=True, check=True, text=True
    )
    body = json.loads(out.stdout)
    return body.get("data", body)


def main() -> None:
    projects = sorted(
        get(f"/projects?limit={LIMIT}"), key=lambda p: (p["createdAt"], p["id"])
    )
    notes = sorted(
        get(f"/lab-notes?limit={LIMIT}"), key=lambda n: (n["createdAt"], n["id"])
    )
    if LIMIT in (len(projects), len(notes)):
        sys.exit("More than one page of content: paginate before trusting this.")

    project_docs = []
    for p in projects:
        obj = SimpleNamespace(
            title=p["title"],
            slug=p["slug"],
            year=p["year"],
            tags=p["tags"],
            description=p["description"],
            metrics=p.get("metrics"),
            link=p.get("link"),
            github=p.get("github"),
        )
        project_docs.append((obj, ask._project_text(obj)))

    note_docs = []
    for n in notes:
        full = get(f"/lab-notes/{n['slug']}")
        obj = SimpleNamespace(
            title=full["title"],
            slug=full["slug"],
            tags=full["tags"],
            excerpt=full["excerpt"],
            content=full["content"],
            date=date.fromisoformat(full["date"]),
        )
        note_docs.append((obj, ask._note_text(obj)))

    summarize = "--summarize" in sys.argv[1:]
    rendered = ask._render(project_docs, note_docs, summarize)
    prompt = rendered.prompt
    tokens = ask.estimate_tokens(prompt)
    budget = ask.prompt_budget()
    if tokens > (budget - ask.READ_TOKENS if summarize else budget):
        # Production would leave the oldest documents out (build_snapshot)
        # and renumber the rest; this prompt would no longer match it.
        sys.exit("The prompt is over its budget: production would drop documents.")
    # Sources by slug, to check each answer's citations against the
    # evaluation set's expected sources.
    slugs = {"R1": "resume"}
    for i, p in enumerate(projects, 1):
        slugs[f"P{i}"] = f"project:{p['slug']}"
    for i, n in enumerate(notes, 1):
        slugs[f"N{i}"] = f"note:{n['slug']}"

    out = {
        "system_prompt": prompt,
        "estimated_tokens": tokens,
        "sources": slugs,
        "max_tokens": config.ASK_MAX_TOKENS,
        "temperature": config.ASK_TEMPERATURE,
    }
    if summarize:
        # The backend's own request for choosing; run.py puts each question
        # where this one's is.
        snap = ask.Snapshot(
            key=(),
            system_prompt=prompt,
            sources=rendered.sources,
            notes=rendered.notes,
            read_tokens=budget - tokens,
        )
        choose = ask.Answer(snap, QUESTION).choose_request()
        out |= {
            "notes": rendered.notes,
            "read_tokens": budget - tokens,
            "max_read": ask.MAX_READ,
            "choose": choose,
        }
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
