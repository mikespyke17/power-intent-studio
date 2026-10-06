#!/usr/bin/env python3
"""Convenience launcher: `python3 power_intent_studio.py` from the repo
root. Equivalent to `python3 -m frontend.src.app.main` -- this just saves
typing the module path.

Runs the real backend (RTL/.lib elaboration via pyslang/GHDL, UPF/CPF
elaboration, mapping, validation -- see docs/backend_integration.md).
This is the app's single, standard entry point -- the earlier separate
`power_intent_studio_validation_harness.py` prototype has been retired;
its real-backend wiring is now this script's own default behavior.
Mock/fixture data is still used throughout the front-end test suite
(`MainWindow()`'s own bare constructor defaults every action to `None`),
just not by this launcher.
"""

from frontend.src.app.main import main

if __name__ == "__main__":
    main()
