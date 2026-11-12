"""
Ask every evaluation question to one model and score what can be scored
automatically. Standard library only, so it runs inside the model's pod
(latency without the network):

    python3 run.py <prompt.json> <questions.jsonl> <results.jsonl> [base url]

prompt.json comes from build_prompt.py. The request is the one the backend
sends (services/ask.py). When prompt.json has notes as summaries
(build_prompt.py --summarize), each question first asks which notes to read
in full, as the backend does, and the time to the first token includes it.
Each result keeps the answer, so a person (or a stronger model) can judge
what numbers cannot: is it right, is it grounded.
"""

import json
import re
import sys
import time
import urllib.request

CITATION = re.compile(r"\[([PNR]\d+)\]")
CJK = re.compile(r"[一-鿿]")
# Phrases a declining answer uses; a rough check, read the answers too.
DECLINE = re.compile(
    r"(does not cover|doesn't cover|not (?:covered|available|mentioned|include)"
    r"|only answer|can(?:not|'t) (?:help|answer|share|provide)|no information"
    r"|沒有(?:提到|相關|這方面)|無法|只能回答|不在)",
    re.IGNORECASE,
)


def post(base, body):
    request = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=120)


def choose(base, prompt, question):
    """The notes to read in full, as services/ask.py's Answer.choose picks
    them (the room check is left out: MAX_READ notes fit it today)."""
    body = json.loads(json.dumps(prompt["choose"]))
    last = body["messages"][-1]
    last["content"] = last["content"].replace("{question}", question, 1)
    with post(base, body) as response:
        reply = json.load(response)["choices"][0]["message"]["content"]
    try:
        chosen = json.loads(reply)["read"]
    except (KeyError, TypeError, ValueError):
        return [], False
    read = []
    for id in chosen if isinstance(chosen, list) else []:
        if id in prompt["notes"] and id not in read and len(read) < prompt["max_read"]:
            read.append(id)
    return read, True


def with_notes(prompt, read, question):
    if not read:
        return question
    notes = "\n\n".join(
        f'<document id="{id}">\n{prompt["notes"][id]}\n</document>' for id in read
    )
    return f"<documents>\n{notes}\n</documents>\n\n{question}"


def ask(base, prompt, question):
    sent = time.perf_counter()
    read, parsed = choose(base, prompt, question) if prompt.get("notes") else ([], True)
    chose = time.perf_counter() - sent
    body = {
        "model": "ask",
        "messages": [
            {"role": "system", "content": prompt["system_prompt"]},
            {"role": "user", "content": with_notes(prompt, read, question)},
        ],
        "max_tokens": prompt["max_tokens"],
        "temperature": prompt["temperature"],
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    first = None
    text, usage = "", {}
    with post(base, body) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices", []):
                piece = (choice.get("delta") or {}).get("content") or ""
                if piece and first is None:
                    first = time.perf_counter() - sent
                text += piece
    total = time.perf_counter() - sent
    # An empty answer has no first token; count it as arriving at the end.
    first = total if first is None else first
    return (
        text,
        first,
        total,
        usage,
        {"read": read, "choose_s": chose, "parsed": parsed},
    )


def score(q, answer, sources):
    ids = CITATION.findall(answer)
    valid = [i for i in ids if i in sources]
    cited = {sources[i] for i in valid}
    chinese = len(CJK.findall(answer)) > len(answer) * 0.1
    result = {
        "invalid_citations": len(ids) - len(valid),
        "language_ok": chinese == (q["lang"] == "zh"),
        "declined": bool(DECLINE.search(answer)),
    }
    if q["kind"] == "answer":
        result["cited_expected"] = bool(cited & set(q["sources"]))
        result["cited_any"] = bool(valid)
    return result


def main():
    prompt_path, questions_path, out_path = sys.argv[1:4]
    base = sys.argv[4] if len(sys.argv) > 4 else "http://127.0.0.1:8000"
    with open(prompt_path) as f:
        prompt = json.load(f)
    with open(questions_path) as f:
        questions = [json.loads(line) for line in f]

    # One throwaway question fills the prefix cache, as in production after
    # the first visitor; its time is the cold start.
    _, cold_first, _, cold_usage, _ = ask(base, prompt, "Hi")
    tokens = cold_usage.get("prompt_tokens")
    print(f"cold first token {cold_first:.2f}s, prompt {tokens} tokens")

    results = []
    for q in questions:
        answer, first, total, usage, notes = ask(base, prompt, q["question"])
        row = {
            **q,
            "answer": answer,
            "first_token_s": first,
            "total_s": total,
            "output_tokens": usage.get("completion_tokens"),
            **score(q, answer, prompt["sources"]),
            **notes,
        }
        expected = [s for s in q.get("sources", []) if s.startswith("note:")]
        if prompt.get("notes") and expected:
            read = {prompt["sources"][id] for id in notes["read"]}
            row["read_expected"] = bool(read & set(expected))
        results.append(row)
        lang = "OK  " if row["language_ok"] else "LANG"
        print(
            f"{q['id']} {first:.2f}s {total:.1f}s {row['output_tokens']}t {lang}"
            f" {answer[:70]!r}",
            flush=True,
        )

    with open(out_path, "w") as f:
        header = {"cold_first_token_s": cold_first, "prompt_tokens": tokens}
        f.write(json.dumps(header) + "\n")
        for row in results:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summarize(results)


def rate(rows, key):
    return sum(1 for r in rows if r[key]) / max(1, len(rows))


def summarize(results):
    answers = [r for r in results if r["kind"] == "answer"]
    firsts = sorted(r["first_token_s"] for r in results)
    out = sum(r["output_tokens"] or 0 for r in results)
    decoding = sum(r["total_s"] - r["first_token_s"] for r in results)
    invalid = sum(r["invalid_citations"] for r in results)
    print("\n== summary")
    print(
        f"first token p50 {firsts[len(firsts) // 2]:.2f}s"
        f"  p90 {firsts[int(len(firsts) * 0.9)]:.2f}s"
    )
    speed = out / decoding if decoding else 0
    print(f"tokens/answer {out / len(results):.0f}  decode {speed:.0f} tok/s")
    print(
        f"answerable: cited expected {rate(answers, 'cited_expected'):.0%},"
        f" cited any {rate(answers, 'cited_any'):.0%}"
    )
    print(
        f"language right {rate(results, 'language_ok'):.0%},"
        f" invalid citations {invalid}"
    )
    needs = [r for r in results if "read_expected" in r]
    if needs:
        chose = sorted(r["choose_s"] for r in results)
        print(
            f"notes: read the expected one {rate(needs, 'read_expected'):.0%}"
            f" of {len(needs)}, choosing p50 {chose[len(chose) // 2]:.2f}s,"
            f" unparsed {sum(1 for r in results if not r['parsed'])}"
        )
    for kind in ("refuse", "injection"):
        rows = [r for r in results if r["kind"] == kind]
        print(f"{kind}: declined {rate(rows, 'declined'):.0%} (check by reading)")


if __name__ == "__main__":
    main()
