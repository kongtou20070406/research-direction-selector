"""Live-check one selected local capability; no inventory, installs or GPU jobs."""
import argparse
from fractions import Fraction
import importlib
import json
import os
import sys
import time

from rds_reference_values import CAPABILITIES


def probe(capability):
    if capability not in CAPABILITIES:
        raise ValueError('Choose one supported local capability')
    started = time.perf_counter()
    report = {'capability': capability, 'python': sys.executable, 'python_version': sys.version,
              'logical_cpus': os.cpu_count(), 'status': 'UNKNOWN',
              'assurance': 'LOCAL_CAPABILITY_SMOKE_NOT_SCIENTIFIC_VERIFICATION'}
    try:
        if capability == 'python_exact':
            valid = Fraction(1, 3) + Fraction(1, 6) == Fraction(1, 2)
        elif capability == 'rust_native':
            import rds_accelerator as accelerator
            acc_info = accelerator.get_accelerator_status()
            report.update(
                backend=acc_info['backend'],
                native_loaded=acc_info['native_library_loaded'],
                cargo_available=acc_info['cargo_available']
            )
            valid = (acc_info['status'] == 'AVAILABLE')
            if not valid:
                report.update(status='UNAVAILABLE', error='No usable compiled native ABI; cargo presence is reported separately')
                report['wall_seconds'] = round(time.perf_counter() - started, 6)
                return report
        else:
            name = {'sympy_exact': 'sympy', 'mpmath_iv': 'mpmath', 'torch_cpu': 'torch'}[capability]
            module = importlib.import_module(name)
            report.update(module=name, module_path=module.__file__, module_version=getattr(module, '__version__', None))
            if capability == 'sympy_exact':
                valid = module.Rational(1, 3) + module.Rational(1, 6) == module.Rational(1, 2)
            elif capability == 'mpmath_iv':
                value = module.iv.mpf([1, 2]) + module.iv.mpf([3, 4])
                report['smoke_enclosure'] = str(value)
                valid = value.a == 4 and value.b == 6
            else:
                valid = module.tensor([1, 2], dtype=module.int64, device='cpu').sum().item() == 3
        report['status'] = 'AVAILABLE' if valid else 'FAILED_SMOKE'
    except (ImportError, OSError, AttributeError) as exc:
        report.update(status='UNAVAILABLE', error=str(exc))
    except Exception as exc:
        report.update(status='FAILED_SMOKE', error=str(exc))
    report['wall_seconds'] = round(time.perf_counter() - started, 6)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capability', choices=CAPABILITIES, required=True)
    report = probe(parser.parse_args().capability)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report['status'] == 'AVAILABLE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
