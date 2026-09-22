"""Retrieval over the company knowledge base.

Chunking is by markdown '##' section, because a section IS the natural citation unit
here -- the agent says "according to our warranty information" and that maps to a real
heading. No token-window chunker, no tiktoken dependency.

# ponytail: numpy dot product over a JSON file. ~90 vectors does not justify a vector
# database; swap in one past roughly 50k chunks.
"""

import json
import re
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types

import config

_client: genai.Client | None = None
_index: dict | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        if not config.API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set (see .env.example)")
        _client = genai.Client(api_key=config.API_KEY)
    return _client


def parse_front_matter(raw: str) -> tuple[dict, str]:
    """Split '---' YAML-ish front matter from the body. Only flat key: value pairs."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    meta = {}
    for line in raw[3:end].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, raw[end + 4 :]


def chunk_file(path: Path) -> list[dict]:
    """One chunk per '##' section, carrying enough metadata to cite it."""
    meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
    title = meta.get("title", path.stem.replace("_", " ").title())

    chunks = []
    # Split on '##' headings, keeping the heading with its body.
    parts = re.split(r"\n##\s+", "\n" + body)
    for part in parts:
        part = part.strip()
        if not part or part.startswith(">"):
            continue
        heading, _, section_body = part.partition("\n")
        section_body = section_body.strip()
        # Drop the demo-data footer line from the indexed text.
        section_body = "\n".join(
            ln for ln in section_body.splitlines() if not ln.startswith(">")
        ).strip()
        if not section_body:
            continue
        chunks.append(
            {
                "doc": path.name,
                "doc_title": title,
                "category": meta.get("category", ""),
                "section": heading.strip(),
                "source_url": meta.get("source_url", ""),
                # Embed heading + body so the heading's words are searchable too.
                "text": f"{title} - {heading.strip()}\n{section_body}",
            }
        )
    return chunks


def embed(texts: list[str]) -> np.ndarray:
    """Embed each text separately.

    gemini-embedding-2 aggregates multiple raw strings in `contents` into ONE vector,
    so each text must be wrapped in its own Content to get one vector per text.
    """
    vectors: list[list[float]] = []
    batch = 32
    for i in range(0, len(texts), batch):
        window = texts[i : i + batch]
        result = client().models.embed_content(
            model=config.EMBED_MODEL,
            contents=[
                types.Content(parts=[types.Part.from_text(text=t)]) for t in window
            ],
            config=types.EmbedContentConfig(output_dimensionality=config.EMBED_DIM),
        )
        vectors.extend(e.values for e in result.embeddings)

    matrix = np.array(vectors, dtype=np.float32)
    # Non-default dimensionalities come back normalized already; normalizing again is
    # one line and makes the dot product a cosine no matter what the API returns.
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.clip(norms, 1e-9, None)


def build(kb_dir: Path = config.KB_DIR, out: Path = config.INDEX_PATH) -> int:
    files = sorted(kb_dir.glob("*.md"))
    if not files:
        raise RuntimeError(f"no markdown files in {kb_dir}")

    chunks: list[dict] = []
    for path in files:
        found = chunk_file(path)
        chunks.extend(found)
        print(f"  {path.name}: {len(found)} sections")

    print(f"embedding {len(chunks)} chunks with {config.EMBED_MODEL}...")
    vectors = embed([c["text"] for c in chunks])

    out.write_text(
        json.dumps({"chunks": chunks, "vectors": vectors.tolist()}), encoding="utf-8"
    )
    print(f"wrote {out} ({len(chunks)} chunks, dim {vectors.shape[1]})")
    return len(chunks)


def load() -> dict:
    global _index
    if _index is None:
        if not config.INDEX_PATH.exists():
            raise RuntimeError(
                f"{config.INDEX_PATH} missing -- run: python build_kb.py"
            )
        raw = json.loads(config.INDEX_PATH.read_text(encoding="utf-8"))
        _index = {
            "chunks": raw["chunks"],
            "vectors": np.array(raw["vectors"], dtype=np.float32),
        }
    return _index


def search(query: str, top_k: int = config.TOP_K) -> list[dict]:
    """Top-k chunks above MIN_SCORE, best first."""
    index = load()
    query_vec = embed([query])[0]
    scores = index["vectors"] @ query_vec

    order = np.argsort(scores)[::-1][:top_k]
    results = []
    for i in order:
        score = float(scores[i])
        if score < config.MIN_SCORE:
            continue
        chunk = index["chunks"][i]
        results.append({**chunk, "score": round(score, 4)})
    return results


def format_for_model(results: list[dict]) -> dict:
    """Payload handed back to the model as the tool response."""
    if not results:
        return {
            "found": False,
            "instruction": (
                "No relevant information found in the knowledge base. Tell the caller "
                "IN UZBEK that you do not have that detail on hand and offer to connect "
                f"them with a specialist or give the support line {config.SUPPORT_PHONE}. "
                "Do not answer from your own knowledge. Speak Uzbek only."
            ),
            "passages": [],
        }
    return {
        "found": True,
        "instruction": (
            "Answer the caller using only these passages. The passages are in English "
            "and use imperial units; translate the answer into natural spoken Uzbek AND "
            "convert every distance, weight, and temperature to metric (km, m, kg, C) "
            "before speaking, rounded to a number a person would say out loud. Keep it "
            "to one to three spoken sentences. Do not read the source names aloud "
            "verbatim, and never read the English text aloud."
        ),
        "passages": [
            {
                "source": f"{r['doc_title']} - {r['section']}",
                "content": r["text"],
            }
            for r in results
        ],
    }
