"""The on-demand command map must cover the actual public CLI entry points."""
import argparse
import contextlib
import io
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_cli


def leaves(parser, prefix=()):
    groups = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    if not groups:
        yield prefix
    for group in groups:
        seen = set()
        for name, child in group.choices.items():
            if id(child) in seen:
                continue
            seen.add(id(child))
            yield from leaves(child, (*prefix, name))


class SkillRouteTests(unittest.TestCase):
    def test_campaign_setup_and_quick_activation_have_independent_scopes(self):
        text = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        blocks = dict(re.findall(r'^    IF ([^\n]+):\n((?: {8}[^\n]*\n)+)', text, re.M))
        new_campaign = blocks['a new goal-driven campaign has no discovered project']
        activation = blocks['an existing QUICK project needs owned Advisor']
        self.assertIn('campaign-setup.md#new-campaign-after-discovery', new_campaign)
        self.assertIn('campaign-setup.md#enable-advisor-in-an-existing-quick-project', activation)
        for declaration in ('DEFAULT new campaigns TO FULL', 'FREEZE actual objective',
                            'AND their distinguishing observations', 'FULL owns collection/selection'):
            self.assertIn(declaration, new_campaign)
            self.assertNotIn(declaration, activation)
        for guard in ('PREVIEW Advisor activation', 'apply only reviewed policy at the expected snapshot',
                      'KEEP genesis contract', 'old runs/receipts/checkpoints and cumulative budget',
                      'NEVER replace the frozen contract', 'infer extra execution authority from activation'):
            self.assertIn(guard, activation)
            self.assertNotIn(guard, new_campaign)
        self.assertNotIn('PREVIEW Advisor activation', text.replace(activation, ''))

    def test_every_public_leaf_is_documented_and_reachable(self):
        actual = set(leaves(rds_cli.parser()))
        text = (ROOT / "docs/command-map.md").read_text(encoding="utf-8")
        documented = {tuple(path.split()) for path in re.findall(r"^\| `([^`]+)` \|", text, re.M)}
        self.assertEqual(actual - documented, set(), "Undocumented public CLI command")
        # Other tables may contain option forms; canonical command rows may not invent routes.
        self.assertEqual({path for path in documented if path and not any(part.startswith("-") for part in path)
                          and path[0] not in {"python", "python3"}} - actual, set(), "Documented command is unreachable")
        for path in sorted(actual):
            with self.subTest(command=" ".join(path)), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    rds_cli.parser().parse_args([*path, "--help"])
                self.assertEqual(raised.exception.code, 0)

    def test_skill_entry_is_ascii_and_references_the_complete_map(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.isascii(), "The Skill entry is required to be pure English/ASCII")
        paths = re.findall(r"\]\(([^)]+)\)", text)
        self.assertIn("docs/command-map.md", paths)
        for target in paths:
            if "://" not in target:
                self.assertTrue((ROOT / target.split("#", 1)[0]).is_file(), target)


if __name__ == "__main__":
    unittest.main()
