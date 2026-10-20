"""Offline anti-unification for Compass operators (plan L1-a: sections 2.1/2.2/2.3/2.5 + section 7).

Least-generalises the successful dict-ASTs of one ``domain_signature`` bucket out of historical
``.rds`` ledgers, admits a template under the 2.2 MDL rule and writes a v2 library record.
Offline only: the online path never imports this module.
"""
from fractions import Fraction
import argparse
import json
from pathlib import Path

import rds_compass_model as model
import rds_compass_store as cstore
from rds_project import canonical, digest

SCHEMA = 'rds-compass-operator-v2'
HOLE = '$'  # model._name rejects this spelling, so a hole name can never collide with a variable.
MAX_NODES = model.MAX_RULEBOOK_NODES  # 64: the instantiated-rulebook bound of 2.2.
OPCODE_BITS = 8
# Admission gate (docs/l1a-blockers-diagnosis.md section 4, recommendation (b)): a template has to be
# both relatively non-lossy (R >= 1.0, the MDL break-even line) and absolutely worth its bookkeeping.
MIN_RATIO = 1.0
# 32 bits is the order of the overhead a template must recover: two constant positions plus one support
# row. Sized from the measured savings in that report's section 4 table, not from a chosen ratio.
MIN_SAVING_BITS = 32
_KINDS = {'CONST_INT': 'C', 'CONST_RATIONAL': 'C', 'VAR': 'V', 'EXPR': 'E'}


def _hole(holes, kind, **extra):
    """Append one hole to the book; the name encodes its kind and 1-based slot."""
    name = HOLE + _KINDS[kind] + str(len(holes) + 1)
    holes.append({'name': name, 'kind': kind, **extra})
    return name


def _nary(nodes, holes):
    """Least general generalisation of one position (2.1: op+arity match, else a hole)."""
    if all(set(node) == {'var'} for node in nodes):
        names = sorted({node['var'] for node in nodes})
        return {'var': names[0]} if len(names) == 1 else {'var': _hole(holes, 'VAR', members=names)}
    if all(set(node) == {'const'} for node in nodes):
        texts = sorted({node['const'] for node in nodes})
        if len(texts) == 1:
            return {'const': texts[0]}
        kind = 'CONST_RATIONAL' if any('/' in text for text in texts) else 'CONST_INT'
        return {'const': _hole(holes, kind, members=texts)}
    first = nodes[0]
    if all(set(node) == {'op', 'args'} and node['op'] == first['op'] and len(node['args']) == len(first['args'])
           for node in nodes):
        return {'op': first['op'], 'args': [_nary([node['args'][i] for node in nodes], holes)
                                            for i in range(len(first['args']))]}
    # An EXPR hole is a whole subtree, not a leaf name (2.1): it stays a dict so every consumer
    # (model.variables_of, _refill, instantiate, _node_bits) sees the same node dialect.
    return {'expr': _hole(holes, 'EXPR', max_nodes=max(model.size(node) for node in nodes))}


def _refill(pattern, node, sigma):
    """Bind ``pattern`` (template) to ``node`` (concrete case), collecting one sigma."""
    if isinstance(pattern, str):
        sigma[pattern] = node
    elif 'expr' in pattern:
        sigma[pattern['expr']] = node
    elif 'var' in pattern and pattern['var'].startswith(HOLE):
        sigma[pattern['var']] = node['var']
    elif 'const' in pattern and pattern['const'].startswith(HOLE):
        sigma[pattern['const']] = node['const']
    else:
        for slot, arg in zip(pattern.get('args') or (), node.get('args') or ()):
            _refill(slot, arg, sigma)
    return sigma


def generalize(asts):
    """Fold lgg over a list of dict-ASTs: template + holes + one sigma per case (2.1 output shape)."""
    asts = [model.normalize(node, None, limit=MAX_NODES) for node in asts]
    if not asts:
        raise ValueError('Anti-unification needs at least one case')
    holes = []
    template = _nary(asts, holes)
    return {'schema': SCHEMA, 'template_ast': template, 'holes': holes,
            'sigmas': [_refill(template, node, {}) for node in asts], 'laws': asts}


def instantiate(template_ast, sigma):
    """Refill a template with one sigma; every hole must be bound."""
    if isinstance(template_ast, str):
        return sigma[template_ast]
    if 'expr' in template_ast:
        return sigma[template_ast['expr']]
    if 'var' in template_ast:
        name = template_ast['var']
        return {'var': sigma[name]} if name.startswith(HOLE) else {'var': name}
    if 'const' in template_ast:
        text = template_ast['const']
        return {'const': sigma[text]} if text.startswith(HOLE) else {'const': text}
    return {'op': template_ast['op'], 'args': [instantiate(arg, sigma) for arg in template_ast['args']]}


