"""Shared pieces of the NewtonBench comparison: paths, the Codex CLI backend, task sampling and the design grid.

NewtonBench (HKUST-KnowComp/NewtonBench, MIT) is never copied into this repository. It is cloned into its own
directory, pinned by commit, and imported from there. Its LLM layer (``utils/call_llm_api``) is replaced at import
time by a stub that routes every chat call to the Codex CLI; nothing else in NewtonBench is modified.
"""
import hashlib
import importlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import types
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EVAL = Path(os.environ.get('NB_EVAL_DIR', '/Users/norriswu/Documents/kongtou项目/RDS/newtonbench-eval'))
NEWTONBENCH = Path(os.environ.get('NB_DIR', EVAL / 'NewtonBench'))
NEWTONBENCH_COMMIT = '912a4ba5f4356ddd06acc16e44460ca30be4abc2'
# Agents run in empty directories under here; the path holds none of the audited tokens.
AGENT_TMP = Path(os.environ.get('NB_AGENT_TMP', tempfile.gettempdir())) / 'nbcmp-agents'

CODEX = os.environ.get('RDS_CODEX_BIN') or '/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex'
MODEL, EFFORT, TIER = 'gpt-6.1-sol', 'xhigh', 'default'
# Pure chat: every tool that can execute code, read the filesystem or browse is switched off. With the
# code-mode host disabled, ``functions.exec`` fails closed (probed before the pre-registration).
CHAT_DISABLED = ('shell_tool', 'unified_exec', 'code_mode_host', 'code_mode', 'apps', 'plugins', 'browser_use',
                 'browser_use_external', 'computer_use', 'image_generation', 'view_image', 'multi_agent', 'goals',
                 'sleep_tool', 'tool_suggest', 'skill_search', 'in_app_browser')
# The Compass jump agent and the fallback keep a read-only shell (they may compute, like the code-assisted agent)
# but lose every tool that reaches outside the machine.
TOOL_DISABLED = ('apps', 'plugins', 'browser_use', 'browser_use_external', 'computer_use', 'image_generation',
                 'view_image', 'multi_agent', 'goals', 'tool_suggest', 'skill_search', 'in_app_browser')
CHAT_SECONDS = 1800
CALL_ATTEMPTS = 3

QUERY_BUDGET = 40      # Simulator evaluations per trial, every arm.
DESIGN_POINTS = 64     # Compass's declared candidate experiments (its MAX_CANDIDATES).
REL_TOLERANCE = '1/1000000'
# Amendment 1: a second stratum of the same cells at NewtonBench noise 0.01. Every arm is told the noise model;
# the floor is NewtonBench's absolute precision constant for the module's vanilla-equation output.
NOISE_LEVEL = 0.01
NOISE_FLOOR = {'m0_gravity': '1e-12', 'm1_coulomb_force': '1e-12', 'm2_magnetic_force': '1e-12',
               'm3_fourier_law': '1e-9', 'm4_snell_law': '1e-6', 'm5_radioactive_decay': '1e-9',
               'm6_underdamped_harmonic': '1e-9', 'm7_malus_law': '1e-9', 'm8_sound_speed': '1e-9',
               'm9_hooke_law': '1e-9', 'm10_be_distribution': '1e-18', 'm11_heat_transfer': '1e-9'}
NOISE_SIGMAS = 5       # Compass tolerance in the noisy stratum: 5 standard deviations (relative and floor).
NOISE_NOTE = ('\n\n**Noise model (benchmark harness):** each returned value is the true value plus independent '
              "Gaussian noise with standard deviation equal to 1% of the true value's magnitude, or {floor} if that "
              'is larger.')
SAMPLE_SEED = 20261015
EVAL_SEED_BASE = 7_000_000

MODULES = ['m0_gravity', 'm1_coulomb_force', 'm2_magnetic_force', 'm3_fourier_law', 'm4_snell_law',
           'm5_radioactive_decay', 'm6_underdamped_harmonic', 'm7_malus_law', 'm8_sound_speed', 'm9_hooke_law',
           'm10_be_distribution', 'm11_heat_transfer']
