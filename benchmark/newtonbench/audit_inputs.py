"""Amendment 5 audit: does NewtonBench's simulator read every input the Compass harness passes?

    python -B audit_inputs.py --out results/input-audit.md

For every formal cell and every ``discovered_law`` parameter, the parameter is varied over its declared design
levels from every design point and the noise-free vanilla_equation output is compared. A parameter whose
variation changes nothing from any design point is ignored. Two harnesses are audited: the formal run's, which passed
the signature names, and the amended one, which passes ``common.simulator_name``. A parameter the ground-truth law
itself does not depend on is ignored under both; that is the law, not the harness.
"""
import argparse
import importlib
import sys
import types
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402


def load(module):
    if str(common.NEWTONBENCH) not in sys.path:
        sys.path.insert(0, str(common.NEWTONBENCH))
        stub = types.ModuleType('utils.call_llm_api')
        stub.call_llm_api = lambda *_a, **_k: None
        import utils  # noqa: F401
        sys.modules['utils.call_llm_api'] = stub
    return importlib.import_module('modules.' + module)


def audit(cell, rename):
    module = load(cell['module'])
    sig = module.FUNCTION_SIGNATURE
    names = [p.strip() for p in sig[sig.index('(') + 1:sig.rindex(')')].split(',') if p.strip()]
    _, points = common.design(cell['module'], names)

    def run(point):
        kwargs = {(common.simulator_name(cell['module'], n) if rename else n): float(v) for n, v in point.items()}
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                return repr(float(module.run_experiment_for_module(
                    **kwargs, noise_level=0.0, difficulty=cell['difficulty'], system='vanilla_equation',
                    law_version=cell['version'])))
        except Exception as exc:  # noqa: BLE001 - an exception is an outcome of the audit, not of the harness
            return 'EXCEPTION ' + type(exc).__name__

    ignored = []
    for name in names:
        levels = sorted({p[name] for p in points})
        if all(len({run(dict(base, **{name: level})) for level in levels}) == 1 for base in points):
            ignored.append(name)
    return names, ignored, len({run(p) for p in points})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    lines = ['# Simulator input audit (amendment 5)', '',
             'Noise-free vanilla_equation output over the 64 declared design points. "Ignored" = varying that input '
             'alone over its design levels, from any design point, never changes the output. An input the '
             'ground-truth law does not depend on is ignored under both harnesses.', '',
             '| Cell | Inputs | Ignored, formal-run harness | Distinct outputs | Ignored, amended harness | '
             'Distinct outputs |', '|---|---|---|---|---|---|']
    for cell in common.formal_cells():
        names, old, old_distinct = audit(cell, rename=False)
        _, new, new_distinct = audit(cell, rename=True)
        lines.append(f"| `{common.task_key(cell)}` | {', '.join(names)} | {', '.join(old) or '-'} | {old_distinct} | "
                     f"{', '.join(new) or '-'} | {new_distinct} |")
    Path(args.out).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
