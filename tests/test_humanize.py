import asyncio
import random

import pytest

from collector.humanize import BROWSER_CONTEXT_DEFAULTS, act_like_a_person, human_delay_seconds


def test_human_delay_quick_recheck_stays_near_base(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.1)  # forces the "quick recheck" branch
    monkeypatch.setattr(random, "uniform", lambda a, b: (a + b) / 2)
    delay = human_delay_seconds(base_seconds=30, jitter_seconds=10)
    assert 19 <= delay <= 41  # base +/- jitter, roughly


def test_human_delay_never_goes_below_a_floor(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.1)
    monkeypatch.setattr(random, "uniform", lambda a, b: a)  # worst case: full negative jitter
    delay = human_delay_seconds(base_seconds=5, jitter_seconds=10)
    assert delay >= 3.0


def test_human_delay_distracted_branch_is_multiple_of_base(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.85)  # "briefly distracted" branch
    monkeypatch.setattr(random, "uniform", lambda a, b: a)  # pick the low end (2.0x)
    delay = human_delay_seconds(base_seconds=30, jitter_seconds=10)
    assert delay == 60.0


def test_human_delay_long_break_branch_is_a_big_multiple_of_base(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.99)  # "long break" branch
    monkeypatch.setattr(random, "uniform", lambda a, b: a)  # pick the low end (8.0x)
    delay = human_delay_seconds(base_seconds=30, jitter_seconds=10)
    assert delay == 240.0


def test_human_delay_distribution_is_mostly_quick_rechecks():
    random.seed(42)
    samples = [human_delay_seconds(base_seconds=30, jitter_seconds=10) for _ in range(2000)]
    quick = sum(1 for s in samples if s <= 45)
    # ~75% of the time per the documented distribution; allow slack for randomness.
    assert 0.65 < quick / len(samples) < 0.85


def test_browser_context_defaults_look_like_a_real_desktop():
    assert BROWSER_CONTEXT_DEFAULTS["viewport"]["width"] >= 1024
    assert "HeadlessChrome" not in BROWSER_CONTEXT_DEFAULTS["user_agent"]
    assert BROWSER_CONTEXT_DEFAULTS["locale"]
    assert BROWSER_CONTEXT_DEFAULTS["timezone_id"]


class _FakeMouse:
    def __init__(self):
        self.moved = False
        self.wheeled = False

    async def move(self, x, y):
        self.moved = True

    async def wheel(self, dx, dy):
        self.wheeled = True


class _FakePage:
    def __init__(self):
        self.mouse = _FakeMouse()


async def _no_sleep(_seconds):
    return None


def test_act_like_a_person_moves_the_mouse(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    page = _FakePage()
    asyncio.run(act_like_a_person(page))
    assert page.mouse.moved is True


def test_act_like_a_person_never_raises_when_page_is_broken(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    class BrokenPage:
        @property
        def mouse(self):
            raise RuntimeError("page closed")

    # Must not propagate — a cosmetic interaction can't be allowed to break
    # the real polling cycle.
    asyncio.run(act_like_a_person(BrokenPage()))
