"""One-shot: embed kb/*.md into kb_index.json. Re-run after editing the knowledge base."""

import rag

if __name__ == "__main__":
    rag.build()
