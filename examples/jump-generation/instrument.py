"""Synthetic measured system, separate from the candidate search.

Observations are computed when the probe requests an input. This visible trusted
fixture is neither a sealed benchmark nor a real scientific instrument.
"""
import json
from pathlib import Path


def measure(inputs):
    settings = json.loads(Path('instrument.json').read_text(encoding='utf-8'))
    return inputs['x'] * inputs['y'] + settings['offset']