DIFFICULTIES = ['easy', 'medium', 'hard']
VERSIONS = ['v0', 'v1', 'v2']

# Audit: any of these in an agent's event stream means it touched sealed material (task identity, simulator
# or ground truth). Matching is case-sensitive; the agent's own working directory is masked first.
SEALED_TOKENS = ('NewtonBench', 'newtonbench', 'laws.py', 'LAW_REGISTRY', 'HIDDEN_CONSTANT', 'ground_truth',
                 'get_ground_truth_law', 'task.json', 'experiment.py', 'compass.json', '/.rds', 'rds-newtonbench',
                 'research-direction-selector', 'modules/m', 'modules.m', 'evaluate_law')


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_json(value):
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8'))


def git_head(path):
    done = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=path, capture_output=True, text=True)
    return done.stdout.strip() or None


# --- Task sample --------------------------------------------------------------------------------------------

def task_id(module, difficulty, version):
    return f'{module}/{difficulty}/{version}'


def task_key(task):
    """Task identity: the cell id, plus the noise stratum when noisy (noise-free ids are the registered ones)."""
    cell = task_id(task['module'], task['difficulty'], task['version'])
    return cell if not task['noise'] else f"{cell}/noise{task['noise']:g}"


def formal_cells():
    """One law version per (module, difficulty) cell, drawn once with the pre-registered seed."""
    rng = random.Random(SAMPLE_SEED)
    return [{'module': m, 'difficulty': d, 'version': rng.choice(VERSIONS), 'system': 'vanilla_equation',
             'noise': 0.0} for m in MODULES for d in DIFFICULTIES]


def formal_tasks():
    """Both strata of the formal cells (amendment 1): noise-free, then noise ``NOISE_LEVEL``."""
    cells = formal_cells()
    return cells + [dict(t, noise=NOISE_LEVEL) for t in cells]


# Amendment 6 (PILOT, descriptive only): the Compass arm with the transcendental dialect of release 0fb9e96.
# Synthesis grammar: the dialect's operators; the default constants plus generic halves and thirds as exponents.
PILOT_GRAMMAR = {'operators': ['add', 'sub', 'mul', 'div', 'pow', 'sqrt', 'exp', 'log', 'sin', 'cos', 'pi'],
                 'constants': ['0', '1', '2', '-1', '3', '-2', '1/2', '-1/2', '3/2', '-3/2', '1/3', '2/3']}
PILOT_AGENT_MAX_TOKENS = 1_000_000   # agent.max_tokens per trial (input + output, as Compass sums it)
PILOT_TOTAL_TOKENS = 54_937_698      # the registered formal Compass arm's jump-call spend (input + output)
PILOT_RESERVE = 1_800_000            # held per running trial: cap + one overshooting call + a fallback call
PILOT_UNREPORTED_CALL = 600_000      # counted by the launch guard for a call without usage (above any observed)
PILOT_SEED = 20261016
# random.Random(PILOT_SEED).sample(sorted(analyze.EXPRESSIBLE), 4), drawn before any pilot trial.
PILOT_REGRESSION = ['m11_heat_transfer/easy/v1', 'm8_sound_speed/medium/v0', 'm6_underdamped_harmonic/easy/v1',
                    'm9_hooke_law/hard/v0']
# A priori, from the ground-truth laws: of the 22 cells the registered dialect cannot express, these still need a
# function the transcendental dialect lacks (atan). The other 19 are expressible, some only as exp(a * log(x)).
NOT_EXPRESSIBLE_TRANSCENDENTAL = {'m4_snell_law/easy/v2', 'm4_snell_law/medium/v2', 'm4_snell_law/hard/v2'}


