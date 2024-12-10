#!/bin/bash
# workdir=$(cd $(dirname $0); pwd)
cd $(dirname $0)

[[ -d ".venv" ]] && source .venv/bin/activate
rm -rf build
python3 setup.py bdist_wheel
