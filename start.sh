#!/bin/bash
# workdir=$(cd $(dirname $0); pwd)
cd $(dirname $0)
[[ -d ".venv" ]] && source .venv/bin/activate
export PYTHONPATH=$PWD

ps aux|grep octopus|grep -v grep > /dev/null && ps -ef|grep octopus|grep -v grep|awk '{print $2}'|xargs kill

nohup python3 -m octopus.app >/dev/null 2>&1 &
