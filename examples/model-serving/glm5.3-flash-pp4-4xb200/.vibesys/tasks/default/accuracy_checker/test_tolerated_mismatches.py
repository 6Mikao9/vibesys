#!/usr/bin/env python3
"""CPU unit tests of the tolerated-mismatch rule: up to max(1, ceil(5% of rounds)) rounds may answer a
token no trusted build gave, if it is in some build's top 10 within 3.0 nats of that build's top token.
No server. Each test checks a property over seeded, generated inputs; runs under pytest or as a script.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checker  # noqa: E402
from common import Answer  # noqa: E402

CASES = 300
VOCAB = 150_000


def record(rng: random.Random, *, margin: float | None = None) -> dict:
    """One build's answer slot over a realistic vocabulary: 10 distinct tokens, falling logprobs.
    `margin`, when given, is the gap from the top token to the second."""
    tokens = rng.sample(range(VOCAB), 10)
    top_lp = rng.uniform(-2.0, 0.0)
    second = top_lp - (margin if margin is not None else rng.uniform(0.0, 4.0))
    lps = [top_lp, second] + sorted(
        (second - rng.uniform(0.0, 6.0) for _ in range(8)), reverse=True
    )
    return {
        "token": tokens[0],
        "top": [[t, lp] for t, lp in zip(tokens, lps, strict=True)],
        "tokens": [tokens[0]],
        "lp_range": [top_lp, top_lp],
    }


def answer(token: int, expect: dict, *, shift: float = 0.0) -> Answer:
    """The candidate's answer: `token` greedy, the reference's top logprobs moved by `shift`."""
    top = {int(t): lp - shift for t, lp in expect["top"]}
    top.setdefault(token, top[int(expect["token"])] + 0.01)
    return Answer(token=token, top=top, prompt_tokens=8, cached_tokens=0, seconds=0.1)


def gap_of(expect: dict, token: int) -> float:
    ref = {int(t): lp for t, lp in expect["top"]}
    return ref[int(expect["token"])] - ref[token]


def summary_of(verdicts: list[checker.Verdict], builds: list[str]) -> tuple[bool, dict]:
    return checker.summarize(verdicts, [], {"builds": [{"id": b} for b in builds]})


def test_budget_is_max_1_ceil_5_percent_of_rounds() -> None:
    assert checker.tolerated_budget(37) == 2
    for rounds in range(1, 1000):
        assert checker.tolerated_budget(rounds) == max(1, math.ceil(rounds * 5 / 100)), rounds


def test_a_mismatch_is_tolerable_iff_some_build_ranks_it_in_top_10_within_3_nats() -> None:
    rng = random.Random(11)
    for _ in range(CASES):
        records = {f"b{i}": record(rng) for i in range(rng.randint(1, 3))}
        pool = [int(t) for r in records.values() for t, _ in r["top"]] + [rng.randrange(VOCAB)]
        token = rng.choice(pool)
        verdict = checker.judge_builds(
            "c", records, answer(token, rng.choice(list(records.values()))), 8
        )
        within = [
            b
            for b, r in records.items()
            if token in {int(t) for t, _ in r["top"]}
            and gap_of(r, token) <= checker.MAX_TOLERATED_GAP_NATS
        ]
        if verdict.kind == "mismatch":
            assert verdict.tolerable == bool(within), (verdict, within)
            assert (checker.tolerance(records, token) is not None) == bool(within)
        if verdict.tolerable:
            assert verdict.kind == "mismatch" and verdict.build in within
            assert (
                2 <= verdict.ref_rank <= 10
                and verdict.ref_gap_nats <= checker.MAX_TOLERATED_GAP_NATS
            )


def flipped_rounds(n: int, flips: int, rng: random.Random, *, gap: float) -> list[checker.Verdict]:
    """n rounds answered with the build's top token, except the first `flips`, which answer its
    second token, `gap` nats below the top."""
    out = []
    for i in range(n):
        expect = record(rng, margin=gap if i < flips else None)
        token = int(expect["top"][1][0]) if i < flips else int(expect["token"])
        out.append(checker.judge_builds(f"r{i}", {"b": expect}, answer(token, expect), 8))
    return out


def test_tolerable_flips_pass_up_to_the_budget_and_fail_above_it() -> None:
    rng = random.Random(12)
    for _ in range(60):
        n = rng.randint(5, 120)
        budget = checker.tolerated_budget(n)
        gap = rng.uniform(checker.NEAR_TIE_NATS + 0.01, checker.MAX_TOLERATED_GAP_NATS)
        ok, summary = summary_of(flipped_rounds(n, budget, rng, gap=gap), ["b"])
        assert ok, summary["failures"]
        assert len(summary["tolerated_mismatches"]) == budget and summary["hard_mismatches"] == 0
        ok, summary = summary_of(flipped_rounds(n, budget + 1, rng, gap=gap), ["b"])
        assert not ok and any("above the" in f and "tolerated" in f for f in summary["failures"])


def test_a_flip_beyond_3_nats_fails_at_once() -> None:
    rng = random.Random(13)
    for _ in range(60):
        gap = rng.uniform(checker.MAX_TOLERATED_GAP_NATS + 0.01, 8.0)
        ok, summary = summary_of(flipped_rounds(37, 1, rng, gap=gap), ["b"])
        assert not ok and summary["hard_mismatches"] == 1 and not summary["tolerated_mismatches"]


def test_a_token_outside_the_top_10_fails_at_once() -> None:
    rng = random.Random(14)
    for _ in range(CASES):
        records = {f"b{i}": record(rng) for i in range(rng.randint(1, 3))}
        listed = {int(t) for r in records.values() for t, _ in r["top"]}
        token = rng.choice([t for t in range(VOCAB) if t not in listed][:50])
        verdict = checker.judge_builds("c", records, answer(token, next(iter(records.values()))), 8)
        assert verdict.kind == "mismatch" and not verdict.tolerable


def test_a_tolerable_replay_shaped_flip_counts_against_the_same_budget() -> None:
    rng = random.Random(15)
    for _ in range(60):
        n = 37
        verdicts = flipped_rounds(n, 1, rng, gap=1.0)
        expect = record(rng, margin=1.5)
        clean = checker.judge_builds("s", {"b": expect}, answer(int(expect["token"]), expect), 8)
        checker.judge_replay_shaped(clean, {"b": expect}, int(expect["top"][1][0]))
        assert clean.shaped_token is not None and not clean.problems
        ok, summary = summary_of([*verdicts[:-1], clean], ["b"])
        assert ok and len(summary["tolerated_mismatches"]) == 2, summary["failures"]
        # A third round with a tolerable flip exceeds the budget of 2.
        extra = flipped_rounds(1, 1, rng, gap=1.0)[0]
        extra.name = "extra"
        ok, summary = summary_of([*verdicts[:-2], clean, extra], ["b"])
        assert not ok
        # A replay-shaped token outside the top 10 stays a hard problem.
        wrong = checker.judge_builds("w", {"b": expect}, answer(int(expect["token"]), expect), 8)
        checker.judge_replay_shaped(wrong, {"b": expect}, VOCAB + 1)
        assert wrong.shaped_token is None and wrong.problems


def test_the_mean_logprob_gate_still_fails_a_shifted_server_with_tolerated_flips() -> None:
    rng = random.Random(16)
    verdicts = []
    for i in range(37):
        expect = record(rng, margin=1.0 if i < 2 else None)
        token = int(expect["top"][1][0]) if i < 2 else int(expect["token"])
        verdicts.append(
            checker.judge_builds(f"r{i}", {"b": expect}, answer(token, expect, shift=0.5), 8)
        )
    ok, summary = summary_of(verdicts, ["b"])
    assert not ok and any("mean |dlogprob|" in f for f in summary["failures"])


def adversarial_verdicts(kind: str, rng: random.Random, n: int = 37) -> list[checker.Verdict]:
    """A wrong server over n rounds of two trusted builds: random tokens, one constant token, or each
    round answered with the next round's reference answer (a shifted answer)."""
    rounds = [{"a": record(rng), "b": record(rng)} for _ in range(n)]
    constant = rng.randrange(VOCAB)
    out = []
    for i, records in enumerate(rounds):
        if kind == "random":
            token = rng.randrange(VOCAB)
        elif kind == "constant":
            token = constant
        else:
            token = int(rounds[(i + 1) % n]["a"]["token"])
        out.append(checker.judge_builds(f"r{i}", records, answer(token, records["a"]), 8))
    return out


def test_random_constant_and_shifted_answers_still_fail() -> None:
    rng = random.Random(17)
    for kind in ("random", "constant", "shifted"):
        for _ in range(30):
            ok, summary = summary_of(adversarial_verdicts(kind, rng), ["a", "b"])
            assert not ok and summary["hard_mismatches"] >= 30, (kind, summary["hard_mismatches"])


def test_headline_states_the_verdict_and_every_tolerated_mismatch() -> None:
    rng = random.Random(18)
    for flips in (0, 1, 2, 3):
        ok, summary = summary_of(flipped_rounds(37, flips, rng, gap=1.0), ["b"])
        line = checker.headline(ok, summary)
        assert "\n" not in line and line.startswith("ACCURACY PASS" if ok else "ACCURACY FAIL")
        assert ok == (flips <= 2)
        for entry in summary["tolerated_mismatches"]:
            assert f"{entry['name']} (logprob) got {entry['got']}" in line
        assert f"{flips} of 2 tolerated mismatch rounds" in line


def main() -> int:
    tests = [
        (name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)
    ]
    for name, fn in tests:
        fn()
        print(f"ok {name}")
    print(f"ALL {len(tests)} UNIT TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
