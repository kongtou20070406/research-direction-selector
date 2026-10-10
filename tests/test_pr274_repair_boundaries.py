"""Public and native recovery boundaries for the five PR274 review findings."""
from copy import deepcopy
from contextlib import ExitStack
import json
import os
import stat
import struct
import subprocess
from pathlib import Path
import sys
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_jump as jump
import rds_hypergraph_archive as archive
import rds_project_lifecycle as lifecycle
import rds_quick as quick
import rds_campaign as campaign
from rds_project import ProjectStore
import test_rds_quick_checkpoint_recovery as recovery
import test_rds_project_lifecycle as lifecycle_fixture
import test_rds_hypergraph_archive as archive_fixture


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

    def test_damaged_state_directory_entry_cannot_select_ancestor(self):
        for kind in ('file', 'dangling-directory'):
            child = self.f.root / ('child-' + kind)
            child.mkdir()
            state = child / '.rds'
            original = Path.is_symlink
            if kind == 'file':
                state.write_text('retained damaged state entry', encoding='utf-8')
            with self.subTest(kind=kind), patch.object(Path, 'is_symlink', autospec=True,
                    side_effect=lambda path: (kind == 'dangling-directory' and path == state) or original(path)):
                with self.assertRaisesRegex(ValueError, 'State directory escapes'):
                    lifecycle.discover(child)
            if kind == 'file':
                self.assertEqual(state.read_text(encoding='utf-8'), 'retained damaged state entry')

    def test_dangling_entry_refuses_ancestor_with_portable_path_evidence(self):
        for name, label in (('project.sqlite3', 'Project'), ('state.sqlite3', 'Reference')):
            child = self.f.root / ('child-' + name)
            (child / '.rds').mkdir(parents=True)
            ledger = child / '.rds' / name
            original = Path.is_symlink
            # Exercise real discovery on Windows without symlink privilege.
            with self.subTest(name=name), patch.object(Path, 'is_symlink', autospec=True,
                              side_effect=lambda path: path == ledger or original(path)):
                with self.assertRaisesRegex(ValueError, label + ' ledger escapes'):
                    lifecycle.discover(child)

    def test_actual_dangling_link_refuses_ancestor(self):
        for name, label in (('project.sqlite3', 'Project'), ('state.sqlite3', 'Reference')):
            child = self.f.root / ('child-' + name)
            (child / '.rds').mkdir(parents=True)
            try:
                os.symlink(child / 'missing.sqlite3', child / '.rds' / name)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f'Symlink privilege unavailable: {exc}')
            rejected = self.f.call('project', 'discover', root=child, ok=False)
            self.assertIn(label + ' ledger escapes', rejected.stderr)


