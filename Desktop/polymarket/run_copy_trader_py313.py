#!/usr/bin/env python3
"""
Launcher for copy_trader using system Python with venv site-packages appended.

Why this exists:
- In this environment, `venv/bin/python` hangs while importing parts of the
  Polymarket client stack (`eth_account` / `py_clob_client` chain).
- System `python3` imports that stack correctly when we append the venv's
  site-packages directory instead of using the venv interpreter directly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV_SITE_PACKAGES = ROOT / "venv" / "lib" / "python3.13" / "site-packages"

if str(VENV_SITE_PACKAGES) not in sys.path:
    sys.path.append(str(VENV_SITE_PACKAGES))

import copy_trader  # noqa: E402


if __name__ == "__main__":
    asyncio.run(copy_trader.main())
