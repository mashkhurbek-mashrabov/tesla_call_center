"""Retrieval tests.

Chunking is offline. Search needs GEMINI_API_KEY and a built kb_index.json, and costs
a few embedding calls.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
import rag  # noqa: E402


def test_chunking() -> int:
    """Offline: every KB doc yields sections with the metadata needed to cite them."""
    files = sorted(config.KB_DIR.glob("*.md"))
    assert files, f"no markdown in {config.KB_DIR}"

    total = 0
    for path in files:
        chunks = rag.chunk_file(path)
        assert chunks, f"{path.name} produced no chunks"
        for chunk in chunks:
            assert chunk["text"].strip(), f"{path.name}: empty chunk text"
            assert chunk["doc_title"], f"{path.name}: missing doc_title"
            assert chunk["section"], f"{path.name}: missing section"
            # The demo-data footer must not be indexed as content.
            assert "Demo data compiled" not in chunk["text"], (
                f"{path.name}: footer leaked into chunk"
            )
        total += len(chunks)

    print(f"  chunking: {len(files)} docs -> {total} sections")
    return total


def test_search_routing() -> int:
    """Online: questions land on the right document."""
    cases = [
        ("How long is the battery warranty on a Model Y?", "warranty.md"),
        ("what is an idle fee at a supercharger", "supercharging.md"),
        ("how do I schedule a service appointment", "service_scheduling.md"),
        ("what does a Wall Connector cost", "home_charging.md"),
        ("my car is locked and I need help", "roadside_assistance.md"),
    ]

    for query, expected in cases:
        results = rag.search(query)
        assert results, f"no results for {query!r}"
        top = results[0]
        assert top["doc"] == expected, (
            f"{query!r} -> {top['doc']} (expected {expected}); "
            f"top hits: {[(r['doc'], r['score']) for r in results[:3]]}"
        )
        print(f"  {expected:26} {top['score']:.3f}  {query[:40]}")

    return len(cases)


def test_uzbek_cross_lingual() -> int:
    """The agent speaks Uzbek; the KB is English. gemini-embedding-2 bridges that.

    If this fails, the KB would have to be translated -- so it is worth asserting
    rather than assuming.
    """
    cases = [
        ("Model Y batareyasiga kafolat qancha muddat?", "warranty.md"),
        ("Supercharger narxi qancha turadi?", "supercharging.md"),
        ("xizmat ko'rsatish uchun navbat olmoqchiman", "service_scheduling.md"),
        ("mashinam qulflanib qoldi", "roadside_assistance.md"),
        ("Wall Connector uyda o'rnatish", "home_charging.md"),
        ("buyurtmam qachon yetkaziladi", "delivery_orders.md"),
    ]

    for query, expected in cases:
        results = rag.search(query)
        assert results, f"no results for Uzbek query {query!r}"
        top = results[0]
        assert top["doc"] == expected, (
            f"{query!r} -> {top['doc']} (expected {expected}); "
            f"top hits: {[(r['doc'], r['score']) for r in results[:3]]}"
        )
        print(f"  {expected:26} {top['score']:.3f}  {query[:38]}")

    return len(cases)


def test_empty_payload() -> int:
    """A miss must instruct the model to admit ignorance, never to improvise."""
    payload = rag.format_for_model([])
    assert payload["found"] is False
    assert payload["passages"] == []
    assert config.SUPPORT_PHONE in payload["instruction"]
    return 1


OFFLINE = [test_chunking, test_empty_payload]
ONLINE = [test_search_routing, test_uzbek_cross_lingual]


def run(online: bool = True) -> int:
    total = 0
    for test in OFFLINE:
        total += test()

    if not online:
        print(f"rag: {total} offline assertions passed (search skipped)")
        return 0

    if not config.API_KEY:
        print("rag: GEMINI_API_KEY not set -- skipping search tests")
        return 0
    if not config.INDEX_PATH.exists():
        print("rag: kb_index.json missing (run build_kb.py) -- skipping search tests")
        return 0

    for test in ONLINE:
        total += test()
    print(f"rag: {total} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(online="--offline" not in sys.argv))
