#!/bin/bash
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus PATH=/home/moeen/.local/bin:$PATH /home/moeen/.local/bin/claude -p "/email-triage" >> /home/moeen/.claude/skills/recurring-tasks/logs/email-triage/run.log 2>&1
