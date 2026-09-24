"""Этап 5 аудита: статические гарантии — «порогов в коде нет» (критерий 6), «записи мимо guard нет» (FR-K3),
детерминизм окна и выгрузок между процессами (FR-C4/FR-X3)."""

import ast
import hashlib
import os
import subprocess
import sys
from pathlib import Path

KONVEYER = Path(__file__).resolve().parent.parent / "konveyer"


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    """Номер строки → имя функции верхнего уровня, в которой она находится."""
    spans: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                spans.setdefault(line, node.name)
    return spans


def test_в_верификаторе_нет_числовых_порогов():
    """Критерий приёмки 6: все пороги Э1 — из norms.json. В сравнениях verifier1.py допустимы только 0 и 1
    (индексы/пустота), остальные числа — только через _norm_value()."""
    offenders = []
    for name in ("verifier1.py", "metrics.py"):
        src = (KONVEYER / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comp in [node.left, *node.comparators]:
                    if isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)) and comp.value not in (0, 1):
                        if "# не порог" not in lines[node.lineno - 1]:
                            offenders.append(f"{name}:{node.lineno}: {lines[node.lineno - 1].strip()}")
    assert not offenders, "\n".join(offenders)


def _owner_name(func: ast.expr) -> str:
    owner = func.value if isinstance(func, ast.Attribute) else None
    return owner.id if isinstance(owner, ast.Name) else ""


def test_единственная_точка_записи():
    """FR-SC-1/FR-SC-12: единственная точка записи и удаления — guard.write_text/append_text/replace/remove.
    Прямые записи, копирования, ссылки, переименования и удаления (os.replace/rename/unlink/link/symlink,
    shutil.rmtree/move/copyfileobj, Path.unlink/rename/rmdir/symlink_to/hardlink_to/touch), внутренний
    `guard._write_atomic` и присваивания приватному состоянию guard (`guard._state.…`, `guard._library_dir`)
    и вызовы git через subprocess вне gitops.py допустимы только в перечисленных функциях, и все они
    работают вне библиотеки (init копирует демо-библиотеку до её защиты, компиляция окна — во временной папке)."""
    allowed = {
        "steps/setup.py": {"init"},
        # создание проекта: библиотеки ещё нет — стартовый комплект и профиль пишутся до её защиты (FR-LC-*)
        "project.py": {"create", "_copy_profile"},
        # онбординг: сырьё/ и онбординг/ лежат вне библиотеки; снимок и его восстановление — откат транзакции (FR-ON-17)
        "onboarding/importer.py": {"import_path", "save_index"},
        "onboarding/propose.py": {"save", "normalized_preview_path", "model_layer"},
        "onboarding/report.py": {"save"},
        "onboarding/apply.py": {"__init__", "restore", "close", "_write_conflict"},
        "steps/canon.py": {"retest", "_compile_window_to"},
        "steps/tact.py": {"apply_edits"},
        # переезд библиотеки целиком (папка переносится, содержимое документов не меняется; по подтверждению автора)
        "backup.py": {"split_library"},
    }
    writers = {"write_text", "write_bytes", "copyfile", "copytree", "move", "copy", "copy2", "rmtree", "copyfileobj"}
    guard_private = {"write_atomic", "_write_atomic"}  # внутренняя запись guard — снаружи не вызывается
    # у str/set/dict есть свои replace/remove — эти имена считаются файловыми только у os/shutil
    os_only = {"replace", "remove", "rename", "unlink", "rmdir", "removedirs", "renames", "link", "symlink"}
    any_owner = {"unlink", "rmdir", "rename", "symlink_to", "hardlink_to", "touch"}  # таких методов у str/dict/set нет — это Path
    offenders = []
    for path in sorted(KONVEYER.rglob("*.py")):
        if path.name == "guard.py":
            continue
        rel = path.relative_to(KONVEYER).as_posix()
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        spans = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    chain = t
                    while isinstance(chain, ast.Attribute) and not (isinstance(chain.value, ast.Name) and chain.value.id == "guard"):
                        chain = chain.value
                    if isinstance(chain, ast.Attribute) and chain.attr.startswith("_"):
                        offenders.append(f"{rel}:{node.lineno}: присваивание guard.{chain.attr}")
                continue
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            owner = _owner_name(func)
            raw_write = False
            if name in guard_private:
                raw_write = True
            elif name in writers and owner != "guard":
                raw_write = True
            elif name in os_only and owner in ("os", "shutil"):
                raw_write = True
            elif name in any_owner and owner not in ("guard",):
                raw_write = True
            elif name == "open":
                modes = [a for a in node.args[1:2]] + [k.value for k in node.keywords if k.arg == "mode"]
                if any(isinstance(m, ast.Constant) and isinstance(m.value, str) and set(m.value) & {"w", "a"} for m in modes):
                    raw_write = True
            elif owner == "subprocess" and path.name != "gitops.py":
                first = node.args[0] if node.args else None
                argv = first.elts if isinstance(first, (ast.List, ast.Tuple)) else []
                if argv and isinstance(argv[0], ast.Constant) and argv[0].value == "git":
                    raw_write = True  # git над библиотекой — только через gitops
            if raw_write and spans.get(node.lineno) not in allowed.get(rel, set()):
                offenders.append(f"{rel}:{node.lineno}: {name}")
    assert not offenders, "\n".join(offenders)


def test_нет_новых_открывателей_сессии():
    """FR-SC-12: `guard.canon_write_session()` открывает ровно один модуль — единый конвейер
    `canonchange.canon_change` (проверки git → запись → экспорт → линт → коммит/«незакоммичено»).
    Канонист, круги истории, панель и `canon-commit` идут через него; новых открывателей быть не должно."""
    openers = []
    for path in sorted(KONVEYER.rglob("*.py")):
        if path.name == "guard.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "canon_write_session":
                openers.append(f"{path.relative_to(KONVEYER).as_posix()}:{node.lineno}")
    assert [o.split(":")[0] for o in openers] == ["canonchange.py"], openers


def test_детерминированность_между_процессами(ws, library):
    """FR-C4/FR-X3: окно и выгрузки не зависят от хэш-сида процесса."""
    results = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1"}
        for args in (["export"], ["compile", "1"]):
            subprocess.run([sys.executable, "-m", "konveyer", *args], cwd=ws.root, env=env, check=True, capture_output=True)
        window = ws.window_path(1).read_bytes()
        manifest = (ws.exports / "индекс.json").read_bytes()
        results.append((hashlib.sha256(window).hexdigest(), hashlib.sha256(manifest).hexdigest()))
    assert results[0] == results[1]
