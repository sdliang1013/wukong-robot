#!/bin/bash
# workdir=$(cd $(dirname $0); pwd)
cd $(dirname $0)
[[ -d ".venv" ]] && source .venv/bin/activate
export PYTHONPATH=$PWD

ps aux|grep wukong|grep -v grep > /dev/null && ps -ef|grep wukong|grep -v grep|awk '{print $2}'|xargs kill

nohup python3 -m chat_robot.wukong >/dev/null 2>&1 &
