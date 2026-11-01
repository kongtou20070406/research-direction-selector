import argparse, json, math, pathlib, time
p = argparse.ArgumentParser(); p.add_argument('--x'); p.add_argument('--id'); p.add_argument('--output', required=True)
a = p.parse_args()
task = json.loads(pathlib.Path('task.json').read_text(encoding='utf-8'))
time.sleep(task.get('sleep', 0))
if 'arms' in task:
    samples = task['arms'][a.id]
    body = {'outputs': {'score': sum(samples.values()) / len(samples)}, 'samples': samples}
else:
    x = int(a.x)
    y = eval(task['law'], {'__builtins__': {}}, {'x': x, 'popcount': lambda n: bin(abs(n)).count('1'),
                                                  'sin': math.sin, 'cos': math.cos, 'exp': math.exp,
                                                  'log': math.log, 'sqrt': math.sqrt})
    body = {'outputs': {'y': y}}
out = pathlib.Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(body), encoding='utf-8')
