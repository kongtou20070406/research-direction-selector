"""The archival viewer retains identities without changing analyzer limits."""
import base64
from copy import deepcopy
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rds_archive_view import load_archive, prepare_archive, render_archive_html


class ArchiveViewTest(unittest.TestCase):
    def sample(self):
        return {'nodes': [{'id': 'a', 'label': '失败原件', 'status': 'UNKNOWN', 'group': 'g'},
                          {'id': 'b'}, {'id': 'isolated'}],
                'edges': [{'id': 'e1', 'source': 'a', 'target': 'b'},
                          {'id': 'e2', 'source': 'a', 'target': 'b'}],
                'groups': [{'id': 'g', 'label': '研究线'}]}

    def test_parallel_edges_and_isolates_preserved_without_mutation(self):
        data = self.sample(); before = deepcopy(data)
        result = prepare_archive(data)
        self.assertEqual(data, before)
        self.assertEqual(result['nodes'], data['nodes'])
        self.assertEqual(result['edges'], data['edges'])
        self.assertEqual(result['counts'], {'nodes': 3, 'edges': 2, 'groups': 2})
        self.assertEqual(result['display_groups'], ['g', 'display:ungrouped', 'display:ungrouped'])

    def test_beyond_previous_node_edge_and_incidence_bounds(self):
        data = {'nodes': [{'id': f'n{i}'} for i in range(5000)],
                'edges': [{'id': f'e{i}', 'source': f'n{i % 5000}',
                           'target': f'n{(i + 1) % 5000}'} for i in range(34000)]}
        result = prepare_archive(data)
        self.assertEqual(len(result['nodes']), 5000)
        self.assertEqual(len(result['edges']), 34000)
        self.assertTrue(all(v is None for v in result['display_limits'].values()))

    def test_duplicate_ids_and_missing_endpoints_rejected(self):
        for kind in ('nodes', 'edges', 'groups'):
            data = self.sample(); data[kind].append(dict(data[kind][0]))
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'Duplicate'):
                prepare_archive(data)
        data = self.sample(); data['edges'][0]['target'] = 'absent'
        with self.assertRaisesRegex(ValueError, 'Missing edge endpoint'):
            prepare_archive(data)

    def test_unknown_group_rejected(self):
        data = self.sample(); data['nodes'][0]['group'] = 'not-declared'
        with self.assertRaisesRegex(ValueError, 'Unknown group'):
            prepare_archive(data)

    def test_fallback_group_does_not_collide(self):
        data = self.sample(); data['groups'].append({'id': 'display:ungrouped'})
        result = prepare_archive(data)
        self.assertEqual(len({g['id'] for g in result['groups']}), len(result['groups']))
        self.assertEqual(result['display_groups'][1], 'display:ungrouped:other')

    def test_embedded_hostile_label_is_only_data(self):
        data = self.sample(); data['nodes'][0]['label'] = '</script><img src=x onerror=alert(1)>'
        html, counts = render_archive_html(data)
        self.assertNotIn(data['nodes'][0]['label'], html)
        packed = html.split('type="application/octet-stream">', 1)[1].split('</script>', 1)[0]
        restored = json.loads(gzip.decompress(base64.b64decode(packed)))
        self.assertEqual(restored['nodes'], data['nodes'])
        self.assertEqual(restored['edges'], data['edges'])
        self.assertEqual(counts['edges'], 2)

    def test_large_input_gzip_and_strict_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.json.gz'
            data = self.sample(); data['nodes'][0]['summary'] = 'x' * (8 * 1024 * 1024 + 1)
            path.write_bytes(gzip.compress(json.dumps(data).encode()))
            self.assertEqual(load_archive(path), data)
            for raw in (b'{"nodes":[],"nodes":[]}', b'{"x":NaN}'):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    load_archive(path)

    def test_cli_read_only_and_rejects_ledger_output(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/rds_archive_view.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source.json'; output = root / 'view.html'
            original = json.dumps(self.sample()).encode(); source.write_bytes(original)
            ledger = root / '.rds'; ledger.mkdir(); sentinel = ledger / 'project.sqlite3'
            sentinel.write_bytes(b'unchanged-sentinel')
            run = subprocess.run([sys.executable, '-B', str(script), '--archive', str(source),
                                  '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)['counts']['nodes'], 3)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(sentinel.read_bytes(), b'unchanged-sentinel')
            blocked = subprocess.run([sys.executable, '-B', str(script), '--archive', str(source),
                                      '--output', str(ledger / 'view.html')], capture_output=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertFalse((ledger / 'view.html').exists())


if __name__ == '__main__':
    unittest.main()
