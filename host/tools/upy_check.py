#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Static check that the MicroPython modules of pe_host use a MicroPython subset.

Runs under CPython (stdlib ast only), so CI can check without a MicroPython
binary. It rejects constructs and library calls that MicroPython (v1.2x, rp2
and unix ports) lacks or implements differently. Checked against the
MicroPython 1.29 unix port in this repository's tests (tests/test_host_micropython.py
runs the real interpreter when one is available). Usage:

    python3 host/tools/upy_check.py            # checks the default module list
    python3 host/tools/upy_check.py FILE...
"""

import ast
import sys
from pathlib import Path

HOST = Path(__file__).resolve().parent.parent
MICROPYTHON_MODULES = [
    "pe_host/__init__.py", "pe_host/protocol.py", "pe_host/errors.py", "pe_host/image.py",
    "pe_host/host.py", "pe_host/peers.py", "pe_host/flagship.py", "pe_host/selftest.py",
    "pe_host/ports/__init__.py", "pe_host/ports/base.py", "pe_host/ports/ttboard.py",
    "pe_host/ports/pico.py", "pe_host/ports/replay.py",
    "examples/flagship_demo.py", "examples/selftest_demo.py", "tests/upy/replay_main.py",
    "tests/upy/ops.py", "tests/upy/sha_check.py",
]

# Modules MicroPython does not ship (or ships so differently that code using
# them would not port).
FORBIDDEN_MODULES = {
    "dataclasses", "typing", "enum", "pathlib", "abc", "itertools", "functools", "shutil",
    "subprocess", "threading", "asyncio", "contextlib", "copy", "inspect", "__future__",
    "os.path", "textwrap", "string", "types", "fractions", "decimal", "statistics",
}
ALLOWED_MODULES = {
    "json", "hashlib", "binascii", "struct", "sys", "os", "time", "gc", "machine", "micropython",
    "ttboard", "ttboard.mode", "ttboard.demoboard", "ttboard.util.platform", "rp2",
    "ops",  # tests/upy/ops.py (test-only helper next to replay_main.py)
}
# Attributes CPython has and MicroPython lacks.
# (probed on the 1.29 unix port: str.zfill/casefold/format_map/isascii/
# expandtabs/translate, hashlib hexdigest and time.monotonic are absent)
FORBIDDEN_ATTRIBUTES = {
    "removesuffix", "removeprefix", "bit_count", "hexdigest", "casefold", "format_map",
    "isascii", "maketrans", "translate", "expandtabs", "zfill", "is_integer",
    "as_integer_ratio", "monotonic", "perf_counter",
}
# Builtins absent on the 1.29 unix port (probed).
FORBIDDEN_NAMES = {"TimeoutError", "ConnectionError", "FileNotFoundError", "PermissionError",
                   "BrokenPipeError", "ModuleNotFoundError", "NotADirectoryError", "breakpoint",
                   "vars", "__annotations__"}


class Checker(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.problems = []

    def bad(self, node, message):
        self.problems.append("%s:%d: %s" % (self.path, getattr(node, "lineno", 0), message))

    def visit_Import(self, node):
        for alias in node.names:
            self._module(node, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.level == 0:
            self._module(node, node.module or "")
        self.generic_visit(node)

    def _module(self, node, name):
        if name in FORBIDDEN_MODULES or name.split(".")[0] in FORBIDDEN_MODULES:
            self.bad(node, "module %s is not available on MicroPython" % name)
        elif name not in ALLOWED_MODULES and name.split(".")[0] not in ("ttboard", "pe_host"):
            self.bad(node, "module %s is not on the MicroPython allow-list" % name)

    def visit_Attribute(self, node):
        if node.attr in FORBIDDEN_ATTRIBUTES:
            self.bad(node, "attribute .%s is not available on MicroPython" % node.attr)
        if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr == "path":
            self.bad(node, "os.path is not available on MicroPython")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in FORBIDDEN_NAMES:
            self.bad(node, "name %s is not available on MicroPython" % node.id)
        self.generic_visit(node)

    def visit_JoinedStr(self, node):
        for value in node.values:
            if isinstance(value, ast.FormattedValue) and value.conversion not in (-1,):
                self.bad(node, "f-string conversions (!r/!s/!a) are avoided for MicroPython")
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        args = node.args
        for arg in args.posonlyargs + args.args + args.kwonlyargs:
            if arg.annotation is not None:
                self.bad(node, "annotations are avoided in MicroPython modules")
        if node.returns is not None:
            self.bad(node, "return annotations are avoided in MicroPython modules")
        if args.posonlyargs:
            self.bad(node, "positional-only parameters are not supported by MicroPython")
        for decorator in node.decorator_list:
            name = decorator.id if isinstance(decorator, ast.Name) else getattr(decorator, "attr", "")
            if name not in ("property", "staticmethod", "classmethod", "setter"):
                self.bad(node, "decorator %s is avoided in MicroPython modules" % name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.bad(node, "async functions are not used by the MicroPython host library")

    def visit_AnnAssign(self, node):
        self.bad(node, "variable annotations are avoided in MicroPython modules")

    def visit_Match(self, node):
        self.bad(node, "match statements are not supported by MicroPython")

    def visit_NamedExpr(self, node):
        self.bad(node, "assignment expressions are avoided in MicroPython modules")

    def visit_ClassDef(self, node):
        if node.keywords:
            self.bad(node, "class keywords (metaclass=...) are not supported by MicroPython")
        for decorator in node.decorator_list:
            self.bad(node, "class decorators are avoided in MicroPython modules")
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "super" and node.args:
            self.bad(node, "super() with arguments is not needed; use super()")
        # MicroPython exception classes have no class-level __init__ attribute
        # unless the class defines one: ExcBase.__init__(self, ...) raises
        # AttributeError at run time (found by the differential replay).
        if (isinstance(func, ast.Attribute) and func.attr == "__init__"
                and isinstance(func.value, ast.Name)
                and (func.value.id.endswith("Error") or func.value.id == "Exception")):
            self.bad(node, "%s.__init__(self, ...) fails on MicroPython; use super().__init__"
                     % func.value.id)
        self.generic_visit(node)


def check(paths):
    problems = []
    for path in paths:
        tree = ast.parse(Path(path).read_text(), filename=str(path))
        checker = Checker(path)
        checker.visit(tree)
        problems.extend(checker.problems)
    return problems


def main(argv):
    paths = argv[1:] or [str(HOST / name) for name in MICROPYTHON_MODULES]
    problems = check(paths)
    for problem in problems:
        print(problem)
    print("upy_check: %d files, %d problems" % (len(paths), len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
