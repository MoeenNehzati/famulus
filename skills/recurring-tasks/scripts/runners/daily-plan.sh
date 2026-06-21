#!/bin/bash
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus PATH=/home/moeen/.local/bin:$PATH /home/moeen/.local/bin/claude -p "/daily-plan" >> /home/moeen/.claude/skills/recurring-tasks/logs/daily-plan/run.log 2>&1
