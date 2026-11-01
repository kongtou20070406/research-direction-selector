import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rds_notes
from rds_project import ProjectStore


class NotesProjectInitTests(unittest.TestCase):
    def test_note_survives_project_init(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            note = rds_notes.append(root, 'claim', 'before initialization')
            (root / 'protocol.txt').write_bytes(b'protocol')
            contract = {'schema': 1, 'bindings': [{'role': 'protocol', 'path': 'protocol.txt',
                        'sha256': hashlib.sha256(b'protocol').hexdigest()}], 'allowed_commands': [],
                        'output_roots': ['out'], 'budget': {'wall_seconds': 1}}
            contract_path = root / 'contract.json'
            contract_path.write_text(json.dumps(contract), encoding='utf-8')
            cli = Path(__file__).resolve().parents[1] / 'scripts' / 'rds_cli.py'
            result = subprocess.run([sys.executable, '-B', str(cli), '--root', str(root), 'project', 'init', '--mode', 'quick', '--contract', str(contract_path)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(rds_notes.read(root), [note])
            self.assertEqual(ProjectStore(root).contract(), contract)
