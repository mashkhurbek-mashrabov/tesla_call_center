---
name: verify-agent
description: Run the call center agent end to end and confirm a change actually works - rebuild index, restart server, run the test tiers, read the Uzbek transcript. Use when asked to verify, test, or check the agent after editing config, kb/, guard.py, rag.py, or server.py, or when asked "does it still work".
---

# Verify the agent end to end

The failure mode this prevents: passing tests against a stale index or a server still
running the old system instruction, and reporting success that is not real.

## 1. Rebuild the index, if `kb/` or chunking changed

```bash
.venv/bin/python build_kb.py
```

Skip only if nothing under `kb/` and nothing in `rag.chunk_file` changed. When in
doubt, rebuild — it is cheap relative to a false pass.

## 2. Restart the server

The index and `config.SYSTEM_INSTRUCTION` are both read at startup, so an edit to
either does nothing until restart.

```bash
pkill -9 -f "uvicorn server:app" 2>/dev/null; sleep 1
```

```bash
.venv/bin/uvicorn server:app --port 8000 2>&1 | tee /tmp/cc.log
```

Run that in the background. The `tee` is required — `tests/test_call.py` reads
`/tmp/cc.log` to assert what was said and whether the KB was hit. Without it those
assertions skip silently and everything "passes".

Confirm it came up with what you expect:

```bash
curl -s localhost:8000/health; echo; curl -s localhost:8000/config
```

`kb_chunks` should match the count `build_kb.py` just printed. If it does not, the
server is running an old index.

## 3. Run the tiers

```bash
.venv/bin/python tests/run.py
```

Offline: guard assertions and chunking. Free, no key. If this fails, stop and fix —
no point burning tokens on the live tier.

```bash
.venv/bin/python tests/run.py --online --log /tmp/cc.log
```

Retrieval routing plus live calls. Costs real tokens.

## 4. Read what the agent actually said

Tests assert structure; only the transcript shows quality.

```bash
grep "AGENT" /tmp/cc.log | sed 's/.*AGENT: //' | tr '\n' ' ' | fold -w 190
```

Check by eye:

- **Uzbek throughout.** Product names (Tesla, Model Y, Supercharger) stay English by
  design; everything else should not.
- **Metric only.** No "milya", "fut", "funt". Verify any converted number with
  `python -c "print(MILES * 1.60934)"` — the model has transposed digits before
  (rendered 100,000 miles as 106,000 km instead of 160,000).
- **Facts match the KB.** Cross-check against the relevant `kb/*.md`. A confident
  wrong answer is the worst outcome and no assertion catches it.
- **Refusals stay in character** and redirect rather than ending the call.

## 5. Known non-failures

- `429 RESOURCE_EXHAUSTED` — API rate limit from repeated live runs. Wait a minute and
  re-run. Not a regression.
- A `pkill` that returns exit code 1 — nothing was running. Fine.
- Background uvicorn task reported as "failed" after you kill it — that is the kill.
- Mic access denied in the in-app browser pane — device capture is blocked there.
  Mic input and barge-in can only be verified by a human at a real microphone; say so
  rather than implying you tested it.

## Reporting

State what you verified and what you did not. If the live tier did not run, say the
verification was offline only. Do not describe mic or barge-in behavior as tested.