def pilot_spend(job):
    """Amendment 6: input + output tokens of every Compass jump call and fallback call in a pilot job directory.

    Every attempt counts. A call without a usage report counts as ``PILOT_UNREPORTED_CALL``, never as zero.
    """
    job = Path(job)
    usages = [json.loads(p.read_text(encoding='utf-8')).get('usage') for p in sorted(job.glob('jumps-a*/*/audit.json'))]
    record = job / 'trial.json'
    fallback = json.loads(record.read_text(encoding='utf-8')).get('fallback_call') if record.exists() else None
    if fallback is not None:
        usages.append(fallback.get('usage'))
    elif (job / 'fallback' / 'events.jsonl').exists():
        usages.append(None)  # A fallback call ran but no record of its usage survived.
    known = [u for u in usages if u and 'input_tokens' in u and 'output_tokens' in u]
    unreported = len(usages) - len(known)
    return {'tokens': sum(u['input_tokens'] + u['output_tokens'] for u in known) + unreported * PILOT_UNREPORTED_CALL,
            'calls': len(usages), 'unreported_calls': unreported}


def pilot_dialect_tasks():
    """Amendment 6: the 22 not-expressible cells and 4 regression cells, both strata, in a seeded launch order."""
    from analyze import EXPRESSIBLE
    cells = [c for c in formal_cells() if task_key(c) not in EXPRESSIBLE or task_key(c) in PILOT_REGRESSION]
    tasks = cells + [dict(c, noise=NOISE_LEVEL) for c in cells]
    random.Random(PILOT_SEED).shuffle(tasks)
    return tasks


def pilot_tasks():
    """Held-out pilot tasks: cells' versions that the formal sample did not draw, in both strata."""
    formal = {task_id(t['module'], t['difficulty'], t['version']) for t in formal_cells()}
    picks = []
    for module, difficulty in (('m0_gravity', 'easy'), ('m1_coulomb_force', 'medium'), ('m7_malus_law', 'easy')):
        version = next(v for v in VERSIONS if task_id(module, difficulty, v) not in formal)
        picks.append({'module': module, 'difficulty': difficulty, 'version': version, 'system': 'vanilla_equation',
                      'noise': 0.0})
    return picks + [dict(t, noise=NOISE_LEVEL) for t in picks]


def noise_note(task):
    return NOISE_NOTE.format(floor=NOISE_FLOOR[task['module']]) if task['noise'] else ''


def tolerance(task):
    """Compass's declared tolerance: 1e-6 relative when noise-free; 5 sd (relative and floor) when noisy."""
    if not task['noise']:
        return {'rel': REL_TOLERANCE}
    sigmas = Fraction(NOISE_SIGMAS)
    return {'rel': frac_text(sigmas * Fraction(str(task['noise']))),
            'abs': frac_text(sigmas * Fraction(NOISE_FLOOR[task['module']]))}


def eval_seed(task):
    """Same official test points for every arm and trial of one task (paired evaluation)."""
    key = task_id(task['module'], task['difficulty'], task['version'])
    return EVAL_SEED_BASE + int(sha256_bytes(key.encode('utf-8'))[:6], 16)


# --- NewtonBench import with the LLM layer routed to Codex -----------------------------------------------

def load_newtonbench(chat):
    """Import NewtonBench with ``utils.call_llm_api.call_llm_api`` bound to ``chat``.

    ``chat(messages, model_name, temperature, trial_info) -> (content, reasoning, tokens)``. The stub replaces
    only the network layer; agents, prompts, simulators and the evaluator are NewtonBench's own code.
    """
    path = str(NEWTONBENCH)
    if path not in sys.path:
        sys.path.insert(0, path)
    stub = types.ModuleType('utils.call_llm_api')

    def call_llm_api(messages, model_name, keys=None, temperature=0.4, trial_info=None):
        return chat(messages, model_name=model_name, temperature=temperature, trial_info=trial_info)
    stub.call_llm_api = call_llm_api
    import utils  # noqa: F401  (the package itself has no side effects)
    sys.modules['utils.call_llm_api'] = stub
    return {'vanilla': importlib.import_module('utils.vanilla_agent'),
            'code_assisted': importlib.import_module('utils.code_assisted_agent'),
            'prompts_base': importlib.import_module('modules.common.prompts_base')}


def nb_module(name):
    return importlib.import_module('modules.' + name)


# --- Codex CLI ----------------------------------------------------------------------------------------------

