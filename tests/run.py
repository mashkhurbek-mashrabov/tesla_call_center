"""Test runner.

    python tests/run.py                       # offline only: free, no key, no server
    python tests/run.py --online              # + retrieval (needs key + built index)
    python tests/run.py --online --log FILE   # + live calls (needs a running server)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import test_guard  # noqa: E402
import test_rag  # noqa: E402
import test_voice  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", action="store_true", help="run tests that call the API")
    parser.add_argument("--log", type=Path, help="server log, enables live call tests")
    args = parser.parse_args()

    print("== guard (offline) ==")
    failed = test_guard.run()

    print("\n== voice (offline) ==")
    failed |= test_voice.run()

    print("\n== rag ==")
    failed |= test_rag.run(online=args.online)

    if args.log:
        import asyncio

        import test_call

        print("\n== live calls ==")
        failed |= asyncio.run(test_call.main(args.log))
    elif args.online:
        print("\n(skipping live call tests -- pass --log FILE to enable)")

    print("\n" + ("FAILURES" if failed else "OK"))
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
