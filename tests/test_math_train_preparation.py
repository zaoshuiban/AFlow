"""Data provenance/filtering tests: no downloads and no model calls."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.operator_library import stable_problem_id
from scripts.prepare_independent_math_train import (
    compact_key, filter_train, scan_history, task_references,
)


def row(problem, answer="1", *, source_id="train/1", split="train"):
    return {"problem": problem, "solution": r"\boxed{" + answer + "}",
            "type": "Algebra", "level": "Level 1", "source_id": source_id, "split": split}


class MathTrainPreparationTests(unittest.TestCase):
    def test_original_test_exact_and_format_overlap_are_excluded(self):
        train = [row("Find $x+1$.", source_id="train/1"),
                 row("Find x + 2.", source_id="train/2"),
                 row("Untouched independent question", source_id="train/3")]
        test = [row("Find $x+1$.", split="test"), row("Find $x+2$.", split="test")]
        kept, excluded, counts = filter_train(train, test, {"exact": {}, "compact": {}})
        self.assertEqual([r["source_id"] for r in kept], ["train/3"])
        self.assertEqual(counts["source_test_exact_overlap"], 1)
        self.assertEqual(counts["source_test_format_overlap"], 1)
        self.assertEqual(len(excluded), 2)
        for field in ("problem", "solution", "type", "level", "source_id", "split"):
            self.assertEqual(kept[0][field], train[2][field])

    def test_history_is_excluded_by_id_or_normalized_text(self):
        history = {"exact": {stable_problem_id("Previously used Q1"): {"old.json"}},
                   "compact": {compact_key("Previously used $Q2$"): {"old.csv"}}}
        kept, excluded, counts = filter_train(
            [row("Previously used Q1"), row("Previously used Q2")], [], history,
        )
        self.assertEqual(kept, [])
        self.assertEqual(counts, {"historical_task_overlap": 2})
        self.assertEqual([r["history_paths"] for r in excluded], [["old.json"], ["old.csv"]])

    def test_duplicates_and_conflicting_references_are_quarantined(self):
        kept, excluded, counts = filter_train([
            row("Same Q", "1", source_id="a"), row("Same Q", "1", source_id="b"),
            row("Conflict Q", "1", source_id="c"), row("Conflict Q", "2", source_id="d"),
            row("Empty answer", "", source_id="e"),
        ], [], {"exact": {}, "compact": {}})
        self.assertEqual([r["source_id"] for r in kept], ["a"])
        self.assertEqual(len(excluded), 4)
        self.assertEqual(counts, {"duplicate_training_question": 1,
                                  "duplicate_question_reference_conflict": 2,
                                  "empty_reference_answer": 1})

    def test_non_training_row_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Non-train"):
            filter_train([row("Q", split="test")], [], {"exact": {}, "compact": {}})

    def test_recursive_scan_covers_csv_jsonl_and_nested_json_not_model_answers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "nested.json").write_text(json.dumps({"results": [
                {"question": "Q1", "prediction": "not a question", "solution": "also not a question"},
                {"problem_id": stable_problem_id("ID only")},
            ]}))
            (root / "lines.jsonl").write_text(json.dumps({"problem": "Q2"}) + "\ninvalid\n")
            (root / "rows.csv").write_text('question,score\n"Q3",1\n')
            ignored = root / "ignored.json"
            ignored.write_text(json.dumps({"problem": "do not scan"}))
            refs, audit = scan_history([root], {ignored.resolve()})
            self.assertEqual(set(refs["exact"]), {stable_problem_id(q) for q in ("Q1", "Q2", "Q3", "ID only")})
            self.assertEqual(audit["file_count"], 3)
            self.assertEqual(audit["files_with_parse_errors"], 1)
            self.assertEqual(audit["task_reference_count"], 4)

    def test_only_recognized_fields_and_id_format_are_collected(self):
        self.assertEqual(list(task_references({"prediction": "Q", "problem_id": "abc"})), [])
        self.assertEqual(compact_key(r"Find $\left(x+1\right)$"), compact_key("Find (x + 1)"))


if __name__ == "__main__":
    unittest.main()
