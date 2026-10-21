# Milestones and roadmap

## Milestones

Built in January 2026 on Cloud Run and Neon, then moved onto a home server in late September 2026, where it grew a GPU and two self-hosted models.

```mermaid
timeline
    title From Cloud Run to a home GPU cluster
    section January 2026
        Jan 19–25 : Personal website and FastAPI API
                  : Projects, notes, admin, image uploads
                  : Cloud Run and Neon
    section September 2026
        Sep 27–28 : Single-node k3s with the GPU on a laptop
                  : API moved home behind Cloudflare Tunnel
                  : Cloud Run removed
        Sep 29 : PostgreSQL on CloudNativePG, backups to R2
               : Argo CD GitOps, Sealed Secrets, deploy on merge
               : Prometheus, Loki, alerts by email
               : Note autocomplete on vLLM (Qwen3.5-0.8B)
        Sep 30 : Rate limits in Redis, edge caching, tracing
               : Image worker for WebP variants
               : GPU time-slicing with priority preemption
               : Ask chat (Qwen3.5-4B, CAG, streamed, cited)
    section October 2026
        Oct 1 : Ask about a highlighted passage
              : Questions kept for review in the admin
              : Follow-ups with conversation memory
              : 24k context, lint, tests and CI on both repos
```

## Roadmap

Planned, not promised; each item has a reason or a trigger.

- [ ] **Learn from real questions.** Rate answers in the admin's Questions page, and grow the 40-question evaluation set in [`eval/ask/`](../eval/ask/) from them.
- [ ] **Retrieval when the content outgrows the context.** Once the prompt nears its budget (the `AskPromptNearLimit` alert), the next step is a narrow agent with two tools, `search` over PostgreSQL full-text and `read` by document id. The evaluation set decides whether it beats reading everything.
- [ ] **Autocomplete, data and evaluation.** An admin page for suggestions and their outcomes, and offline evaluation against what was actually written.
- [ ] **Autocomplete, fine-tuning.** LoRA on the home GPU as a Kubernetes Job that pre-empts both models, with adapters stored in R2 and served by vLLM.
- [ ] **Autocomplete, reinforcement learning.** DPO from accepted and rejected suggestions, then GRPO with a verifiable reward, and A/B tests of adapters.
- [ ] **A small decision model.** Classify contact messages with a fine-tuned encoder on the CPU, compared with the answer model's structured outputs.
