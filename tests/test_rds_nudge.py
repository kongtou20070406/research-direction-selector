"""Acceptance tests for research notes, reminders, delivery and the research map."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rds_notes
import rds_nudge
import rds_research_map

CLI = ROOT / "scripts" / "rds_cli.py"
HOOK = ROOT / "scripts" / "rds_conversation_hook.py"


def utf8_env():
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, kind, text="x", **fields):
        return rds_notes.append(self.root, kind, text, **fields)

    def rules(self, status="OPEN"):
        return {n["rule"] for n in rds_nudge.evaluate(rds_notes.read(self.root)) if n["status"] == status}


class NoteTests(Base):
    def test_read_without_ledger_creates_nothing(self):
        self.assertEqual(rds_notes.read(self.root), [])
        self.assertFalse((self.root / ".rds").exists())

    def test_append_uses_one_project_ledger(self):
        note = self.add("observe", "m", metric="psnr", value=1.5)
        self.assertEqual(rds_notes.read(self.root)[0]["id"], note["id"])
        self.assertEqual(sorted(p.name for p in (self.root / ".rds").glob("*.sqlite3")), ["project.sqlite3"])

    def test_bad_input_is_refused(self):
        for args, fields in ((("nope", "x"), {}), (("observe", "x"), {"bogus": 1}), (("observe", "y" * 2001), {}),
                             (("answer", "x"), {"key": "k"}), (("dismiss", "x"), {"key": "k"})):
            with self.subTest(args=args[0], fields=fields), self.assertRaises(ValueError):
                rds_notes.append(self.root, *args, **fields)


class RuleTests(Base):
    def test_clean_record_fires_nothing(self):
        self.add("goal", "g", metric="psnr", scale="4k")
        self.add("observe", "o", metric="psnr", scale="4k", value=1.0, endpoint=True)
        self.add("attempt", "a", route="r", outcome="fail")
        self.assertEqual(self.rules(), set())

    def test_expect_mismatch_needs_a_tolerance(self):
        self.add("expect", "rise", metric="psnr", value=30)
        self.add("observe", "o", metric="psnr", value=27)
        notes = rds_notes.read(self.root)
        self.assertNotIn("expect_mismatch", self.rules())
        self.assertTrue(rds_nudge.status(notes)["unknown"])

    def test_expect_mismatch_fires_only_outside_tolerance(self):
        self.add("expect", "rise", metric="psnr", value=30, tolerance=0.5)
        self.add("observe", "near", metric="psnr", value=30.2)
        self.assertNotIn("expect_mismatch", self.rules())
        self.add("observe", "far", metric="psnr", value=27)
        self.assertIn("expect_mismatch", self.rules())

    def test_repeat_route(self):
        for _ in range(2):
            self.add("attempt", "a", route="gate", outcome="fail")
        self.assertNotIn("repeat_route", self.rules())
        self.add("attempt", "a", route="gate", outcome="fail")
        self.assertIn("repeat_route", self.rules())

    def test_repeat_route_cleared_by_another_route(self):
        self.add("attempt", "a", route="gate", outcome="fail")
        self.add("attempt", "a", route="other", outcome="fail")
        self.add("attempt", "a", route="gate", outcome="fail")
        self.add("attempt", "a", route="gate", outcome="fail")
        self.assertNotIn("repeat_route", self.rules())

    def test_unbound_claim(self):
        self.add("claim", "story")
        self.assertIn("unbound_claim", self.rules())

    def test_bound_claim_is_quiet(self):
        o = self.add("observe", "m", metric="psnr", value=1)
        self.add("claim", "story", ref=[o["id"]])
        self.assertNotIn("unbound_claim", self.rules())

    def test_claim_bound_to_another_claim_still_fires(self):
        c = self.add("claim", "first", ref=[self.add("observe", "m", value=1)["id"]])
        self.add("claim", "second", ref=[c["id"]])
        self.assertIn("unbound_claim", self.rules())

    def test_proxy_metric_and_scale_gap(self):
        self.add("goal", "g", metric="psnr", scale="4k")
        self.add("observe", "o", metric="flops", value=1, scale="256")
        self.assertIn("scale_gap", self.rules())
        self.assertNotIn("proxy_metric", self.rules())
        self.add("observe", "o", metric="flops", value=2, scale="4k")
        self.assertIn("proxy_metric", self.rules())

    def test_aggregate_and_constraint(self):
        self.add("observe", "mean", metric="m", value=1, aggregate=True)
        self.add("constraint", "keep qkv", source="self")
        self.add("constraint", "deadline", source="user")
        open_rules = [n for n in rds_nudge.evaluate(rds_notes.read(self.root)) if n["status"] == "OPEN"]
        self.assertEqual(sorted(n["rule"] for n in open_rules), ["aggregate", "unsourced_constraint"])

    def test_endpoint_unchecked(self):
        self.add("goal", "g")
        for _ in range(3):
            self.add("observe", "mid", metric="m", value=1)
        self.assertIn("endpoint_unchecked", self.rules())
        self.add("observe", "end", metric="m", value=1, endpoint=True)
        self.assertNotIn("endpoint_unchecked", self.rules())

    def test_question_quotes_the_anchor(self):
        self.add("claim", "attention makes the high frequency")
        nudge = rds_nudge.evaluate(rds_notes.read(self.root))[0]
        self.assertIn("attention makes the high frequency", nudge["question"])
        self.assertEqual(rds_nudge.status(rds_notes.read(self.root))["assurance"],
                         "RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION")


class ClosureTests(Base):
    def open_key(self):
        self.add("claim", "story")
        return rds_nudge.evaluate(rds_notes.read(self.root))[0]["key"]

    def test_answer_needs_a_later_observation(self):
        early = self.add("observe", "before", metric="m", value=1)
        key = self.open_key()
        count = len(rds_notes.read(self.root))
        for ref in (early["id"], "n999"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                rds_nudge.answer(self.root, key, ref)
        attempt = self.add("attempt", "a", route="r", outcome="ok")
        with self.assertRaises(ValueError):
            rds_nudge.answer(self.root, key, attempt["id"])
        self.assertEqual(len(rds_notes.read(self.root)), count + 1)
        later = self.add("observe", "after", metric="m", value=2)
        rds_nudge.answer(self.root, key, later["id"])
        self.assertIn("unbound_claim", self.rules("ANSWERED"))
        self.assertNotIn("unbound_claim", self.rules())

    def test_dismiss_needs_a_reason_and_known_key(self):
        key = self.open_key()
        for bad_key, reason in ((key, ""), ("missing", "r")):
            with self.subTest(key=bad_key), self.assertRaises(ValueError):
                rds_nudge.dismiss(self.root, bad_key, reason)
        rds_nudge.dismiss(self.root, key, "not a causal claim")
        self.assertIn("unbound_claim", self.rules("DISMISSED"))

    def test_status_is_recomputed_from_notes(self):
        key = self.open_key()
        rds_nudge.dismiss(self.root, key, "r")
        rows = [n for n in rds_notes.read(self.root) if n["type"] == "claim"]
        self.assertEqual(rds_nudge.evaluate(rows)[0]["status"], "OPEN")

    def test_next_for_host_respects_priority_and_reshow(self):
        self.add("claim", "story")
        self.add("expect", "e", metric="m", value=1, tolerance=0.1)
        self.add("observe", "o", metric="m", value=9)
        first = rds_nudge.next_for_host(rds_notes.read(self.root))
        self.assertEqual(first["rule"], "expect_mismatch")
        self.add("shown", key=first["key"])
        second = rds_nudge.next_for_host(rds_notes.read(self.root))
        self.assertEqual(second["rule"], "unbound_claim")
        self.add("shown", key=second["key"])
        self.assertIsNone(rds_nudge.next_for_host(rds_notes.read(self.root)))
        for _ in range(rds_nudge.THRESHOLDS["reshow_after"]):
            self.add("constraint", "c", source="user")
        self.assertEqual(rds_nudge.next_for_host(rds_notes.read(self.root))["rule"], "expect_mismatch")


class CliTests(Base):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(CLI), "--root", str(self.root), *args], text=True,
                              capture_output=True, encoding="utf-8", env=utf8_env(), timeout=60)

    def test_round_trip(self):
        self.assertEqual(self.cli("note", "claim", "故事").returncode, 0)
        result = self.cli("nudge")
        self.assertEqual(result.returncode, 0, result.stderr)
        key = json.loads(result.stdout)["open"][0]["key"]
        note = self.cli("note", "observe", "测到了", "--metric", "m", "--value", "1.5")
        ref = json.loads(note.stdout)["id"]
        self.assertEqual(self.cli("nudge", "answer", key, "--ref", ref).returncode, 0)
        self.assertEqual(json.loads(self.cli("nudge").stdout)["open"], [])
        output = self.root / "map.html"
        self.assertEqual(self.cli("map", "--output", str(output)).returncode, 0)
        self.assertIn("故事", output.read_text(encoding="utf-8"))

    def test_bad_answer_fails_closed(self):
        self.cli("note", "claim", "c")
        key = json.loads(self.cli("nudge").stdout)["open"][0]["key"]
        self.assertNotEqual(self.cli("nudge", "answer", key, "--ref", "n999").returncode, 0)


class HookTests(Base):
    def hook(self, event, **extra):
        payload = {"hook_event_name": event, "cwd": str(self.root), **extra}
        result = subprocess.run([sys.executable, "-B", str(HOOK)], input=json.dumps(payload), text=True,
                                capture_output=True, encoding="utf-8", env=utf8_env(), timeout=10)
        self.assertEqual(result.returncode, 0)
        return result.stdout.strip()

    def shell(self, command, code):
        return self.hook("PostToolUse", tool_name="Bash", tool_input={"command": command},
                         tool_response={"exit_code": code})

    def test_no_ledger_is_silent_and_creates_nothing(self):
        self.assertEqual(self.shell("python train.py --lr 1", 1), "")
        self.assertEqual(self.hook("UserPromptSubmit", prompt="hi"), "")
        self.assertFalse((self.root / ".rds").exists())

    def test_prompt_delivers_one_reminder_and_records_it(self):
        self.add("claim", "story one")
        self.add("constraint", "keep qkv", source="self")
        output = json.loads(self.hook("UserPromptSubmit", prompt="next"))
        self.assertIn("story one", output["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("keep qkv", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(sum(n["type"] == "shown" for n in rds_notes.read(self.root)), 1)

    def test_failures_become_a_repeat_reminder_without_the_command_line(self):
        self.add("goal", "g")
        outputs = [self.shell(f"python sub/train.py --secret-arg {i}", 1) for i in range(3)]
        self.assertIn("train.py", json.loads(outputs[-1])["hookSpecificOutput"]["additionalContext"])
        attempts = [n for n in rds_notes.read(self.root) if n["type"] == "attempt"]
        self.assertEqual([a["route"] for a in attempts], ["train.py"] * 3)
        self.assertTrue(all(a["auto"] and a["outcome"] == "fail" for a in attempts))
        self.assertNotIn("secret-arg", json.dumps(rds_notes.read(self.root)))

    def test_commands_without_a_script_are_not_attempts(self):
        self.add("goal", "g")
        self.shell("git status", 0)
        self.assertFalse([n for n in rds_notes.read(self.root) if n["type"] == "attempt"])


class MapTests(Base):
    def test_graph_has_only_declared_and_route_edges(self):
        o = self.add("observe", "m", value=1)
        self.add("claim", "c", ref=[o["id"]])
        self.add("attempt", "a", route="r", outcome="fail")
        self.add("attempt", "b", route="r", outcome="fail")
        self.add("attempt", "z", route="other", outcome="fail")
        notes = rds_notes.read(self.root)
        graph = rds_research_map.graph(notes, rds_nudge.evaluate(notes))
        self.assertEqual(sorted(e["kind"] for e in graph["edges"]), ["ref", "route"])
        self.assertEqual({n["id"] for n in graph["nodes"]} >= {n["id"] for n in notes}, True)

    def test_page_is_offline_and_escapes_text(self):
        self.add("claim", "</script><script>alert(1)</script>")
        notes = rds_notes.read(self.root)
        page = rds_research_map.render(notes, rds_nudge.evaluate(notes))
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotRegex(page, r"<(script|link)[^>]+(src|href)=[\"']https?:")
        self.assertIn(rds_nudge.evaluate(notes)[0]["key"], page)


if __name__ == "__main__":
    unittest.main()
