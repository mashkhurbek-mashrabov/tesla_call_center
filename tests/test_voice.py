"""Voice selection tests. Offline -- no API key, no server, no cost."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
from server import live_config, pick_voice  # noqa: E402


def test_known_voice_passes_through() -> int:
    for name in config.VOICES:
        assert pick_voice(name) == name, f"valid voice rejected: {name}"
    return len(config.VOICES)


def test_unknown_voice_falls_back() -> int:
    """The name reaches Google, so anything off the list must not be forwarded --
    an invalid voice fails the whole session, not just the audio."""
    for value in ["Nonexistent", "", None, "kore", "Kore; DROP", "../Puck"]:
        assert pick_voice(value) == config.VOICE_NAME, f"leaked through: {value!r}"
    return 6


def test_default_is_selectable() -> int:
    """The UI preselects default_voice, so it has to be in the list it renders."""
    assert config.VOICE_NAME in config.VOICES, (
        f"default {config.VOICE_NAME!r} missing from VOICES"
    )
    return 1


def test_live_config_carries_voice() -> int:
    """Regression: the voice has to survive into speech_config, not just be picked."""
    cfg = live_config("Aoede")
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Aoede"
    # The Uzbek-only constraint lives in the system instruction; language_code stays
    # unset because uz-UZ is not a supported Live API speech language.
    assert cfg.speech_config.language_code is None, "language_code must stay unset"
    return 2


TESTS = [
    test_known_voice_passes_through,
    test_unknown_voice_falls_back,
    test_default_is_selectable,
    test_live_config_carries_voice,
]


def run() -> int:
    total = 0
    for test in TESTS:
        count = test()
        total += count
        print(f"  {test.__name__}: {count} assertions")
    print(f"voice: {total} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
