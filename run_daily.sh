#!/bin/bash
# ============================================================
# run_daily.sh — called by macOS launchd at 6:30 AM daily
# ============================================================
cd /Users/samfeldt/tasty-pick-ems
/usr/bin/python3 generate_report.py
