"""Experiment A worker: existing restrictions plus standard JSON input parsing."""
import runpy
from pathlib import Path

# Trusted runner loads a fresh namespace; generated code never sees runpy/Path.
_worker = runpy.run_path(str(Path(__file__).with_name('frozen_math_worker.py')), run_name='aflow_math_worker_definition')
MODULES = _worker['MODULES']
ATTRIBUTES = _worker['ATTRIBUTES']
BUILTINS = _worker['BUILTINS']
MODULES.add('json')
ATTRIBUTES.add('loads')
validate = _worker['validate']

if __name__ == '__main__':
    _worker['main']()
