# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Load test/model/line_std_ref.py by path (no other test/model module is imported)."""

import importlib.util
import pathlib
import sys

_PATH = pathlib.Path(__file__).resolve().parent.parent / "model" / "line_std_ref.py"
_spec = importlib.util.spec_from_file_location("line_std_ref", _PATH)
ref = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("line_std_ref", ref)
_spec.loader.exec_module(ref)