def codex_argv(workdir, last, *, chat, schema=None):
    argv = [CODEX, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '-m', MODEL,
            '-c', f'model_reasoning_effort="{EFFORT}"', '-c', f'service_tier="{TIER}"', '-c', 'web_search="disabled"']
    for feature in (CHAT_DISABLED if chat else TOOL_DISABLED):
        argv += ['--disable', feature]
    argv += ['-s', 'read-only', '-C', str(workdir), '--json', '-o', str(last)]
    if schema is not None:
        argv += ['--output-schema', str(schema)]
    return argv + ['-']


def audit(events_path, workdir, extra_tokens=()):
    """Scan an event stream for sealed material; count tool use; sum token usage."""
    raw = Path(events_path).read_bytes()
    text = raw.decode('utf-8', errors='replace').replace(str(workdir), '<WORK>')
    real = os.path.realpath(str(workdir))
    text = text.replace(real, '<WORK>')
    hits = sorted({t for t in tuple(SEALED_TOKENS) + tuple(extra_tokens) if t and t in text})
    usage, commands, tool_calls, errors = {}, 0, 0, 0
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get('type')
        if kind == 'turn.completed':
            for key, value in (event.get('usage') or {}).items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
        item = event.get('item') or {}
        if kind == 'item.completed':
            if item.get('type') == 'command_execution':
                commands += 1
            elif item.get('type') not in ('agent_message', 'reasoning', 'error', None):
                tool_calls += 1
            errors += item.get('type') == 'error'
    return {'contaminated': bool(hits), 'hits': hits, 'usage': usage, 'commands': commands,
            'other_tool_calls': tool_calls, 'error_items': errors, 'events_sha256': sha256_bytes(raw)}


