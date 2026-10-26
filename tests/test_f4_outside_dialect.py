"""F4 acceptance: a law outside the declared dialect is identified via command.

The world is y = x ** 1.3 (the NewtonBench exponent the declared dialect
rejects: denominator 10 > MAX_ROOT 8). No parseable hypothesis can express it.
A black-box command hypothesis carries the law as a frozen program; the drive
must admit it (F1), replay it against receipts, and report it consistent while
every parseable declared rival is refuted. The certificate and parsed paths are
untouched; scientific_support stays UNKNOWN.
"""
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_model as model
import rds_compass_store as cstore
from rds_compass_declaration import initialize

# The frozen command: reads {"id","inputs"} on stdin, prints {"outputs"}.
LAW = '''import json, sys
row = json.loads(sys.stdin.read())
x = float(row["inputs"]["x"])
print(json.dumps({"outputs": {"y": x ** 1.3}}))
'''


class OutsideDialectWorldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_the_unwritable_law_is_identified_and_the_dialect_rivals_refuted(self):
        root = self.base / 'world'
        fx.law_campaign(root, 'x ** 1.3', range(1, 9),
                        hypotheses=[('linear', 'x'), ('quadratic', 'x * x')])
        fx.write(root / 'law.py', LAW)
        self.assertEqual(initialize(root, root / 'compass.json')['status'], 'INITIALIZED')
        store = cstore.store_for(root)
        for x in range(1, 9):
            manifest = compass.manifest(compass.fold(store), 'x%d' % x)
            store.register(manifest)
            store.execute(manifest['id'])
        state, _ = compass.ingest(store)
        # The declared dialect rivals are refuted by the receipts.
        self.assertEqual(state['hypotheses']['linear']['status'], 'REFUTED')
        self.assertEqual(state['hypotheses']['quadratic']['status'], 'REFUTED')
        # The command hypothesis carrying the true law enters past the parse gate.
        command = {'argv': [sys.executable, '-B', str(root / 'law.py')],
                   'io': {'inputs': ['x'], 'outputs': ['y']}}
        hyp = model.compile_hypothesis(
            {'id': 'true-law', 'predict': {'y': 'x ** 1.3'}, 'command': command},
            ['x'], ['y'])
        tolerances = compass._tolerances(state['plan'])
        result = compass._replay_command(hyp, compass.measured(state),
                                         {k: tolerances[k] for k in hyp['predict'] if k in tolerances})
        # Receipts are float-derived text; the command recomputes the same floats,
        # so with the declared abs:0 tolerance every cell must agree exactly.
        self.assertEqual(result['status'], 'CONSISTENT', result)
        self.assertEqual(result['tested'], 8)
        self.assertEqual(result['scientific_support'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
