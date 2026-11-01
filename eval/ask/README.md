# Ask evaluation set

`questions.jsonl` holds the questions used to choose the answer model and to
check changes to the prompt (homelab `docs/14-ask-chat-plan.md`). One JSON
object per line:

| Field | Meaning |
|---|---|
| `id` | `q01`… |
| `lang` | `en` or `zh`; the answer should be in the same language |
| `kind` | `answer`: the site covers it; `refuse`: off-topic or not on the site, decline without inventing; `injection`: tries to override the rules |
| `question` | What the visitor types |
| `sources` | What a good answer cites: `resume`, `project:<slug>` or `note:<slug>` |
| `points` | Facts a good answer contains, or for `refuse` and `injection`, what it must (not) do |

The mix: 15 answerable questions in English, 10 in Chinese, 5 about proper
nouns, 5 off-topic, 5 injection attempts and 3 with no language of their
own (`121212`, `???`, `asdf`), which should get an English reply.

When site content changes, check that the `sources` and `points` still hold.

## Running it

`build_prompt.py` builds the system prompt from the live site with the
backend's own rendering; `run.py` (standard library only) asks every
question and scores citations, language and refusals. The homelab repo's
`llm/eval/run_ask_candidate.sh` serves a candidate model and runs `run.py`
inside its pod:

```bash
PYTHONPATH=app uv run python eval/ask/build_prompt.py > /tmp/prompt.json
# in the homelab repo:
llm/eval/run_ask_candidate.sh <hf model> <revision> <name> <this dir> /tmp/prompt.json [vllm args]
```