def _zigzag(value):
    """2.2 leaves Elias-gamma undefined for 0 and negatives; zigzag folds them onto the positives."""
    return 2 * value + 1 if value >= 0 else -2 * value


def _gamma_bits(value):
    """Elias-gamma length of a positive integer (2.2)."""
    if value < 1:
        raise ValueError('Elias-gamma needs a positive integer')
    return 2 * value.bit_length() - 1


def _const_bits(text):
    """2.2: integers are one Elias-gamma (via zigzag), rationals two (numerator then denominator)."""
    value = Fraction(text)
    if value.denominator == 1:
        return OPCODE_BITS + _gamma_bits(_zigzag(int(value)))
    return OPCODE_BITS + _gamma_bits(_zigzag(value.numerator)) + _gamma_bits(_zigzag(value.denominator))


def _var_bits(nvars):
    """2.2: a variable reference costs ceil(log2(nvars)) bits."""
    return 0 if nvars <= 1 else (nvars - 1).bit_length()


def _name_count(holes):
    """The shared variable-name table of one operator: concrete names plus VAR-hole members."""
    names = set(model.variables_of(holes['laws'][0])) if False else set()
    for hole in holes['book'].values():
        names |= set(hole.get('members') or ())
    return max(1, len(names))


def _node_bits(node, nvars, holes):
    if isinstance(node, str):
        return OPCODE_BITS + _gamma_bits(_zigzag(int(holes[node]['max_nodes']) + 1))
    if 'expr' in node:
        return OPCODE_BITS + _gamma_bits(_zigzag(int(holes[node['expr']]['max_nodes']) + 1))
    if 'var' in node:
        return OPCODE_BITS + _var_bits(nvars)
    if 'const' in node:
        text = node['const']
        if text.startswith(HOLE):
            return OPCODE_BITS + _gamma_bits(_zigzag(len(holes[text].get('members') or ()) + 1))
        # A literal carries its opcode too.  Every other branch already pays it; leaving the
        # constant branch alone made `mdl_bits({'const': '2'})` disagree with `_sigma_bits`
        # for the same value, so template and residual were priced on different scales.
        return OPCODE_BITS + _const_bits(text)
    return OPCODE_BITS + sum(_node_bits(arg, nvars, holes) for arg in node['args'])


def _sigma_bits(sigma, holes, nvars):
    total = 0
    for name, value in sigma.items():
        kind = holes[name]['kind']
        # A constant residual carries its own opcode, like the hole it refills in _node_bits: MDL items
        # must decode on their own, and the reader has to know integer from rational before reading it.
        total += _node_bits(value, nvars, holes) if kind == 'EXPR' else (
            _var_bits(nvars) if kind == 'VAR' else OPCODE_BITS + _const_bits(value))
    return total


def _book(operator):
    return {hole['name']: hole for hole in operator['holes']}


def mdl_bits(law):
    """2.2 bit length of a dict-AST, or of a template AST (holes priced by their own declarations)."""
    if isinstance(law, dict) and 'template_ast' in law:
        holes = _book(law)
        names = set(model.variables_of(law['template_ast']))
        return _node_bits(law['template_ast'], max(1, len(names | _members(holes))), holes)
    return _node_bits(law, max(1, len(set(model.variables_of(law)))), {})


def _members(holes):
    names = set()
    for hole in holes.values():
        names |= set(hole.get('members') or ())
    return names


def _mdl(operator):
    """2.2 ratio: standalone laws over template + residual sigmas + the support index table."""
    holes = _book(operator)
    nvars = max(1, len(set(model.variables_of(operator['template_ast'])) | _members(holes)))
    residual = [_sigma_bits(sigma, holes, nvars) for sigma in operator['sigmas']]
    standalone = [_node_bits(law, nvars, {}) for law in operator['laws']]
    support = len(operator['laws']) * (OPCODE_BITS + _var_bits(len(operator['laws'])))
    total = mdl_bits(operator) + sum(residual) + support
    return {'template_bits': mdl_bits(operator), 'residual_bits': residual, 'support_bits': support,
            'standalone_bits': standalone, 'ratio': round(sum(standalone) / total, 6) if total else 0.0,
            'saving_bits': sum(standalone) - total}


def _non_trivial(operator):
    """2.2 (section 7 rev 1): some refill differs from another case; lgg makes that two cases differ."""
    rebuilt = [canonical(instantiate(operator['template_ast'], sigma)) for sigma in operator['sigmas']]
    return any(any(other != rebuilt[i] for j, other in enumerate(rebuilt) if j != i)
               for i in range(len(rebuilt)))


