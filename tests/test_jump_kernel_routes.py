"""Jump admission uses actual kernel executables and frozen owned manifests."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import rds_jump as jump
import rds_structure as structure
from rds_project import ProjectStore, file_sha

def example(name):
    spec = importlib.util.spec_from_file_location('kernel_' + name.replace('-', '_'), REPO / 'examples' / name / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

generation, owned = example('jump-generation'), example('jump-loop')


class KernelRouteTests(unittest.TestCase):
    def test_owned_stage_requires_complete_frozen_advisor_route_before_dispatch(self):
        for field in ('id', 'description', 'timeout_seconds', 'resource_estimates', 'outpaths'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root, contract = owned.build(Path(directory) / 'project')
                plan = json.loads((root / 'jump-generation.json').read_text(encoding='utf-8'))
                run = plan['stages'][0]['run']
                if field == 'id':
                    run['id'] = 'unrouted-jump-probe'
                elif field == 'description':
                    run['description'] = 'A different authorized manifest identity'
                elif field == 'timeout_seconds':
                    run['timeout_seconds'] -= 1
                elif field == 'resource_estimates':
                    run['resource_estimates']['wall_seconds'] += 1
                else:
                    run['outpaths'].append('out/unrouted-extra.json')
                owned.write(root, 'jump-generation.json', plan)
                next(b for b in contract['bindings'] if b['path'] == 'jump-generation.json')['sha256'] = file_sha(root / 'jump-generation.json')
                protocol = json.loads((root / 'protocol.json').read_text(encoding='utf-8'))
                protocol.update({role + '_sha256': ProjectStore._role_sha(contract, role) for role in ('code', 'config', 'data')})
                owned.write(root, 'protocol.json', protocol)
                ref = {'path': 'protocol.json', 'sha256': file_sha(root / 'protocol.json')}
                next(b for b in contract['bindings'] if b['role'] == 'protocol').update(ref)
                for route in contract['advisor_policy']['routes']:
                    route['manifest']['protocol'] = deepcopy(ref)
                owned.write(root, 'contract.json', contract)
                store = ProjectStore(root)
                store.initialize(contract)
                before, events = store.snapshot(), structure._events(store)
                with self.assertRaisesRegex(ValueError, 'exactly match.*Advisor route'):
                    jump.load_plan(store, before)
                self.assertEqual(jump.prepare_owned(store, None)['status'], 'JUMP_UNAVAILABLE')
                self.assertEqual(store.snapshot(), before)
                self.assertEqual(structure._events(store), events)

    def test_matching_owned_routes_use_resolved_frozen_protocol_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root, contract = owned.build(Path(directory) / 'project')
            store = ProjectStore(root)
            store.initialize(contract)
            before = store.snapshot()
            plan = jump.load_plan(store, before)
            routes = {r['manifest']['id']: r['manifest'] for r in contract['advisor_policy']['routes']}
            for stage in plan['stages']:
                self.assertNotIn('protocol_path', stage['run'])
                self.assertEqual(stage['run'], routes[stage['run']['id']])
            self.assertEqual(store.snapshot(), before)

    def test_actual_project_local_executable_requires_code_and_declaration(self):
        original_initialize = ProjectStore.initialize
        tool = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/whoami.exe' if os.name == 'nt' else Path(shutil.which('true') or '/usr/bin/true')
        self.assertTrue(tool.is_file(), 'Native small executable fixture is unavailable')
        for binding, declared in ((False, False), (True, False), (True, True)):
            with self.subTest(binding=binding, declared=declared), tempfile.TemporaryDirectory() as directory:
                def initialize(store, contract):
                    contract = deepcopy(contract)
                    local = store.root / ('local-generator.exe' if os.name == 'nt' else 'local-generator')
                    shutil.copy2(tool, local)
                    plan = json.loads((store.root / 'jump-generation.json').read_text(encoding='utf-8'))
                    for stage in plan['stages']:
                        argv = next(a for a in contract['allowed_commands'] if a == stage['run']['argv'])
                        argv[0] = str(local)
                        stage['run']['argv'] = argv
                    if binding:
                        contract['bindings'].append({'path': local.name, 'role': 'code', 'sha256': file_sha(local)})
                    if declared:
                        plan['generator_code_paths'].append(local.name)
                    generation.write(store.root / 'jump-generation.json', plan)
                    next(b for b in contract['bindings'] if b['path'] == 'jump-generation.json')['sha256'] = file_sha(store.root / 'jump-generation.json')
                    protocol = json.loads((store.root / 'protocol.json').read_text(encoding='utf-8'))
                    protocol.update({role + '_sha256': ProjectStore._role_sha(contract, role) for role in ('code', 'config', 'data')})
                    generation.write(store.root / 'protocol.json', protocol)
                    next(b for b in contract['bindings'] if b['role'] == 'protocol')['sha256'] = file_sha(store.root / 'protocol.json')
                    generation.write(store.root / 'contract.json', contract)
                    return original_initialize(store, contract)
                from unittest.mock import patch
                with patch.object(ProjectStore, 'initialize', initialize):
                    root, store = generation.build(Path(directory) / 'project')
                before = store.snapshot()
                if binding and declared:
                    self.assertIsNotNone(jump.load_plan(store, before))
                else:
                    with self.assertRaisesRegex(ValueError, 'entrypoint.*(frozen code|generator dependencies)'):
                        jump.load_plan(store, before)
                # Native command resolution and binding admission only; this
                # generic executable is not claimed to run a domain worker.
                self.assertEqual(store.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