def run_codex(prompt, logs, *, chat, files=None, schema=None, timeout=CHAT_SECONDS, extra_tokens=()):
    """One Codex exec in a fresh empty directory. Returns (last_message or None, record)."""
    AGENT_TMP.mkdir(parents=True, exist_ok=True)
    logs = Path(logs)
    logs.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='w', dir=AGENT_TMP))
    for name, content in (files or {}).items():
        (work / name).write_text(content, encoding='utf-8')
    schema_path = None
    if schema is not None:
        schema_path = logs / 'schema.json'
        schema_path.write_text(json.dumps(schema), encoding='utf-8')
    (logs / 'prompt.txt').write_text(prompt, encoding='utf-8')
    last = logs / 'last.txt'
    started = time.monotonic()
    code = None
    with open(logs / 'events.jsonl', 'wb') as events, open(logs / 'stderr.txt', 'wb') as stderr:
        try:
            done = subprocess.run(codex_argv(work, last, chat=chat, schema=schema_path), input=prompt.encode('utf-8'),
                                  stdout=events, stderr=stderr, timeout=timeout, cwd=work)
            code = done.returncode
        except subprocess.TimeoutExpired:
            code = 'TIMEOUT'
    record = {'exit_code': code, 'wall_seconds': round(time.monotonic() - started, 1),
              'prompt_sha256': sha256_bytes(prompt.encode('utf-8')),
              **audit(logs / 'events.jsonl', work, extra_tokens)}
    try:
        message = last.read_text(encoding='utf-8') if code == 0 else None
    except OSError:
        message = None
    record['last_sha256'] = sha256_bytes(message.encode('utf-8')) if message is not None else None
    (logs / 'audit.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
    return message, record


CHAT_FRAME = '''You are serving as a plain chat-completion model inside a benchmark harness. You have no tools in this
session: do not try to run code, read files or browse. Below is a conversation transcript. Write the single next
message of the assistant, continuing the conversation. Output only the text of that message.

'''


def render_messages(messages):
    parts = [CHAT_FRAME]
    for message in messages:
        role = message.get('role', 'user')
        parts.append(f'<{role}>\n{message.get("content") or ""}\n</{role}>\n')
    parts.append('<assistant>\n')
    return '\n'.join(parts)


class ChatBackend:
    """Stateless chat completion over the Codex CLI with tools disabled; every call is logged and audited."""

    def __init__(self, logs):
        self.logs = Path(logs)
        self.calls = []

    def __call__(self, messages, model_name=None, temperature=None, trial_info=None):
        prompt = render_messages(messages)
        role = 'judge' if 'mathematical judge' in prompt[:4000] or 'determine if two equations' in prompt else 'agent'
        for attempt in range(1, CALL_ATTEMPTS + 1):
            index = len(self.calls) + 1
            message, record = run_codex(prompt, self.logs / f'call{index:03d}', chat=True)
            record.update(index=index, role=role, attempt=attempt, requested_model=model_name,
                          requested_temperature=temperature)
            self.calls.append(record)
            if message is not None:
                tokens = int(record['usage'].get('output_tokens', 0))
                return message, None, tokens
            time.sleep(5 * attempt)
        raise RuntimeError(f'Codex chat call failed {CALL_ATTEMPTS} times (last exit {record["exit_code"]})')


# --- Compass design grid ------------------------------------------------------------------------------------

def _squares(roots):
    return [Fraction(r) ** 2 for r in roots]


# Amendment 5: the vanilla_equation simulator of these modules reads its inputs under other keyword names than
# the ``discovered_law`` signature, and silently falls back to a default for a name it does not read. The names
# are those in each module's own ``prompts.py`` experiment format. Every other module reads the signature names.
SIMULATOR_NAMES = {
    'm4_snell_law': {'n1': 'refractive_index_1', 'n2': 'refractive_index_2', 'angle1': 'incidence_angle'},
    'm6_underdamped_harmonic': {'k': 'k_constant', 'm': 'mass', 'b': 'b_constant'},
    'm8_sound_speed': {'gamma': 'adiabatic_index', 'T': 'temperature', 'M': 'molar_mass'},
    'm10_be_distribution': {'T': 'temperature'},
}
RERUN_MODULES = sorted(SIMULATOR_NAMES)


def simulator_name(module, name):
    return SIMULATOR_NAMES.get(module, {}).get(name, name)


# Input kinds, read from each module's PARAM_DESCRIPTION. Every level is the square of a rational, so the Compass
# dialect's exact ``sqrt`` is defined on every declared input.
KINDS = {
    'm4_snell_law': {'n1': 'index', 'n2': 'index', 'angle1': 'angle_deg'},
    'm7_malus_law': {'theta': 'angle_rad'},
}


def levels(kind, count):
    if kind == 'index':
        return _squares(['1', '5/4', '3/2', '2'])
    if kind == 'angle_deg':
        return _squares(['2', '4', '6', '8'])
    if kind == 'angle_rad':
        return _squares([Fraction(5 * k, 32) for k in range(1, count + 1)])
    if count == 4:
        return _squares(['1/4', '1', '4', '16'])
    if count == 8:
        return _squares(['1/8', '1/4', '1/2', '1', '2', '4', '8', '16'])
    if count == 64:
        return _squares([Fraction(k, 4) for k in range(1, 65)])
    raise ValueError(f'No level set for {kind} x {count}')


def design(module, names):
    """64 declared design points: a full factorial for 1-3 inputs, an OA(64, 4^4, strength 3) for 4 inputs."""
    kinds = {n: KINDS.get(module, {}).get(n, 'pos') for n in names}
    count = {1: 64, 2: 8, 3: 4, 4: 4}[len(names)]
    table = {n: levels(kinds[n], count) for n in names}
    points = []
    if len(names) <= 3:
        def rec(prefix, rest):
            if not rest:
                points.append(dict(prefix))
                return
            for value in table[rest[0]]:
                rec(prefix + [(rest[0], value)], rest[1:])
        rec([], list(names))
    else:
        a, b, c, d = names
        for i in range(4):
            for j in range(4):
                for k in range(4):
                    points.append({a: table[a][(i + j + k) % 4], b: table[b][i], c: table[c][j], d: table[d][k]})
    assert len(points) == DESIGN_POINTS, (module, len(points))
    return kinds, points


def frac_text(value):
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else f'{value.numerator}/{value.denominator}'
