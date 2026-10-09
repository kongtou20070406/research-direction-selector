"""Public synthetic strategies; fixtures do not represent LLM discovery."""
import json
from pathlib import Path
import sys
import time

strategy, output = sys.argv[1:]
data = json.loads(Path('data.json').read_text())
if strategy == 'timeout':
    time.sleep(10)
predictions = []
for row in data['inputs']:
    if strategy == 'interaction':
        predictions.append(row[0] ^ row[1])
    elif strategy == 'simultaneous':
        x, y = (row[0] + row[1]) / 2, (row[0] - row[1]) / 2
        predictions.append(x * y)
    elif strategy == 'periodic':
        predictions.append(row[0] % 2)
    elif strategy == 'linear':
        predictions.append(row[0])
    else:
        predictions.append(sum(row))
Path(output).parent.mkdir(parents=True, exist_ok=True)
Path(output).write_text(json.dumps({'predictions': predictions}), encoding='utf-8')
