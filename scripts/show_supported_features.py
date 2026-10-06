#!/usr/bin/env python3
"""Prints the power-intent import supported-features list.

Section 3's deliverable is meant to be "inspectable/reviewable" -- this
is how you read it without opening Python. The list itself lives in
frontend/src/power_intent_view/supported_features.py and is kept
exhaustive against the backend IR schema by
tests/frontend/test_supported_features.py.

Usage: python3 scripts/show_supported_features.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frontend.src.power_intent_view.supported_features import render_summary  # noqa: E402

if __name__ == "__main__":
    print(render_summary())
