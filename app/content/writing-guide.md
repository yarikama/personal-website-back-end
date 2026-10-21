# Writing lab notes for yarikama.com

Lab notes are short technical essays about things I built, measured or learned. They are written in English, in the first person, for engineers who did not build the thing.

## Fields

- **title**: what the note shows, in Title Case. A claim or a concrete situation beats a topic: "Two LLMs on One 8 GB Laptop GPU", "Why My Portfolio Chatbot Uses CAG, Not RAG".
- **slug**: the title in lowercase kebab-case, ASCII only (`two-llms-on-one-8gb-laptop-gpu`). It becomes the URL, so don't change it once published.
- **excerpt**: one or two sentences, about 170 to 260 characters. The first states the problem or the surprise; the second says what the note covers. It shows on the list page and in link previews, so it has to stand alone.
- **tags**: 2 to 5 proper nouns or established terms, in their usual capitalisation ("Kubernetes", "Argo CD", "LLM Serving"). Reuse existing tags (`list_lab_note_tags`) before inventing one.
- **date**: the day the note is published.
- **read_time**: computed from the body; never set it by hand.

## Body

- Start with `# <title>`, then `##` sections with headings that say something ("Time-slicing shares the clock, not the memory"), not labels ("Background").
- Open with the constraint or the question, in a few sentences. No preamble about why the topic matters.
- Prefer measured numbers to adjectives: "6,853 MiB at peak", not "much less memory". Say how they were measured.
- Compare alternatives in a table when there are more than two dimensions.
- Bold the one sentence per section a skimming reader must not miss, at most.
- End with what it cost or what was left out: the trade-offs, limits and what I'd do next. No "In conclusion".
- 500 to 900 words is typical. Cut anything a reader could skip without losing the argument.
- Images: upload them first, then embed with `![what it shows](url)`. Alt text describes the content, not "screenshot".

## Working on a note

- New notes start as drafts (`published: false`). Publish only when asked.
- Change a few sentences with `edit_lab_note_content` rather than resending the whole body.
- Never invent numbers, results or quotes. If a figure is missing, leave a visible `TODO:` and say so.