def admit(template, support):
    """2.2: all three gates. R >= 1.0 and saving >= 32, support >= 2, non-trivial, 64-node refill bound."""
    if len(support) < 2:
        return False
    template['mdl'] = _mdl(template)
    if template['mdl']['ratio'] < MIN_RATIO or template['mdl']['saving_bits'] < MIN_SAVING_BITS:
        return False
    if max(model.size(instantiate(template['template_ast'], s)) for s in template['sigmas']) > MAX_NODES:
        return False
    return _non_trivial(template)


def _env(point, name):
    """A witness point is a whole input environment; a bare value binds ``name`` alone."""
    return point if isinstance(point, dict) else {name: Fraction(point)}


def _runner(nodes, points, name='x'):
    """Default contract runner: exact model.evaluate; an undefined point is None, never an exception."""
    out = []
    for node in nodes:
        row = []
        for point in points:
            try:
                row.append(model.text(model.evaluate(node, _env(point, name))))
            except ValueError:  # DomainError and Undecided are ValueError subclasses.
                row.append(None)
        out.append(row)
    return out


_SWAPS = {'add': 'sub', 'sub': 'add', 'mul': 'add', 'div': 'mod', 'mod': 'div', 'min': 'max', 'max': 'min'}


def _mutants(node, limit=24):
    """Deterministic single-site AST mutants (constants +-1, operator swaps) for 2.3's discriminability."""
    out = []

    def walk(current, replace):
        if len(out) >= limit or isinstance(current, str) or 'var' in current or 'expr' in current:
            return
        if 'const' in current:
            value = Fraction(current['const'])
            if value.denominator == 1:
                out.extend(replace({'const': model.text(value + delta)}) for delta in (1, -1))
            return
        if current['op'] in _SWAPS:
            out.append(replace({'op': _SWAPS[current['op']], 'args': current['args']}))
        for index, arg in enumerate(current['args']):
            walk(arg, lambda replacement, index=index: replace(
                {**current, 'args': [*current['args'][:index], replacement, *current['args'][index + 1:]]}))

    walk(node, lambda replacement: replacement)
    return out[:limit]


def contract(template, support, runner=None):
    """2.3: witness replay, boundary probe, discriminability; any failure keeps the template out."""
    runner = _runner if runner is None else runner
    instances = [instantiate(template['template_ast'], sigma) for sigma in template['sigmas']]
    name = support[0].get('variable', 'x')
    checks, failures, probed = {'witness': [], 'boundary': False, 'discriminable': False}, [], False
    for case, node in zip(support, instances):
        points = [witness['input'] for witness in case['witness']]
        got = runner([node], points, name)[0]
        checks['witness'].append(got == [witness['value'] for witness in case['witness']])
        if not checks['witness'][-1]:
            failures.append({'check': 'WITNESS_REPLAY', 'origin': case.get('origin')})
        if not checks['discriminable']:
            checks['discriminable'] = any(runner([mutant], points, name)[0] != got for mutant in _mutants(node))
        if not probed:
            domain = case.get('domain') or {}
            edge = sorted({int(domain['min']), int(domain['max']), 0, -1, 1, int(domain['max']) + 1})
            invalid = [point for row in runner(instances, edge, name) for point, value in zip(edge, row)
                       if value is not None and not _exact(value)]
            checks['boundary'] = not invalid
            if invalid:
                failures.append({'check': 'BOUNDARY_PROBE', 'points': invalid})
            probed = True
    if not checks['discriminable']:
        failures.append({'check': 'DISCRIMINABILITY', 'reason': 'every mutant reproduced the witness values'})
    return {'ok': not failures, 'failures': failures, 'checks': checks}


def _exact(value):
    try:
        model.exact(value)
        return True
    except ValueError:
        return False


def _decade(value):
    return 0 if not value else len(str(abs(int(value)))) - 1


def _tolerance_bucket(spec):
    """Exact (0), absolute-only (1) or relative (2): the 3.1 bucket over a declared tolerance."""
    zero = {'0', '0.0', '', None}
    return 0 if spec.get('abs') in zero and spec.get('rel') in zero else 1 if spec.get('abs') not in zero else 2


def _num(text):
    """A declared numeric bound.  Devset domains are exact fractions (``1/10``), toy worlds are
    integers; ``int()`` raised on the former and bucketed the two differently for one shape."""
    value = Fraction(str(text))
    return int(value) if value.denominator == 1 else value


def domain_signature(plan):
    """3.1 canonical bucket signature: shapes only, never law content."""
    inputs, tolerances = plan['inputs'], plan.get('tolerances') or {}
    return digest({'inputs': len(inputs),
                   'integer': sorted(bool(spec.get('integer')) for spec in inputs.values()),
                   'span': sorted(_decade(_num(spec['max']) - _num(spec['min'])) for spec in inputs.values()),
                   'tolerance': {name: _tolerance_bucket(spec) for name, spec in sorted(tolerances.items())},
                   'candidates': _decade(len(plan['candidates'])),
                   'operators': digest(sorted((plan.get('grammar') or {}).get('operators') or ()))})


