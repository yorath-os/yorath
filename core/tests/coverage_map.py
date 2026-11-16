"""Coverage map: which invariants tests declare and which main ones are checked by mutation.

``make coverage-map``; non-zero exit if an invariant has no test.
"""

import ast
import sys
from pathlib import Path

from tests.registry import INVARIANTS_DOC, known_ids, main_ids

TESTS = Path(__file__).resolve().parent


def claims() -> dict[str, list[str]]:
    """Identifier → tests that declared it with the ``covers`` marker."""
    out: dict[str, list[str]] = {}
    for path in sorted(TESTS.glob("*/test_*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and ast.unparse(deco.func).endswith("mark.covers"):
                    for arg in deco.args:
                        if isinstance(arg, ast.Constant):
                            out.setdefault(arg.value, []).append(f"{path.stem}::{node.name}")
    return out


def main() -> int:
    found = claims()
    ids = sorted(known_ids())
    gaps = [i for i in ids if i not in found]
    mutations = INVARIANTS_DOC.read_text(encoding="utf-8").split("<!-- mutations -->")[-1]
    unproved = sorted(i for i in main_ids() if i not in mutations)
    print(f"invariants {len(ids)}, with a test {len(ids) - len(gaps)}, main {len(main_ids())}")
    for i in gaps:
        print(f"  no test: {i}")
    for i in unproved:
        print(f"  no mutation: {i}")
    return 1 if gaps or unproved else 0


if __name__ == "__main__":
    sys.exit(main())
