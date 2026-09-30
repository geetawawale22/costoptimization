"""Static evidence inventory; never imports or executes the legacy scripts."""
import ast
import hashlib
import json
from pathlib import Path
import warnings


def main():
    backend = Path(__file__).resolve().parents[1]
    source_path = backend.parent / "PCM_5/PCM_5/Sheet_Metal_Forging_with summary_chk_Chnages.py"
    source = source_path.read_text(encoding="utf-8-sig")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    functions, formulas = [], []
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        calls = []
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write_formula":
                calls.append(node)
                formulas.append({"function": function.name, "line": node.lineno,
                                 "source_expression": ast.get_source_segment(source, node),
                                 "executable_database_formula": False})
        functions.append({"function": function.name, "start_line": function.lineno, "end_line": function.end_lineno,
                          "write_formula_calls": len(calls), "legacy_alternative": "prev" in function.name.lower()})
    data = {"source": str(source_path.relative_to(backend.parent)),
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "note": "Static write_formula call inventory. Assignments/builders and master-contained expressions require separate translation; these Python/Excel fragments are evidence, not executable database expressions.",
            "functions": functions, "formula_calls": sorted(formulas, key=lambda r: r["line"])}
    target = backend / "docs/pcm/legacy_formula_inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Inventoried {len(functions)} functions and {len(formulas)} write_formula calls")


if __name__ == "__main__":
    main()