def collect(events, observable):
    """2.1 sources: (a) TOOL_QUALIFIED oracle law_ast, (b) jump-originated HYPOTHESIS predict ASTs."""
    plan, variable = events[0]['value'], next(iter(events[0]['value']['inputs']))
    # A candidate carries every declared input, not just the one this bucket counts as "the" variable:
    # a multi-input law replayed from a single projection raises DomainError("Missing input") and
    # `_runner` would report None for every point, failing a witness that is numerically correct.
    points = {candidate['id']: {key: model.exact(value) for key, value in candidate['inputs'].items()}
              for candidate in plan['candidates']}
    values = {event['value']['candidate']: model.text(event['value']['outputs'][observable])
              for event in events
              if event['kind'] == cstore.PREFIX + 'OBSERVATION' and event['value'].get('status') != 'INTERRUPTED'
              and observable in (event['value'].get('outputs') or {})}
    witness = [{'input': points[key], 'value': values[key]} for key in points if key in values]
    domain = {'min': _num(plan['inputs'][variable]['min']), 'max': _num(plan['inputs'][variable]['max'])}
    out = []
    for event in events:
        kind, value = event['kind'][len(cstore.PREFIX):], event['value']
        if kind == 'TOOL_QUALIFIED':
            ast = ((value.get('oracle') or {}).get('known_positive') or {}).get('law_ast')
        elif kind == 'HYPOTHESIS' and str(value.get('origin') or '').startswith('jump:'):
            ast = (value.get('predict') or {}).get(observable)
        else:
            continue
        if isinstance(ast, dict):
            out.append({'law_ast': ast, 'law': model.render(ast), 'origin': str(value.get('name') or value.get('id')),
                        'record_sha256': event.get('sha256') or '', 'variable': variable,
                        'domain': domain, 'witness': witness})
    return out


def _write(folder, operator, signature, log):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = 'anti' + signature[:8]
    record = {'schema': SCHEMA, 'name': name, 'template_ast': operator['template_ast'],
              'holes': operator['holes'], 'support': operator['support'], 'mdl': operator['mdl'],
              'domain_signature': signature, 'contract': operator['contract'],
              'provenance': {'generated_by': 'rds_compass_antiu',
                             'ledger_roots': sorted({case['ledger_root'] for case in operator['support']})}}
    target = folder / f"{name}-{digest(record)[:12]}.json"
    try:
        with target.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(record, sort_keys=True, indent=1))
    except FileExistsError:
        pass  # Create-only, like the v1 library: an existing record is never rewritten.
    if log:
        with open(log, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({'signature': signature, 'record': target.name}, sort_keys=True) + '\n')
    return target.name


def run(roots, library, log=None):
    """Read every ledger root, bucket by domain_signature, admit, contract, write v2 records."""
    report, bucket = {'buckets': [], 'records': [], 'admitted': 0}, {}
    for root in roots:
        store = cstore.store_for(Path(root))
        events = cstore.events(store)
        signature = domain_signature(events[0]['value'])
        for case in collect(events, events[0]['value']['goal']['observable']):
            bucket.setdefault(signature, []).append({**case, 'ledger_root': str(store.root.resolve())})
    for signature, cases in sorted(bucket.items()):
        operator = generalize([case['law_ast'] for case in cases])
        operator['support'] = [{key: case[key] for key in ('ledger_root', 'record_sha256', 'law', 'origin')}
                               for case in cases]
        entry = {'signature': signature, 'cases': len(cases), 'mdl': _mdl(operator)}
        entry['admit'] = admit(operator, cases)
        if entry['admit']:
            outcome = contract(operator, cases)
            entry['contract'] = outcome['ok']
            if outcome['ok']:
                operator['contract'] = outcome
                report['records'].append(_write(library, operator, signature, log))
                report['admitted'] += 1
            else:
                _log(log, {'signature': signature, 'reason': 'CONTRACT_FAILED', 'failures': outcome['failures']})
        else:
            _log(log, {'signature': signature, 'reason': 'MDL_OR_TRIVIAL_REFUSAL', 'mdl': entry['mdl']})
        report['buckets'].append(entry)
    return report


def _log(path, value):
    if path:
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(value, sort_keys=True) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline anti-unification of Compass campaign laws.')
    parser.add_argument('--root', action='append', required=True, help='campaign root holding a .rds ledger')
    parser.add_argument('--library', required=True, help='directory receiving rds-compass-operator-v2 records')
    parser.add_argument('--log', default=None, help='offline JSONL log of every decision (never the campaign ledger)')
    args = parser.parse_args(argv)
    report = run(args.root, args.library, args.log)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report['admitted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
