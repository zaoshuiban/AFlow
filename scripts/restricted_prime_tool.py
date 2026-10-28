"""Reusable exact tool for restricted-digit prime enumeration.

The detector and parser are intentionally narrow.  Unsupported wording or
constraints cause a refusal so the contextual router can use its baseline
fallback instead of guessing.
"""

from __future__ import annotations

import itertools
import math
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


RESTRICTED_DIGIT_PRIME_MECHANISM = "restricted_digit_prime_enumeration"


@dataclass(frozen=True)
class RestrictedPrimeToolResult:
    status: str
    mechanism: str = ""
    response: str = ""
    observation: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RestrictedDigitPrimeEnumerationTool:
    mechanism = RESTRICTED_DIGIT_PRIME_MECHANISM
    version = "2"
    max_distinct_digits = 10
    max_permutations = 1_000_000

    @classmethod
    def detect(cls, problem: str) -> bool:
        text = " ".join(str(problem).lower().split())
        asks_count = bool(re.search(r"\bhow many\b|\bnumber of\b", text))
        asks_primes = bool(re.search(r"\bprime (?:numbers?|integers?)\b", text))
        digit_set = bool(
            re.search(
                r"(?:digit set|set)\s*\$?\s*\\?\{\s*\d(?:\s*,\s*\d)+\s*\\?\}",
                text,
            )
        )
        bounded_arrangement = bool(
            re.search(r"\barrang(?:e|ed|ement|ements|ing)\b", text)
            and re.search(
                r"\bwithout repetition\b|\bno (?:digit )?repetition\b|"
                r"\bdifferent digits?\b|\bdistinct digits?\b",
                text,
            )
        )
        digits = cls._parse_digit_set(problem)
        leading_zero_safe = bool(
            digits is not None
            and (0 not in digits or re.search(r"\bleading zero\b", text))
        )
        excluded_intent = bool(
            re.search(
                r"\b(?:probability|prove|proof|show that|does there exist|list all)\b|"
                r"\brepetition (?:is|are) allowed\b|\bwith repetition\b|"
                r"\bnot (?:only )?prime\b",
                text,
            )
        )
        return all(
            (asks_count, asks_primes, digit_set, bounded_arrangement, leading_zero_safe)
        ) and not excluded_intent

    @staticmethod
    def _parse_digit_set(problem: str) -> Optional[list[int]]:
        match = re.search(
            r"(?:digit set|set)\s*\$?\s*\\?\{\s*(\d(?:\s*,\s*\d)+)\s*\\?\}",
            str(problem),
            re.I,
        )
        if not match:
            return None
        digits = [int(value) for value in re.findall(r"\d", match.group(1))]
        if len(digits) != len(set(digits)):
            return None
        return sorted(digits)

    @staticmethod
    def _parse_length_range(problem: str) -> Optional[tuple[int, int]]:
        text = " ".join(str(problem).lower().split())
        number_words = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        }
        patterns = (
            r"between\s+(?:one|1)\s+and\s+(\d+)\s+distinct digits?",
            r"(?:from|using)\s+(?:one|1)\s+(?:through|to)\s+(\d+)\s+digits?",
            r"(?:at most|up to)\s+(\d+)\s+digits?",
            r"(?:one|1)-digit\s+(?:through|to)\s+(\d+)-digit",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return 1, int(match.group(1))
        fewer = re.search(
            r"(?:pick|choose|use)\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
            r"\s+or fewer\s+(?:different|distinct)\s+digits?",
            text,
        )
        if fewer:
            token = fewer.group(1)
            return 1, int(token) if token.isdigit() else number_words[token]
        return None

    @staticmethod
    def _is_prime(value: int) -> bool:
        if value < 2:
            return False
        if value in (2, 3):
            return True
        if value % 2 == 0 or value % 3 == 0:
            return False
        divisor = 5
        while divisor * divisor <= value:
            if value % divisor == 0 or value % (divisor + 2) == 0:
                return False
            divisor += 6
        return True

    @staticmethod
    def _permutation_count(digit_count: int, minimum: int, maximum: int) -> int:
        return sum(
            math.factorial(digit_count) // math.factorial(digit_count - length)
            for length in range(minimum, maximum + 1)
        )

    @classmethod
    def solve(cls, problem: str) -> RestrictedPrimeToolResult:
        started = time.perf_counter()
        if not cls.detect(problem):
            return RestrictedPrimeToolResult(status="no_match")
        digits = cls._parse_digit_set(problem)
        length_range = cls._parse_length_range(problem)
        if digits is None or length_range is None:
            return RestrictedPrimeToolResult(
                status="refused",
                mechanism=cls.mechanism,
                observation="known_mechanism_but_parameters_not_extractable",
            )
        minimum, maximum = length_range
        if (
            not digits
            or len(digits) > cls.max_distinct_digits
            or minimum < 1
            or maximum < minimum
            or maximum > len(digits)
        ):
            return RestrictedPrimeToolResult(
                status="refused",
                mechanism=cls.mechanism,
                observation="parameterized_tool_boundary:invalid_digit_or_length_range",
                parameters={"digits": digits, "min_length": minimum, "max_length": maximum},
            )
        candidate_bound = cls._permutation_count(len(digits), minimum, maximum)
        if candidate_bound > cls.max_permutations:
            return RestrictedPrimeToolResult(
                status="refused",
                mechanism=cls.mechanism,
                observation=(
                    "parameterized_tool_boundary:permutation_count="
                    f"{candidate_bound} exceeds max={cls.max_permutations}"
                ),
                parameters={"digits": digits, "min_length": minimum, "max_length": maximum},
            )
        primes: set[int] = set()
        for length in range(minimum, maximum + 1):
            for arrangement in itertools.permutations(digits, length):
                if arrangement[0] == 0:
                    continue
                value = 0
                for digit in arrangement:
                    value = value * 10 + digit
                if cls._is_prime(value):
                    primes.add(value)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        answer = len(primes)
        return RestrictedPrimeToolResult(
            status="success",
            mechanism=cls.mechanism,
            response=f"Reusable exact restricted-digit prime count: {answer}\n\\boxed{{{answer}}}",
            parameters={
                "digits": digits,
                "min_length": minimum,
                "max_length": maximum,
                "candidate_bound": candidate_bound,
                "prime_count": answer,
            },
            elapsed_ms=elapsed_ms,
        )
