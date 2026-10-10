"""Public and native recovery boundaries for the five PR274 review findings."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_jump as jump
import rds_hypergraph_archive as archive
import rds_project_lifecycle as lifecycle
import rds_quick as quick
from rds_project import ProjectStore
import test_rds_quick_checkpoint_recovery as recovery
import test_rds_project_lifecycle as lifecycle_fixture


class StartupAndArchiveTests(unittest.TestCase):
    def test_perl_requires_effective_flag_before_main(self):
        for argv in (['perl', 'worker.pl', '-f'], ['perl', '-I', '-f', 'worker.pl'],
                     ['perl', '-if', 'worker.pl'], ['perl', '--', 'worker.pl']):
            with self.subTest(argv=argv), self.assertRaisesRegex(ValueError, 'pre-main -f'):
                jump._interpreter_script_operand(argv, frozen_startup=True)
        for options in (['-f'], ['-wf'], ['-f', '--'], ['-Ilib', '-f']):
            argv = ['perl', *options, 'worker.pl']
            self.assertEqual(jump._interpreter_script_operand(argv, frozen_startup=True),
                             (len(argv) - 1, 'worker.pl'))

    def test_zsh_refuses_even_no_rcs(self):
        for options in ([], ['-f'], ['-f', '+f'], ['-f', '--']):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'zsh startup'):
                jump._interpreter_script_operand(['zsh', *options, 'worker.sh'], frozen_startup=True)

    def test_archive_goal_bound_precedes_iteration(self):
        class UniterableList(list):
            def __iter__(self):
                raise AssertionError('must reject count before materializing')
        graph = {'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': UniterableList(['a', 'b'])}
        with patch.object(archive, 'MAX_NODES', 1), self.assertRaisesRegex(ValueError, 'declared goal limit'):
            archive.build_archive_display(graph)
        graph['goals'] = ['a']
        graph['nodes'] = [{'id': 'a', 'type': 'goal', 'label': 'a'}]
        with patch.object(archive, 'MAX_NODES', 1):
            self.assertEqual(archive.build_archive_display(graph)['goals'], [{'id': 'a', 'label': 'a'}])


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = lifecycle_fixture.ProjectLifecycleTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.init_quick()

    def test_dangling_entry_refuses_ancestor_with_portable_path_evidence(self):
        child = self.f.root / 'child'
        (child / '.rds').mkdir(parents=True)
        ledger = child / '.rds/project.sqlite3'
        original = Path.is_symlink
        # Exercise the real discovery path on Windows without symlink privilege.
        with patch.object(Path, 'is_symlink', autospec=True,
                          side_effect=lambda path: path == ledger or original(path)):
            with self.assertRaisesRegex(ValueError, 'Project ledger escapes'):
                lifecycle.discover(child)

    def test_actual_dangling_link_refuses_ancestor(self):
        child = self.f.root / 'child'
        (child / '.rds').mkdir(parents=True)
        try:
            os.symlink(child / 'missing.sqlite3', child / '.rds/project.sqlite3')
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'Symlink privilege unavailable: {exc}')
        rejected = self.f.call('project', 'discover', root=child, ok=False)
        self.assertIn('Project ledger escapes', rejected.stderr)


class PartialQuickTests(unittest.TestCase):
    def setUp(self):
        self.f = recovery.QuickCheckpointRecoveryTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.f.script('print("original partial QUICK request")\n')

    def recover_boundary(self, boundary, *, plain=False):
        f = self.f.f
        if plain:
            f.initialize_policy_ledger()
            args = recovery.rds_cli.parser().parse_args([
                '--root', str(f.ledger), 'exec', '--name', 'partial-' + boundary, '--timeout', '5',
                '--', sys.executable, '-B', 'probe.py'])
            reviewed = None
        else:
            args, reviewed = self.f.reviewed_request('partial-' + boundary)
        workspace = Path(args.root) / '.rds/exec' / args.name
        observed = {}
        if boundary == 'mkdir':
            original = Path.mkdir
            def interrupt(path, *a, **kw):
                value = original(path, *a, **kw)
                if path == workspace:
                    raise OSError('partial materialization interruption')
                return value
            target, method = Path, 'mkdir'
            replacement = dict(autospec=True, side_effect=interrupt)
        elif boundary == 'request':
            original = os.replace
            def interrupt(source, destination):
                if destination == workspace / 'rds-exec-request.json':
                    raise OSError('partial materialization interruption')
                return original(source, destination)
            target, method = os, 'replace'
            replacement = dict(new=interrupt)
        elif boundary == 'copy':
            original = Path.write_bytes
            def interrupt(path, *a, **kw):
                value = original(path, *a, **kw)
                if path == workspace / 'probe.py':
                    raise OSError('partial materialization interruption')
                return value
            target, method = Path, 'write_bytes'
            replacement = dict(autospec=True, side_effect=interrupt)
        else:
            original = getattr(ProjectStore, boundary)
            def interrupt(store, *a, **kw):
                value = original(store, *a, **kw)
                if store.root == workspace.resolve():
                    raise OSError('partial materialization interruption')
                return value
            target, method = ProjectStore, boundary
            replacement = dict(new=interrupt)
        with patch.dict(os.environ, f.env):
            with patch.object(target, method, **replacement):
                with self.assertRaisesRegex(OSError, 'partial materialization interruption'):
                    quick.execute(args, review=reviewed)
            self.assertTrue(workspace.is_dir())
            parent = ProjectStore(f.ledger)
            budget = deepcopy(parent.snapshot()['budget'])
            observed = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
            if boundary not in {'mkdir', 'request'}:
                (workspace / 'rds-exec-request.json').write_text('{}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'identity is frozen'):
                    quick.execute(args, review=reviewed)
                (workspace / 'rds-exec-request.json').write_bytes(observed[Path('rds-exec-request.json')])
                self.assertEqual(parent.snapshot()['budget'], budget)
            with patch.object(quick, 'choice', side_effect=AssertionError('must preserve original choice')):
                result = quick.execute(args, review=reviewed)
            self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
            self.assertEqual(parent.snapshot()['budget'], budget)
            retained = list(workspace.parent.glob('.partial-' + args.name + '-*'))
            self.assertEqual(len(retained), 1)
            for relative, raw in observed.items():
                self.assertEqual((retained[0] / relative).read_bytes(), raw)
            with parent._db(True) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' AND json_extract(body,'$.job_root')=?",
                                            (str(workspace),)).fetchone()[0], 1)
            child = ProjectStore(workspace)
            before = child.snapshot()
            with patch.object(ProjectStore, 'execute', side_effect=AssertionError('no repeat dispatch')):
                repeated = quick.execute(args, review=reviewed)
            self.assertEqual(repeated['receipt']['sha256'], result['receipt']['sha256'])
            self.assertEqual(repeated['receipt']['run_status'], result['receipt']['run_status'])
            self.assertEqual(child.snapshot(), before)
            # A missing publication marker never permits rerunning paid work.
            with parent._db(True) as db:
                prior = json.loads(db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' AND json_extract(body,'$.job_root')=?", (str(workspace),)).fetchone()['body'])
                class MissingMarker:
                    def execute(self, *a):
                        class Empty:
                            def fetchone(self):
                                return None
                        return Empty()
                # Model a lost parent publication without weakening append-only
                # guards or erasing any of the native execution evidence.
                with self.assertRaisesRegex(ValueError, 'execution evidence|has an attempt'):
                    quick._is_unmaterialized_allowance(MissingMarker(), workspace,
                        json.loads((workspace / 'rds-exec-request.json').read_text(encoding='utf-8')), prior)
            self.assertEqual(parent.snapshot()['budget'], budget)
            self.assertEqual(child.snapshot(), before)

    def test_after_mkdir(self):
        self.recover_boundary('mkdir')

    def test_after_copy(self):
        self.recover_boundary('copy')

    def test_request_publication_interruption(self):
        self.recover_boundary('request')

    def test_after_initialize(self):
        self.recover_boundary('initialize')

    def test_after_register(self):
        self.recover_boundary('register')

    def test_plain_policy_after_register(self):
        self.recover_boundary('register', plain=True)


if __name__ == '__main__':
    unittest.main()
