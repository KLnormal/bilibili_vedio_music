#!/usr/bin/env python3
"""Bilibili Video Crawler — entry point.

Run ``python main.py`` (no arguments) to launch the btop-style TUI, or use a
subcommand for headless operation (see ``bilibili_crawler.cli.commands``).
"""
import sys

from bilibili_crawler.cli.commands import main

if __name__ == "__main__":
    sys.exit(main())
