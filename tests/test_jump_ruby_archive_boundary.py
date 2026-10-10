"""Frozen Ruby admission and actual launch AST; bounded archive group controls."""
import ast
from copy import deepcopy
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import test_jump_ambient_startup_boundary as admission
import rds_hypergraph_archive as archive


class RubyArchiveBoundaryTests(unittest.TestCase):
    def launch(self, argv, *, autonomy=False):
        source = Path(__file__).resolve().parents[1] / 'scripts/rds_project.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        blocks = [n for n in ast.walk(tree) if isinstance(n, ast.With)
                  and any(isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'worker_options'
                          for t in s.targets) for s in n.body)]
        self.assertEqual(len(blocks), 1)
        body = blocks[0].body
        start = next(i for i,s in enumerate(body) if isinstance(s, ast.Assign)
                     and any(isinstance(t,ast.Name) and t.id == 'worker_options' for t in s.targets))
        end = next(i for i,s in enumerate(body[start:],start) if isinstance(s, ast.Assign)
                   and any(isinstance(t,ast.Name) and t.id == 'process' for t in s.targets))
        launch = ast.fix_missing_locations(ast.Module(body=body[start:end+1], type_ignores=[]))
        seen = []
        class Observer:
            PIPE = object()
            DEVNULL = object()
            def Popen(_, actual, **options):
                seen.append((list(actual), options)); return object()
        namespace = {'Path':Path,'os':os,'re':re,'subprocess':Observer(),
                     '__file__':str(source),'current':{'autonomy_request':{}} if autonomy else {},
                     'argv':argv,'self':type('Store',(),{'root':source.parent})(),
                     'out':object(),'err':object(),'flags':0}
        exec(compile(launch,str(source),'exec'),namespace)
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0][0],argv)
        return seen[0][1]

    def test_ruby_frozen_admission_and_launch_isolate_post_admission_options(self):
        # Resolver-only admission and inert actual Popen AST: no Ruby/hook executes.
        for family in ('ruby','ruby3.4','ruby3.4.1'):
            for legacy in (False,True):
                admission.AmbientStartupTests().admission(family,['worker.opaque'],legacy=legacy)
        for name in ('ruby','ruby.exe','RUBY3.4.EXE','ruby3.4.1'):
            for autonomy in (False,True):
                argv=[name,'--enable=rubyopt','--','worker.rb','--disable=rubyopt']
                with patch.dict(os.environ,{'RUBYOPT':'-r./unbound.rb','RuByOpT':'-r./other.rb',
                    'RUBYLIB':'./mutable-libs','RuByLiB':'./other-libs',
                    'RUBYGEMS_GEMDEPS':'./unbound-Gemfile','RuByGeMs_GeMdEpS':'-',
                    'RDS_RUNTIME_SCRIPTS':'parent-runtime','OTHER_STARTUP_CONTROL':'retained'},clear=True):
                    before=dict(os.environ)
                    options=self.launch(argv,autonomy=autonomy)
                    env=options['env']
                    self.assertFalse({'rubyopt','rubylib','rubygems_gemdeps'} & {k.casefold() for k in env})
                    self.assertEqual(env['OTHER_STARTUP_CONTROL'],'retained')
                    runtime=str(Path(__file__).resolve().parents[1]/'scripts') if autonomy else 'parent-runtime'
                    self.assertEqual(env['RDS_RUNTIME_SCRIPTS'],runtime)
                    self.assertEqual(dict(os.environ),before)
                    self.assertIs(options['shell'],False)

    def test_launch_keeps_node_isolation_and_unrelated_interpreter_environment(self):
        for name in ('node','NODEJS.EXE','ruby-wrapper','ruby3.x','python'):
            with patch.dict(os.environ,{'NODE_OPTIONS':'--require unbound.js','RUBYOPT':'-rhook',
                                        'RUBYLIB':'./kept','RUBYGEMS_GEMDEPS':'./kept-Gemfile',
                                        'RDS_RUNTIME_SCRIPTS':'runtime'},clear=True):
                before=dict(os.environ);options=self.launch([name,'-r','frozen-file','--','main'])
                if name.lower().removesuffix('.exe') in ('node','nodejs'):
                    self.assertNotIn('NODE_OPTIONS',options['env'])
                    self.assertEqual(options['env']['RUBYOPT'],'-rhook')
                    self.assertEqual(options['env']['RUBYLIB'],'./kept')
                    self.assertEqual(options['env']['RUBYGEMS_GEMDEPS'],'./kept-Gemfile')
                    self.assertEqual(options['env']['RDS_RUNTIME_SCRIPTS'],'runtime')
                else:
                    self.assertNotIn('env',options)
                self.assertEqual(dict(os.environ),before)

    def test_declared_group_bound_precedes_filtering_or_materialization(self):
        for schema in ('rds-readonly-archive-v1',1):
            graph={'schema':schema,'nodes':[], 'hyperedges' if schema==1 else 'edges':[],
                   'groups':[{'id':'a','label':'unused A'}, {'id':'b','summary':'unused B'}]}
            with patch.object(archive,'MAX_NODES',2):
                before=deepcopy(graph);display=archive.build_archive_display(graph)
                self.assertEqual([g['id'] for g in display['groups']],['a','b'])
                self.assertEqual(graph,before)
                for extra in ({'id':'c'}, None, {'id':'a'}):
                    oversized=deepcopy(graph);oversized['groups'].append(extra)
                    with patch.object(archive,'_text',side_effect=AssertionError('materialization before bound')):
                        with self.assertRaisesRegex(ValueError,'declared group limit'):
                            archive.build_archive_display(oversized)
                    self.assertEqual(oversized['groups'][-1],extra)

    def test_bounded_declared_groups_keep_authored_metadata_and_duplicate_refusal(self):
        graph={'schema':'rds-readonly-archive-v1','nodes':[{'id':'n','group':'authored','type':'research'}],
               'edges':[],'groups':[{'id':'authored','label':'Original authored','summary':'Original summary'},
                                   {'id':'unused','label':'Unused retained'}]}
        before=deepcopy(graph)
        with patch.object(archive,'MAX_NODES',2):
            display=archive.build_archive_display(graph)
            self.assertEqual(display['nodes'][0]['group'],'authored')
            group=next(g for g in display['groups'] if g['id']=='authored')
            self.assertEqual(group['label'],'Original authored')
            self.assertEqual(group['summary'],'Original summary')
            self.assertEqual(graph,before)
            duplicate=deepcopy(graph);duplicate['groups'][1]={'id':'authored','label':'conflict'}
            with self.assertRaisesRegex(ValueError,'Duplicate declared group ID'):
                archive.build_archive_display(duplicate)
            self.assertEqual(graph,before)

if __name__ == '__main__':
    unittest.main()
