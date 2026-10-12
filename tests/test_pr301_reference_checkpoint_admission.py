"""Reference checkpoint saves must share native reference writer admission."""
import argparse
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

import rds_cli as cli
from rds_checkpoints import read_checkpoint, restore_checkpoint, save_checkpoint
from rds_project import ProjectStore
from rds_workspace import bind, pointer_path
import test_rds_project


class ReferenceCheckpointAdmissionTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store, self.contract = helper.root, helper.store, helper.contract

    def call(self, root, *arguments):
        return subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
             '--root', str(root), *map(str, arguments)],
            cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=20,
            env={**os.environ, 'RDS_USAGE_LOG': '0'})

    def reference(self, root, *, quiescent=False):
        root.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / 'examples/reference-run').iterdir():
            if source.is_file():
                shutil.copyfile(source, root / source.name)
        state = cli.RDSState(root)
        cli.cmd_init(argparse.Namespace(contract=str(root / 'contract.json')), state)
        for arguments in (('hypothesis', 'add', '--spec', root / 'hypothesis.json'),
                          ('plan', 'create', '--spec', root / 'plan.json')):
            result = self.call(root, *arguments)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with state.snapshot() as (_, snapshot):
            self.assertEqual(snapshot['budget']['reserved']['runs'], 1)
            self.assertEqual(snapshot['plans']['P1']['run_status'], 'RESERVED')
        if quiescent:
            cli.cmd_cancel(argparse.Namespace(id='P1'), state)
            with state.snapshot() as (_, snapshot):
                self.assertEqual(snapshot['budget']['reserved']['runs'], 0)
        return state, snapshot

    def retained(self, *states):
        paths = [self.store.path, pointer_path(self.root)]
        for state in states:
            paths.extend((state.db_path, state.directory / 'project.sqlite3', pointer_path(state.root)))
        result = {}
        for path in paths:
            if path in result:
                continue
            raw = path.read_bytes() if path.is_file() else None
            dump = None
            if raw is not None and path.suffix == '.sqlite3':
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    dump = tuple(db.iterdump())
            result[path] = (raw, dump)
        return result

    def refuse(self, state, snapshot, message, *, command=False):
        before = self.retained(state)
        try:
            if command:
                result = self.call(state.root, 'checkpoint', 'save', '--kind', 'reference', '--id', 'blocked')
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(message, result.stdout + result.stderr)
            else:
                with self.assertRaisesRegex(ValueError, message):
                    save_checkpoint(state.root, 'blocked', snapshot, kind='reference')
        finally:
            # Compare every SQL row (budgets, plans/runs, events, receipts and
            # checkpoints), schema, database bytes and both effective pointers.
            self.assertEqual(self.retained(state), before)

    def local_bound(self, *, command=False):
        state, snapshot = self.reference(self.root, quiescent=True)
        bind(self.root)
        self.refuse(state, snapshot, 'reference accounting is refused', command=command)

    def test_direct_bound_reference_save_is_refused_without_changes(self):
        self.local_bound()

    def test_real_cli_bound_reference_save_is_refused_without_changes(self):
        self.local_bound(command=True)

    def ancestor_bound(self, *, command=False, nested_project=False):
        child = self.root / 'legacy-reference'
        state, snapshot = self.reference(child)
        if nested_project:
            for binding in self.contract['bindings']:
                shutil.copyfile(self.root / binding['path'], child / binding['path'])
            ProjectStore(child).initialize(deepcopy(self.contract))
        with self.assertRaisesRegex(ValueError, 'active descendant'):
            bind(self.root)
        cli.cmd_cancel(argparse.Namespace(id='P1'), state)
        with state.snapshot() as (_, snapshot):
            self.assertEqual(snapshot['budget']['reserved']['runs'], 0)
        bind(self.root)
        message = 'identity does not match' if nested_project else 'bound to a project ledger that is now missing'
        self.refuse(state, snapshot, message, command=command)

    def test_direct_reference_save_inherits_ancestor_admission(self):
        self.ancestor_bound()

    def test_real_cli_reference_save_inherits_ancestor_admission(self):
        self.ancestor_bound(command=True)

    def test_direct_preexisting_nested_project_cannot_hide_binding(self):
        self.ancestor_bound(nested_project=True)

    def test_real_cli_preexisting_nested_project_cannot_hide_binding(self):
        self.ancestor_bound(command=True, nested_project=True)

    def test_bound_reference_is_refused_before_opening_writer(self):
        state, snapshot = self.reference(self.root, quiescent=True)
        bind(self.root)
        original = sqlite3.connect
        writers = []

        def observed(target, *args, **kwargs):
            if '?mode=ro' not in str(target):
                writers.append(str(target))
            return original(target, *args, **kwargs)

        try:
            with patch('rds_checkpoints.sqlite3.connect', side_effect=observed):
                self.refuse(state, snapshot, 'reference accounting is refused')
        finally:
            self.assertEqual(writers, [])

    def test_workspace_identity_damage_is_checked_before_reference_refusal(self):
        state, snapshot = self.reference(self.root, quiescent=True)
        bind(self.root)
        pointer = json.loads(pointer_path(self.root).read_text(encoding='utf-8'))
        pointer['event_digest'] = '0' * 64
        pointer_path(self.root).write_text(json.dumps(pointer), encoding='utf-8')
        self.refuse(state, snapshot, 'identity does not match')

    def test_unbound_direct_reference_save_is_preserved(self):
        state, snapshot = self.reference(self.root)
        result = save_checkpoint(self.root, 'unbound', snapshot, kind='reference')
        self.assertEqual(result['status'], 'SAVED')
        with state.snapshot() as (_, live):
            self.assertEqual(live, snapshot)
        self.assertEqual(restore_checkpoint(self.root, 'unbound', snapshot, kind='reference')['status'],
                         'RESUMABLE_HANDOFF')
        self.assertFalse(pointer_path(self.root).exists())

    def test_unbound_real_cli_reference_save_is_preserved(self):
        state, snapshot = self.reference(self.root)
        result = self.call(self.root, 'checkpoint', 'save', '--kind', 'reference', '--id', 'unbound-cli')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'SAVED')
        with state.snapshot() as (_, live):
            self.assertEqual(live, snapshot)
        self.assertFalse(pointer_path(self.root).exists())

    def test_bound_reference_checkpoint_readers_remain_readonly(self):
        state, snapshot = self.reference(self.root, quiescent=True)
        save_checkpoint(self.root, 'retained', snapshot, kind='reference')
        bind(self.root)
        before = self.retained(state)
        with state.snapshot() as (db, live):
            record = read_checkpoint(db, 'retained', root=self.root)
            self.assertEqual(record['record']['snapshot'], snapshot)
            self.assertEqual(live, snapshot)
        restored = restore_checkpoint(self.root, 'retained', snapshot, kind='reference')
        self.assertEqual(restored['status'], 'RESUMABLE_HANDOFF')
        result = self.call(self.root, 'checkpoint', 'restore', '--kind', 'reference', '--id', 'retained')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'RESUMABLE_HANDOFF')
        self.assertEqual(self.retained(state), before)


if __name__ == '__main__':
    unittest.main()
