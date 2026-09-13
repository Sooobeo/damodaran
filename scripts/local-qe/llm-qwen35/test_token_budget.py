"""Synthetic-only budget, byte-preservation, and owned-child lifecycle tests."""
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import token_budget as budget


class FakeChild:
    def __init__(self, timeout=False):
        self.pid = 4242
        self.returncode = None
        self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
        self.calls = []
        self.killed = False
        self.timeout = timeout

    def communicate(self, input=None, timeout=None):
        self.calls.append((input, timeout))
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("synthetic tokenizer", timeout)
        self.returncode = -9 if self.killed else 0
        return b"[10, 20]\r\n", b"synthetic vocabulary diagnostic\r\n"

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class FakeOwner:
    process_id = 3131

    def __init__(self, child):
        self.child = child
        self.calls = []

    def spawn(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.child


def probe(child):
    return {"priorityClass": budget.BELOW_NORMAL, "exitCode": child.returncode,
            "processHandleQueried": True}


class TokenBudgetTests(unittest.TestCase):
    def test_exact_strict_budget_boundary(self):
        self.assertTrue(budget.check_budget([1] * 2047)["fitsStrict"])
        equal = budget.check_budget([1] * 2048)
        self.assertEqual(equal["remainingTokens"], 0)
        self.assertFalse(equal["fitsStrict"])
        self.assertFalse(budget.check_budget([1] * 2049)["fitsStrict"])

    def test_invalid_budget_and_token_values_are_not_silently_counted(self):
        for ids in ([], [True], [-1], [1.0], "1,2", None):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                budget.check_budget(ids)
        for context, reserve in ((0, 2048), (4096, 0), (True, 2048), (4096, 1.5)):
            with self.assertRaises(ValueError):
                budget.check_budget([1], context, reserve)

    def test_ids_parser_accepts_native_line_endings_but_no_extra_text(self):
        self.assertEqual(budget.parse_tokenizer_ids(b"[0, 248045, 17]\r\n"), [0, 248045, 17])
        for raw in (b"[1]\nTotal: 1", b"[1] trailing", b"[true]", b"[-1]", b"[1.0]", b"[]",
                    b"[NaN]", b'{"tokens":[1]}', b"\xff", b"[1,]"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                budget.parse_tokenizer_ids(raw)

    def test_command_is_vocabulary_tool_with_exact_byte_controls(self):
        command = budget.tokenizer_command()
        self.assertEqual(Path(command[0]).name, "llama-tokenize.exe")
        for flag in ("--ids", "--no-bos", "--no-escape", "--stdin", "--offline"):
            self.assertIn(flag, command)
        self.assertNotIn("--no-parse-special", command)
        self.assertNotIn("--parse-special", command)
        self.assertNotIn("--predict", command)
        self.assertEqual(command[command.index("--poll") + 1], "0")
        self.assertEqual(command[command.index("--poll-batch") + 1], "0")
        self.assertEqual(command[command.index("--prio") + 1], "-1")
        self.assertEqual(command[command.index("--threads") + 1], "1")

    def test_rendering_preserves_user_text_without_id_or_extra_metadata(self):
        row = {"id": "excluded-synthetic-id", "source": 'A\\nB\n"quoted" 😀',
               "translation": "한글\r\n끝\\n", "context": ""}
        suffix = "<think>\n\n</think>\n\n"
        template = "{{ messages[1].content }}" + suffix
        rendered = budget.render_prompts([row], template)[0]
        self.assertTrue(rendered.endswith(suffix))
        payload = json.loads(rendered[:-len(suffix)])
        self.assertEqual(payload, {key: row[key] for key in ("source", "translation", "context")})
        self.assertNotIn("excluded-synthetic-id", rendered)
        with self.assertRaises(ValueError):
            budget.render_prompts([{**row, "labels": []}], template)

    def test_input_must_have_all_48_rows(self):
        with patch.object(budget.contract, "read_input", return_value=([{}] * 48, "synthetic-hash")):
            self.assertEqual(len(budget.read_plan_inputs()[0]), 48)
        with patch.object(budget.contract, "read_input", return_value=([{}] * 47, "synthetic-hash")):
            with self.assertRaisesRegex(ValueError, "row_count"):
                budget.read_plan_inputs()

    def test_native_stdin_bytes_not_escaped_trimmed_or_given_a_bos(self):
        child = FakeChild()
        owner = FakeOwner(child)
        raw = '첫줄\r\n끝\\n😀\n\n'.encode("utf-8")
        out, err, receipt = budget.run_owned_tokenizer(owner, budget.tokenizer_command(), raw, probe=probe)
        self.assertEqual(child.calls[0][0], raw)
        self.assertEqual(out, b"[10, 20]\r\n")
        self.assertEqual(receipt["stdinSha256"], budget.sha_bytes(raw))
        self.assertTrue(receipt["childStopped"])
        self.assertEqual(receipt["exitCode"], 0)
        self.assertEqual(len(owner.calls), 1)
        kwargs = owner.calls[0][1]
        self.assertEqual(kwargs["creationflags"], budget.BELOW_NORMAL | budget.CREATE_NO_WINDOW)
        self.assertIs(kwargs["stdin"], subprocess.PIPE)
        self.assertNotIn("text", kwargs)
        self.assertNotIn("encoding", kwargs)
        self.assertTrue(child.stdin.closed and child.stdout.closed and child.stderr.closed)
        self.assertFalse(receipt["pollingLoopUsed"])

    def test_timeout_kills_only_owned_child_and_preserves_output(self):
        child = FakeChild(timeout=True)
        owner = FakeOwner(child)
        out, err, receipt = budget.run_owned_tokenizer(owner, budget.tokenizer_command(), b"A", probe=probe)
        self.assertTrue(child.killed)
        self.assertTrue(receipt["timedOut"])
        self.assertTrue(receipt["childStopped"])
        self.assertEqual(receipt["exitCode"], -9)
        self.assertEqual(out, b"[10, 20]\r\n")
        self.assertEqual(len(owner.calls), 1)

    def test_unexpected_priority_stops_owned_child_before_token_input(self):
        child = FakeChild()
        owner = FakeOwner(child)
        def wrong_priority(_child):
            return {"priorityClass": 32, "exitCode": _child.returncode}
        _, _, receipt = budget.run_owned_tokenizer(owner, budget.tokenizer_command(), b"A", probe=wrong_priority)
        self.assertTrue(child.killed)
        self.assertIn("failure", receipt)
        self.assertTrue(receipt["childStopped"])
        self.assertTrue(all(value is None for value, _timeout in child.calls))

    def test_longest_prefix_including_short_rows_and_adjacent_differences(self):
        self.assertEqual(budget.longest_common_prefix([[1, 2, 3], [1, 2]]), 2)
        self.assertEqual(budget.longest_common_prefix([[1], [2]]), 0)
        self.assertEqual(budget.longest_common_prefix([[], [1]]), 0)
        audit = budget.shared_prefix_audit([[1, 2, 3], [1, 2, 4], [1, 5]], [{}, {}, {}])
        self.assertEqual(audit["allRowsLongestCommonPrefixTokens"], 1)
        self.assertEqual(audit["adjacentMinimumCommonPrefixTokens"], 1)
        self.assertEqual(audit["adjacentMaximumCommonPrefixTokens"], 2)
        self.assertFalse(audit["cacheReuseDemonstrated"])
        self.assertFalse(audit["recurrentCheckpointStateObserved"])

    def test_boundary_offsets_use_exact_utf8_bytes_and_actual_boundary_ids(self):
        prompt = "<|im_start|>system\n한글😀<|im_end|>\n<|im_start|>user\nA<|im_end|>\n<|im_start|>assistant\n"
        ids = [100, 1, 2, 101, 3, 100, 4, 101, 3, 100, 5]
        result = budget.boundary_positions(prompt, ids, {"<|im_start|>": 100, "<|im_end|>": 101})
        self.assertEqual(result["systemEndBoundaryTokenIndex"], 3)
        self.assertEqual(result["firstUserBoundaryTokenIndex"], 5)
        raw = prompt.encode("utf-8")
        self.assertEqual(raw[result["firstUserBoundaryByteOffset"]:], b"<|im_start|>user\nA<|im_end|>\n<|im_start|>assistant\n")
        with self.assertRaises(ValueError):
            budget.boundary_positions(prompt, ids + [100], {"<|im_start|>": 100, "<|im_end|>": 101})

    def test_technical_parity_reads_only_six_allowed_files_and_preserves_bytes(self):
        prompts = ["prompt\r\n", "prompt\\n", "한글😀\n\n"]
        permitted = {}
        for index, prompt in enumerate(prompts, 1):
            permitted[f"{index:04d}-template.raw.json"] = json.dumps({"prompt": prompt}).encode("utf-8")
            permitted[f"{index:04d}-tokenize.raw.json"] = json.dumps({"tokens": [index, 100]}).encode("utf-8")
        calls = []
        def read(path):
            calls.append(path.name)
            self.assertIn(path.name, permitted)
            return permitted[path.name]
        with patch.object(Path, "read_bytes", read):
            matches, ids, hashes = budget.verify_technical_templates(prompts)
        self.assertTrue(all(item["templateBytesEqual"] for item in matches))
        self.assertEqual(ids, [[1, 100], [2, 100], [3, 100]])
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(hashes), 6)
        with patch.object(Path, "read_bytes", read):
            changed = budget.verify_technical_templates([prompts[0].rstrip(), *prompts[1:]])[0]
        self.assertFalse(changed[0]["templateBytesEqual"])


if __name__ == "__main__":
    unittest.main()
