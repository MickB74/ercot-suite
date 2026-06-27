#!/usr/bin/env python3
"""Cron/launchd entry point — one alert check. Use --force to ignore cooldowns."""

import sys

from alerts import run

if __name__ == "__main__":
    run(force="--force" in sys.argv)
