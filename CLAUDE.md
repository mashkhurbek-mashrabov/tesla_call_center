# AI Call Center — agent notes

Uzbek-speaking voice agent that answers as Tesla customer support, grounded in a RAG
knowledge base, with a two-layer off-topic guard. Browser mic stands in for a phone
line; SIP is deliberately deferred.

Read `README.md` for setup and architecture. This file is the things that will waste
your time if you guess.

## Run it

```bash
.venv/bin/uvicorn server:app --port 8000 2>&1 | tee /tmp/cc.log
```

The `tee` matters: `tests/test_call.py` reads that log to assert what the agent said
and whether the KB was searched. Without it, those assertions silently skip.

```bash
.venv/bin/python tests/run.py                          # offline, free
.venv/bin/python tests/run.py --online                 # + retrieval, costs embeddings
.venv/bin/python tests/run.py --online --log /tmp/cc.log   # + 7 live calls
```

Run the offline tier before every commit. The live tier costs real tokens and can hit
`429 RESOURCE_EXHAUSTED` if run repeatedly — that is rate limiting, not a regression.
Wait and re-run before investigating.

After editing anything in `kb/`, re-run `.venv/bin/python build_kb.py` or the index
goes stale. The server caches the index at startup, so restart it too.

## Gemini API constraints

Verified against the current docs. Getting these wrong fails at runtime, sometimes
silently:

- `thinking_level` / `thinking_config` are **not supported** on `gemini-3.8-live`.
  They exist only on `gemini-3.8-live-extended-thinking`.
- `enable_affective_dialog` was **removed from the API**. Do not set it.
  `proactive_audio` is permanently on.
- `behavior: NON_BLOCKING` is the **default** for function calling. Response
  scheduling is `INTERRUPT` / `WHEN_IDLE` / `SILENT`.
- Audio formats are fixed: input **PCM16 / 16 kHz / mono**, output **PCM16 / 24 kHz /
  mono**. The two `AudioContext`s in `static/app.js` exist for exactly this reason.
- One response modality per session. Text comes from `output_audio_transcription`,
  never from adding `TEXT` alongside `AUDIO`.
- `gemini-embedding-2` has no `task_type`, and multiple raw strings in one `contents`
  list produce **one aggregated vector**. Each chunk must be wrapped in its own
  `types.Content` — see `rag.embed()`.

## The receive loop

`session.receive()` yields **one turn** and then the generator ends. This is by design
(SDK `live.py`, `_is_interaction_complete` → `break`), not a bug.

`gemini_to_browser()` wraps it in an outer `while True` so the call survives past the
greeting. Removing that loop makes the agent hang up immediately after saying hello,
which looks like a connection fault and is not.

An immediately-empty generator means Google's socket is gone, so the loop returns
rather than spinning hot.

A tool round trip **ends a turn before the answer is spoken**. Any test that stops at
the first `turn_complete` will conclude the agent went silent when it is working fine.

## Language: Uzbek only

The agent speaks Uzbek and nothing else, including when the caller writes English, and
refuses requests to switch language.

**The knowledge base stays in English.** `gemini-embedding-2` retrieves
cross-lingually — Uzbek queries hit the right English document, asserted 6/6 in
`tests/test_rag.py`. Translating `kb/` would fork the source of truth for no gain. Do
not "fix" this by translating the KB.

Cross-lingual similarity scores run lower than same-language (Uzbek 0.51–0.79 vs
English 0.76–0.87). That is why `MIN_SCORE` is 0.35. Raising it back toward 0.45 will
start dropping valid Uzbek questions.

## Units: metric only

The agent speaks km, m, kg, °C. The KB is imperial, so the model converts as it
speaks.

Common warranty mileages are a **lookup table** in `config.SYSTEM_INSTRUCTION`, not
left to the model's arithmetic. It originally rendered 100,000 miles as "bir yuz olti
ming kilometr" (106,000) instead of 160,000 — a digit transposition mid-sentence,
while getting the other figure in the same reply right. If you add a new mileage to
the KB, add its converted value to that table.

Wheel sizes in inches (18-dyuym) are product names and are deliberately **not**
converted. Neither are dollars, volts, amps, kilowatts, or warranty durations in
years.

## The guard

Two layers, and they are not redundant: the system instruction can be argued with, a
code check cannot. `guard.py` runs on both the tool-call query and the caller's
transcribed turn, but only the tool-call path in `run_tool()` **enforces** — that is
where a block actually stops the KB being searched. The transcript check in
`gemini_to_browser()` is observability: by `turn_complete` the model has already
spoken, so it logs and does not reject. It is how the word lists get tuned.

Three Uzbek-specific traps in the matcher:

- **Agglutination.** Uzbek stacks suffixes, so `aksiya` must also match
  `aksiyalarini`. Off-topic Uzbek stems listed in `UZBEK_STEMS` match with a suffix
  tail.
- **That tail must be a closed set of real inflections** (`_SUFFIX_TAIL`), never an
  open `\w{0,N}`. An open tail is anchored on the left only, so `osh` (pilaf) ate
  `oshxona` (kitchen), `din` ate `dinamik`, `dori` ate `dorixona`. Pinned by
  `test_uzbek_stem_boundaries`. Do not add derivational morphemes (`xona`, `paz`,
  `iv`) to that list — only case and possessive endings belong there.
- **Suffix tolerance must never be applied to English terms.** `war` plus a
  suffix tail swallows `warranty` and rejects every warranty question. There is a
  regression test (`test_word_boundaries`) pinning exactly this — it caught the bug
  when suffix matching was first added set-wide.
- **Apostrophes.** `o'zbek` / `o'zbek` / `oʻzbek` are the same word and transcription
  picks arbitrarily. `normalize()` folds them before matching.

Ambiguous input defaults to **allow**. A false refusal on a real customer is worse
than a weak search, and the retrieval gate catches the rest.

`OFF_TOPIC` is checked before `ON_TOPIC`, so a block-list hit beats every domain
word in the same sentence. That ordering is deliberate ("write a python script about
Tesla" is still a coding request), but it means each new block term must be checked
against Tesla vocabulary first. `sport` was removed for exactly this: it rejected
"Sport rejimi", a real drive mode. `futbol` and `chempionat` still cover sports.

## Conventions

- Deliberate simplifications carry a `ponytail:` comment naming the ceiling and the
  upgrade path. They are choices, not oversights — read the comment before
  "improving" one.
- No vector database: numpy cosine over an 80-chunk JSON index. Chroma plus torch for
  80 vectors is not a trade worth making until roughly 50k chunks.
- KB chunking is by `##` heading because a section is the natural citation unit. Keep
  each section self-contained; it is what gets retrieved in isolation.
- Every KB file carries front matter (`title`, `category`, `source_url`) and a
  demo-data footer. The footer is stripped at index time, asserted in `test_chunking`.

## Data provenance

`kb/` is **not** official Tesla documentation. tesla.com returns HTTP 403 to automated
fetches and bot-walls the browser, so the content was compiled from public sources on
2026-09-22 and carries a disclaimer in every file, the README, and the page footer.
Keep that disclaimer on anything you add.