class ArchiveInputTests(unittest.TestCase):
    def test_zip64_locator_in_comment_cannot_override_normal_directory_bounds(self):
        f = archive_fixture.ArchiveTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        source = f.write_zip(archive_fixture.encoded(archive_fixture.fixture()))
        raw = bytearray(source.read_bytes())
        end = raw.rfind(b'PK\x05\x06')
        last_header = raw.rfind(b'PK\x01\x02', 0, end)
        directory_bytes = struct.unpack_from('<L', raw, end + 12)[0]
        # The normal count/size fields remain below the limits. CPython also
        # interprets these ZIP64 bytes when they sit in the final entry comment.
        record = struct.pack('<4sQ2H2L4Q', b'PK\x06\x06', 44, 45, 45, 0, 0, 3, 3, 2048, 0)
        locator = struct.pack('<4sLQL', b'PK\x06\x07', 0, 0, 1)
        comment = record + locator
        struct.pack_into('<H', raw, last_header + 32, len(comment))
        raw[end:end] = comment
        struct.pack_into('<L', raw, end + len(comment) + 12, directory_bytes + len(comment))
        source.write_bytes(raw)
        with patch.object(archive, 'MAX_ZIP_DIRECTORY_BYTES', 1000), \
                patch.object(archive.zipfile, 'ZipFile', side_effect=AssertionError('ZIP64 parser allocation')):
            with self.assertRaisesRegex(ValueError, 'ZIP64 inventory is unsupported'):
                archive.read_archive(source)

    def test_zip_inventory_limits_precede_parser_allocation(self):
        f = archive_fixture.ArchiveTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        source = f.write_zip(archive_fixture.encoded(archive_fixture.fixture()))
        for variable, limit, error in (
                ('MAX_ZIP_BYTES', source.stat().st_size - 1, 'container exceeds'),
                ('MAX_ZIP_DIRECTORY_BYTES', 1, 'directory exceeds'),
                ('MAX_ZIP_ENTRIES', 2, 'entry count exceeds')):
            with self.subTest(variable=variable), patch.object(archive, variable, limit), \
                    patch.object(archive.zipfile, 'ZipFile', side_effect=AssertionError('unbounded parser allocation')):
                with self.assertRaisesRegex(ValueError, error):
                    archive.read_archive(source)
        with patch.object(archive, 'MAX_ZIP_ENTRIES', 3):
            self.assertEqual(archive.read_archive(source)['graph'], archive_fixture.fixture())

    def test_zip_understated_directory_count_cannot_bypass_real_inventory(self):
        f = archive_fixture.ArchiveTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        source = f.write_zip(archive_fixture.encoded(archive_fixture.fixture()))
        raw = bytearray(source.read_bytes())
        end = raw.rfind(b'PK\x05\x06')
        struct.pack_into('<HH', raw, end + 8, 2, 2)  # Three actual entries remain.
        source.write_bytes(raw)
        with patch.object(archive, 'MAX_ZIP_ENTRIES', 2), \
                patch.object(archive.zipfile, 'ZipFile', side_effect=AssertionError('unbounded parser allocation')):
            with self.assertRaisesRegex(ValueError, 'entry count exceeds'):
                archive.read_archive(source)

    def test_descriptor_type_checked_before_zip_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'graph.json'
            source.write_text('{}', encoding='utf-8')
            with patch.object(archive.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFIFO)), \
                    patch.object(archive.zipfile, 'is_zipfile', side_effect=AssertionError('probe before type check')):
                with self.assertRaisesRegex(ValueError, 'regular file'):
                    archive.read_archive(source)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO unavailable')
    def test_fifo_without_writer_rejected_by_public_reader_with_bounded_child(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'archive.fifo'
            os.mkfifo(source)
            code = 'import sys; sys.path.insert(0,sys.argv[1]); from rds_hypergraph_archive import read_archive; read_archive(sys.argv[2])'
            result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts'), str(source)],
                                    capture_output=True, text=True, timeout=8)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Archive input must be a regular file', result.stderr)

    def test_character_device_rejected_before_read(self):
        with self.assertRaisesRegex(ValueError, 'regular file'):
            archive.read_archive(os.devnull)


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
        # QUICK canonicalizes the source root. Keep a lexical alias in every
        # recovery case so fault injection and ledger queries must use that
        # same identity, including Windows temporary-directory aliases.
        source = Path(args.root)
        args.root = str(source / '..' / source.name)
        workspace = (Path(args.root) / '.rds/exec' / args.name).resolve()
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
            retained = list((Path(args.root).resolve() / '.rds/quick-partials').glob('.partial-' + args.name + '-*'))
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
            if plain:
                # Both real downstream inventories must accept the recovered
                # terminal job while every forensic partial byte stays intact.
                with parent._db(True) as db:
                    targets = list(campaign._retained_targets(parent.root, db,
                        campaign._Inventory(), workspace=parent.root))
                self.assertEqual(set(targets), {workspace})
                with ExitStack() as locks, parent._db(True) as db:
                    self.assertEqual(len(lifecycle._activation_children(parent, db, locks)), 64)
                self.assertEqual(child.snapshot(), before)
                for relative, raw in observed.items():
                    self.assertEqual((retained[0] / relative).read_bytes(), raw)

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

    def test_plain_policy_after_mkdir(self):
        self.recover_boundary('mkdir', plain=True)


if __name__ == '__main__':
    unittest.main()
