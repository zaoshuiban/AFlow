import inspect
import re
from math import isclose
from typing import Any, Callable, List, Optional, Tuple

import regex
from sympy import N, factorial, pi, simplify, sqrt
from sympy.parsing.latex import parse_latex
from sympy.parsing.sympy_parser import parse_expr
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from benchmarks.benchmark import BaseBenchmark
from scripts.logs import logger


class MATHBenchmark(BaseBenchmark):
    SCORER_VERSION = "math_equivalence_v10"

    def __init__(self, name: str, file_path: str, log_path: str):
        super().__init__(name, file_path, log_path)

    def extract_model_answer(self, text: str) -> str:
        boxed_matches = self.extract_boxed_answers(text)
        if boxed_matches:
            return boxed_matches[-1].strip()

        sentence_end_pattern = r"(?<!\d)[.!?]\s+"
        sentences = re.split(sentence_end_pattern, text)
        sentences = [s.strip() for s in sentences if s.strip()]
        return sentences[-1] if sentences else ""

    @staticmethod
    def extract_boxed_answers(text: str) -> List[str]:
        """Return every top-level boxed answer with arbitrary brace depth."""
        source = str(text)
        answers = []
        cursor = 0
        marker = r"\boxed"

        def escaped(position: int) -> bool:
            backslashes = 0
            position -= 1
            while position >= 0 and source[position] == "\\":
                backslashes += 1
                position -= 1
            return backslashes % 2 == 1

        while True:
            marker_index = source.find(marker, cursor)
            if marker_index < 0:
                break
            brace_index = marker_index + len(marker)
            while brace_index < len(source) and source[brace_index].isspace():
                brace_index += 1
            if brace_index >= len(source) or source[brace_index] != "{":
                # TeX permits a single unbraced token. Do not truncate an
                # ambiguous multi-token expression such as \\boxed 12 or 2+3.
                token = re.match(r"([0-9A-Za-z])(?=\s*(?:\$|$|[.,;](?:\s|$)))", source[brace_index:])
                if token:
                    answers.append(token.group(1))
                cursor = marker_index + len(marker)
                continue
            depth = 0
            end_index = None
            for index in range(brace_index, len(source)):
                char = source[index]
                if char == "{" and not escaped(index):
                    depth += 1
                elif char == "}" and not escaped(index):
                    depth -= 1
                    if depth == 0:
                        end_index = index
                        break
            if end_index is None:
                cursor = brace_index + 1
                continue
            answers.append(source[brace_index + 1:end_index].strip())
            cursor = end_index + 1
        return answers

    @classmethod
    def _unwrap_full_boxed(cls, value: str) -> str:
        text = str(value).strip()
        while text.startswith(r"\boxed"):
            answers = cls.extract_boxed_answers(text)
            if len(answers) != 1:
                break
            marker_end = text.find("{")
            depth = 0
            closing = None
            for index in range(marker_end, len(text)):
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                    if depth == 0:
                        closing = index
                        break
            if closing is None or text[closing + 1:].strip(" .,$"):
                break
            text = answers[0].strip()
        return text

    @staticmethod
    def _unwrap_text_command(value: str) -> str:
        text = str(value).strip()
        match = re.fullmatch(r"\\(?:text|mathrm|operatorname)\{([^{}]*)\}", text)
        return match.group(1).strip() if match else text

    @staticmethod
    def _brace_unbraced_square_roots(value: str) -> str:
        """Canonicalize common TeX shorthand such as ``\\sqrt2``.

        TeX permits a single token after ``\\sqrt`` without braces. Model
        answers also commonly use the same shorthand for a multi-digit
        numeric radicand. The symbolic fallback below expects an explicit
        group, so canonicalize these forms before parsing while leaving
        already grouped roots and optional-index roots unchanged.
        """
        source = str(value)
        output: List[str] = []
        cursor = 0
        marker = r"\sqrt"

        def group_end(start: int) -> Optional[int]:
            if start >= len(source) or source[start] != "{":
                return None
            depth = 0
            for index in range(start, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return index + 1
            return None

        while True:
            marker_index = source.find(marker, cursor)
            if marker_index < 0:
                output.append(source[cursor:])
                break
            output.append(source[cursor:marker_index])
            token_start = marker_index + len(marker)
            while token_start < len(source) and source[token_start].isspace():
                token_start += 1

            if token_start >= len(source) or source[token_start] in "{[":
                output.append(source[marker_index:token_start])
                cursor = token_start
                continue

            token_end: Optional[int] = None
            if source[token_start] == "\\":
                command = re.match(r"\\[A-Za-z]+", source[token_start:])
                if command:
                    token_end = token_start + len(command.group(0))
                    if command.group(0) in {r"\frac", r"\dfrac", r"\tfrac"}:
                        first_end = group_end(token_end)
                        second_end = group_end(first_end) if first_end else None
                        token_end = second_end
            elif source[token_start].isdigit() or source[token_start] == ".":
                number = re.match(r"(?:\d+(?:\.\d*)?|\.\d+)", source[token_start:])
                if number:
                    token_end = token_start + len(number.group(0))
            elif source[token_start].isalpha():
                token_end = token_start + 1

            if token_end is None:
                output.append(source[marker_index:token_start])
                cursor = token_start
                continue
            output.append(marker + "{" + source[token_start:token_end] + "}")
            cursor = token_end
        return "".join(output)

    @classmethod
    def normalize_answer(cls, value: Any) -> str:
        """Remove representational noise while preserving mathematical order."""
        text = cls._unwrap_full_boxed(str(value).strip())
        text = text.strip().strip("$").strip()
        text = text.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
        text = text.replace("\\left", "").replace("\\right", "")
        text = cls._brace_unbraced_square_roots(text)
        # Models commonly protect thousands-separator commas with braces so
        # TeX does not add punctuation spacing: 2{,}875{,}125. Preserve the
        # comma semantics while removing this purely presentational grouping;
        # parse_digits later discards scalar thousands separators.
        text = text.replace("{,}", ",")
        text = re.sub(r"(?<!\\)\\\s+", "", text)
        text = re.sub(r"\\(?:[,;!]|quad\b|qquad\b)", "", text)
        text = re.sub(r"[.,;]\s*$", "", text)
        if re.fullmatch(r"\\\$\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
            text = text[2:].strip()
        # Brace only a single subscript token, retaining its value.
        text = re.sub(r"_([0-9A-Za-z])(?![0-9A-Za-z])", r"_{\1}", text)

        # Terminal units are semantically irrelevant for MATH answers.  Only
        # remove a final text command whose content is a recognized unit, so
        # labels such as \text{MAKE} remain meaningful.
        unit_words = (
            r"(?:(?:square|cubic)\s+)?"
            r"(?:units?|meters?|metres?|centimeters?|centimetres?|"
            r"millimeters?|millimetres?|kilometers?|kilometres?|feet|foot|"
            r"inches?|yards?|miles?|seconds?|minutes?|hours?|degrees?|"
            r"ft|m|cm|mm|km|in|yd|mi|sec|s|hrs?|"
            r"cents?|dollars?|gallons?|percent)"
        )
        terminal_unit = re.compile(
            rf"\s*\\(?:text|mathrm)\{{\s*{unit_words}\s*\}}"
            r"(?:\s*\^\s*\{?[23]\}?)?\s*$",
            re.I,
        )
        while terminal_unit.search(text):
            text = terminal_unit.sub("", text).strip()
        text = re.sub(r"\s*\^\s*\{?\\circ\}?\s*$", "", text)
        text = re.sub(r"\s*\\circ\s*$", "", text)
        text = re.sub(r"\s*\\?%\s*$", "", text)
        text = cls._unwrap_text_command(text)
        text = re.sub(r"\s+", "", text)
        return text

    @classmethod
    def _parse_matrix(cls, value: Any) -> Optional[List[List[str]]]:
        text = cls.normalize_answer(value)
        match = re.fullmatch(
            r"\\begin\{([pbBvV]?matrix)\}(.*?)\\end\{\1\}",
            text,
            re.DOTALL,
        )
        if not match:
            return None
        body = match.group(2)
        rows = re.split(r"\\\\", body)
        matrix = []
        for row in rows:
            if not row:
                continue
            matrix.append([entry for entry in row.split("&")])
        if not matrix or any(not row for row in matrix):
            return None
        width = len(matrix[0])
        if any(len(row) != width for row in matrix):
            return None
        return matrix

    @staticmethod
    def _split_top_level_answers(answer: str) -> List[str]:
        """Split answer lists without splitting coordinates or LaTeX groups."""
        text = str(answer).strip()
        if not text:
            return []
        pairs = {"(": ")", "[": "]", "{": "}"}
        closing = set(pairs.values())
        stack = []
        pieces = []
        start = 0
        index = 0
        while index < len(text):
            char = text[index]
            if char in pairs:
                stack.append(pairs[char])
            elif char in closing and stack and char == stack[-1]:
                stack.pop()
            elif not stack and char in {",", ";"}:
                pieces.append(text[start:index].strip())
                start = index + 1
            elif not stack and text[index:index + 5].lower() == " and ":
                pieces.append(text[start:index].strip())
                start = index + 5
                index += 4
            index += 1
        pieces.append(text[start:].strip())
        return [piece for piece in pieces if piece]

    def _answer_collection(self, text: str) -> List[str]:
        boxed = self.extract_boxed_answers(text)
        if len(boxed) > 1:
            return boxed
        answer = boxed[0] if boxed else self.extract_model_answer(text)
        answer = self._unwrap_text_command(answer)
        if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", answer.strip()):
            return [answer]
        return self._split_top_level_answers(answer)

    def _unordered_answers_equal(
        self, predictions: List[str], references: List[str]
    ) -> bool:
        """Match a small answer multiset using semantic equality."""
        if len(predictions) != len(references) or len(predictions) <= 1:
            return False

        def match(position: int, remaining: Tuple[int, ...]) -> bool:
            if position == len(predictions):
                return not remaining
            for offset, reference_index in enumerate(remaining):
                if self.math_equal(
                    predictions[position], references[reference_index]
                ):
                    if match(
                        position + 1,
                        remaining[:offset] + remaining[offset + 1:],
                    ):
                        return True
            return False

        return match(0, tuple(range(len(references))))

    @classmethod
    def extract_choice_options(cls, problem: str) -> dict[str, str]:
        """Parse a complete, ordered multiple-choice block from a problem.

        This deliberately accepts only explicit ``(A)``-style labels.  A
        partial or duplicated block fails closed so an algebraic ``A`` is
        never guessed to be a choice label.
        """
        text = str(problem or "")
        marker = re.compile(
            r"(?:\\(?:text|textbf|mathrm)\s*\{\s*)?"
            r"\(\s*([A-Z])\s*\)"
            r"(?:\s*\})?"
        )
        matches = list(marker.finditer(text))
        labels = [match.group(1) for match in matches]
        if len(matches) < 2 or len(set(labels)) != len(labels):
            return {}
        expected_labels = [chr(ord("A") + offset) for offset in range(len(labels))]
        if labels != expected_labels:
            return {}
        options: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = text[match.end():end].strip()
            content = re.sub(r"^(?:\$|\\[,;!]|\\quad\b|\\qquad\b|\s)+", "", content)
            content = re.sub(r"(?:\$|\\[,;!]|\\quad\b|\\qquad\b|\s)+$", "", content)
            if not content:
                return {}
            options[match.group(1)] = content
        return options

    @classmethod
    def resolve_choice_answer(cls, answer: str, problem: str) -> tuple[str, Optional[str]]:
        normalized = cls.normalize_answer(answer)
        if re.fullmatch(r"\([A-Z]\)", normalized):
            normalized = normalized[1:-1]
        if not re.fullmatch(r"[A-Z]", normalized):
            return answer, None
        options = cls.extract_choice_options(problem)
        if normalized not in options:
            return answer, None
        return options[normalized], normalized

    def calculate_score(
        self, expected_output: str, prediction: str, problem: str = ""
    ) -> Tuple[int, str]:
        expected_answer = self.extract_model_answer(expected_output)
        predicted_answer = self.extract_model_answer(prediction)
        expected_resolved, _ = self.resolve_choice_answer(expected_answer, problem)
        predicted_resolved, _ = self.resolve_choice_answer(predicted_answer, problem)

        if self.math_equal(predicted_resolved, expected_resolved):
            return 1, predicted_answer

        expected_items = self._answer_collection(expected_output)
        predicted_items = self._answer_collection(prediction)
        if self._unordered_answers_equal(predicted_items, expected_items):
            return 1, predicted_answer
        return 0, predicted_answer

    def math_equal(self, prediction: Any, reference: Any) -> bool:
        prediction_normalized = self.normalize_answer(prediction)
        reference_normalized = self.normalize_answer(reference)

        if prediction_normalized == reference_normalized:
            return True

        # Case is representational for ordinary multi-letter answer labels
        # (Orange/orange), but semantic for one-letter mathematical symbols
        # (A/a, x/X).  Expressions and LaTeX commands stay case-sensitive.
        plain_text = re.compile(r"[^\W\d_]+(?:[ '\N{RIGHT SINGLE QUOTATION MARK}-][^\W\d_]+)*", re.UNICODE)
        if (len(re.findall(r"[^\W\d_]", prediction_normalized, re.UNICODE)) >= 2
                and len(re.findall(r"[^\W\d_]", reference_normalized, re.UNICODE)) >= 2
                and plain_text.fullmatch(prediction_normalized)
                and plain_text.fullmatch(reference_normalized)):
            return prediction_normalized.casefold() == reference_normalized.casefold()

        # A model sometimes puts a complete arithmetic equality chain inside
        # the final box, for example ``15!/4=326918592000``.  In answer
        # position the right-most clause is the asserted final value.  Compare
        # that clause while retaining normal semantic comparison for it.
        prediction_chain = self._split_top_level_equalities(
            prediction_normalized
        )
        reference_chain = self._split_top_level_equalities(reference_normalized)
        if len(prediction_chain) > 1 or len(reference_chain) > 1:
            return self.math_equal(prediction_chain[-1], reference_chain[-1])

        prediction_matrix = self._parse_matrix(prediction_normalized)
        reference_matrix = self._parse_matrix(reference_normalized)
        if prediction_matrix is not None or reference_matrix is not None:
            if prediction_matrix is None or reference_matrix is None:
                return False
            if len(prediction_matrix) != len(reference_matrix):
                return False
            if any(
                len(predicted_row) != len(reference_row)
                for predicted_row, reference_row in zip(
                    prediction_matrix, reference_matrix
                )
            ):
                return False
            return all(
                self.math_equal(predicted_entry, reference_entry)
                for predicted_row, reference_row in zip(
                    prediction_matrix, reference_matrix
                )
                for predicted_entry, reference_entry in zip(
                    predicted_row, reference_row
                )
            )

        try:
            if self.is_digit(prediction_normalized) and self.is_digit(
                reference_normalized
            ):
                prediction_number = self.parse_digits(prediction_normalized)
                reference_number = self.parse_digits(reference_normalized)
                return isclose(
                    prediction_number, reference_number, abs_tol=1e-3
                )
        except:
            pass

        try:
            return self.symbolic_equal(
                prediction_normalized, reference_normalized
            )
        except:
            pass

        return False

    @staticmethod
    def _split_top_level_equalities(value: str) -> List[str]:
        """Split plain top-level equality chains, not grouped expressions."""
        text = str(value)
        pairs = {"(": ")", "[": "]", "{": "}"}
        closing = set(pairs.values())
        stack: List[str] = []
        pieces: List[str] = []
        start = 0
        for index, char in enumerate(text):
            if char in pairs:
                stack.append(pairs[char])
            elif char in closing and stack and char == stack[-1]:
                stack.pop()
            elif char == "=" and not stack:
                previous = text[index - 1] if index else ""
                following = text[index + 1] if index + 1 < len(text) else ""
                if previous not in "<>!" and following != "=":
                    pieces.append(text[start:index].strip())
                    start = index + 1
        pieces.append(text[start:].strip())
        return [piece for piece in pieces if piece]

    def is_digit(self, num):
        return self.parse_digits(num) is not None

    def parse_digits(self, num):
        num = str(num).strip()
        num = num.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
        num = num.replace("\\left", "").replace("\\right", "")
        num = regex.sub(r"\s+", "", num)
        if len(num) >= 2 and num[0] == "{" and num[-1] == "}":
            num = num[1:-1]

        percent = num.endswith("%")
        if percent:
            num = num[:-1]
            if num.endswith("\\"):
                num = num[:-1]

        latex_fraction = re.fullmatch(
            r"([+-]?)\\frac\{([^{}]+)\}\{([^{}]+)\}", num
        )
        if latex_fraction:
            numerator = self.parse_digits(latex_fraction.group(2))
            denominator = self.parse_digits(latex_fraction.group(3))
            if numerator is None or denominator in {None, 0}:
                return None
            value = numerator / denominator
            if latex_fraction.group(1) == "-":
                value = -value
            return value / 100 if percent else value

        simple_fraction = re.fullmatch(
            r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))/"
            r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))",
            num,
        )
        if simple_fraction:
            denominator = float(simple_fraction.group(2))
            if denominator == 0:
                return None
            value = float(simple_fraction.group(1)) / denominator
            return value / 100 if percent else value

        # Commas are thousands separators only after collection splitting has
        # determined that this is a scalar answer.
        num = regex.sub(",", "", num)
        try:
            value = float(num)
            return value / 100 if percent else value
        except:
            pass
        return None

    def symbolic_equal(self, a, b):
        def _balanced_group(text: str, start: int) -> Tuple[str, int]:
            if start >= len(text) or text[start] != "{":
                raise ValueError("expected LaTeX group")
            depth = 0
            for index in range(start, len(text)):
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start + 1:index], index + 1
            raise ValueError("unclosed LaTeX group")

        def _latex_to_sympy_text(value: str) -> str:
            """Translate the scalar LaTeX subset used by MATH answers."""
            text = str(value)

            def replace_commands(source: str) -> str:
                output = []
                cursor = 0
                while cursor < len(source):
                    if source.startswith(r"\frac", cursor):
                        first_start = cursor + len(r"\frac")
                        numerator, after_numerator = _balanced_group(
                            source, first_start
                        )
                        denominator, after_denominator = _balanced_group(
                            source, after_numerator
                        )
                        output.append(
                            "(("
                            + replace_commands(numerator)
                            + ")/("
                            + replace_commands(denominator)
                            + "))"
                        )
                        cursor = after_denominator
                        continue
                    if source.startswith(r"\sqrt", cursor):
                        group_start = cursor + len(r"\sqrt")
                        radicand, after_radicand = _balanced_group(
                            source, group_start
                        )
                        output.append("sqrt(" + replace_commands(radicand) + ")")
                        cursor = after_radicand
                        continue
                    output.append(source[cursor])
                    cursor += 1
                return "".join(output)

            text = replace_commands(text)
            text = text.replace(r"\cdot", "*").replace(r"\times", "*")
            text = text.replace(r"\pi", "pi")
            text = text.replace("^", "**")
            text = text.replace("{", "(").replace("}", ")")
            text = re.sub(r"(?<=[0-9.)])(?=sqrt\(|pi\b|[A-Za-z]\b)", "*", text)
            text = re.sub(r"(?<=\))(?=\()", "*", text)
            text = re.sub(r"(?<=[0-9)])(?=\()", "*", text)
            # ``parse_expr`` does not accept TeX-style postfix factorials.
            # Repeatedly rewrite simple atoms; the loop also handles a
            # parenthesized atom produced by the fraction/root translation.
            factorial_atom = re.compile(
                r"(?P<atom>(?:\d+|[A-Za-z][A-Za-z0-9_]*|\([^()]+\)))!"
            )
            while factorial_atom.search(text):
                text = factorial_atom.sub(
                    lambda match: f"factorial({match.group('atom')})", text
                )
            return text

        def _parse(s):
            converted = None
            try:
                converted = _latex_to_sympy_text(s)
            except (TypeError, ValueError):
                pass
            if converted is not None:
                try:
                    return parse_expr(
                        converted,
                        local_dict={
                            "sqrt": sqrt, "pi": pi, "factorial": factorial,
                        },
                        evaluate=True,
                    )
                except Exception:
                    pass
            try:
                # Default ANTLR parsing accepts a valid prefix of malformed
                # input, e.g. ``1+`` as 1. Require the entire expression.
                parsed = parse_latex(s, strict=True)
                if r"\pi" in s:
                    # ANTLR represents LaTeX pi as an ordinary Symbol, unlike
                    # parse_expr's numeric constant. Bind only explicit pi.
                    parsed = parsed.subs({symbol: pi for symbol in parsed.free_symbols
                                          if symbol.name == "pi"})
                return parsed
            except Exception:
                pass
            try:
                return parse_expr(s)
            except Exception:
                pass
            return s

        a = _parse(a)
        b = _parse(b)

        try:
            if simplify(a - b) == 0:
                return True
        except:
            pass

        try:
            if isclose(N(a), N(b), abs_tol=1e-3):
                return True
        except:
            pass
        return False

    def get_function_code(self, func):
        try:
            source_code = inspect.getsource(func)
            return source_code
        except OSError:
            return "no code"

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(1), retry=retry_if_exception_type(Exception), reraise=True)
    async def _generate_output(self, graph, input_text):
        return await graph(input_text)

    async def evaluate_problem(self, problem: dict, graph: Callable) -> Tuple[str, str, str, int, float]:
        input_text = problem["problem"]
        expected_output = problem["solution"]

        try:
            import asyncio as _asyncio
            output, cost = await _asyncio.wait_for(
                self._generate_output(graph, input_text),
                timeout=120.0  # 2 minutes per problem
            )
            uni_score, extracted_output = self.calculate_score(
                expected_output, output, problem=input_text
            )

            if uni_score == 0:
                self.log_mismatch(
                    input_text,
                    expected_output,
                    output,
                    extracted_output,
                    extract_answer_code=self.get_function_code(self.extract_model_answer),
                )

            return input_text, output, expected_output, uni_score, cost

        except _asyncio.TimeoutError:
            logger.info(f"Problem timed out after 120s. Skipping. Problem: {input_text[:80]}...")
            return input_text, "TIMEOUT", expected_output, 0.0, 0.0
        except Exception as e:
            logger.info(f"Maximum retries reached. Skipping this sample. Error: {e}")
            return input_text, str(e), expected_output, 0.0, 0.0

    def get_result_columns(self) -> List[str]:
        return ["question", "prediction", "expected_output", "score", "cost"]
