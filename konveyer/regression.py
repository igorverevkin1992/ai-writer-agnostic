"""Регрессионный корпус золотых тестов (FR-RG-1…FR-RG-4).

Пропуск любого ожидаемого флага блокирует смену конфигурации (FR-RG-3):
предупреждение при `accept`, запрет при фиксации `пере-тест --зафиксировать`.

Э2 без API (П-5): промпт теста сохраняется в `регрессия/промпты/<id>.md`, ответ модели, положенный автором
в `регрессия/ответы/<id>.json` (список флагов Верификатора-2), принимается при следующем прогоне.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import yaml

from . import adapters, cancel, exporter, guard, verifier1, verifier2
from .config import Config
from .paths import Workspace
from .schemas import Brief, GoldenTest

PROMPTS_DIR = "промпты"
ANSWERS_DIR = "ответы"
# ключи конфига, от которых зависят проверки (FR-RG-4): модели — отдельным ключом «модели»;
# пути, пороги экономики, сохранность, текущий том — не конфигурация проверок
CONFIG_KEYS = ("e2_max_flags", "e2_quote_words", "window_soft_limit_chars", "auto_retries_verify1", "edit_cycle_max_iterations")


def golden_dir(ws: Workspace) -> Path:
    return ws.regression / "золотые"


def load_tests(ws: Workspace) -> list[GoldenTest]:
    tests = []
    for path in sorted(golden_dir(ws).glob("*.json")):
        tests.append(GoldenTest.model_validate(json.loads(path.read_text(encoding="utf-8"))))
    return tests


def safe_file_stem(test_id: str) -> str:
    """test_id → безопасное имя файла: без разделителей путей и служебных символов."""
    stem = re.sub(r"[^\w.\-]+", "_", test_id.strip(), flags=re.UNICODE).strip("._")
    if not stem:
        raise ValueError(f"test_id «{test_id}» не годится для имени файла — используйте буквы, цифры, «_» и «-».")
    return stem[:120]


def context_slice(ws: Workspace, *, focal: str = "", year: int | None = None, chapter: int | None = None,
                  window_file: Path | None = None, use_corpus: bool = False, volume_words: int | None = None) -> dict:
    """Срез контекста золотого теста (FR-RG-1): явные значения поверх брифа и окна главы, где ошибка была поймана.
    Ключи — те, что читает прогон: chapter, volume, focal, year, volume_words, not_knows, window, use_corpus."""
    ctx: dict = {"focal": focal, "year": year}
    if chapter is not None:
        brief = exporter.load_brief(ws.exports, chapter)
        ctx.update({"chapter": brief.chapter, "volume": brief.volume, "focal": focal or brief.focal,
                    "year": year if year is not None else brief.year})
        if brief.volume_words:
            ctx["volume_words"] = brief.volume_words
        if brief.not_knows:
            ctx["not_knows"] = list(brief.not_knows)
        window_path = ws.window_path(chapter)
        if window_path.exists():
            ctx["window"] = window_path.read_text(encoding="utf-8")
    if window_file is not None:
        ctx["window"] = Path(window_file).read_text(encoding="utf-8")
    if volume_words is not None:
        ctx["volume_words"] = volume_words
    if use_corpus:
        ctx["use_corpus"] = True
    return ctx


def add_test(ws: Workspace, test: GoldenTest) -> Path:
    """FR-RG-1: пополнение корпуса из ошибки, пропущенной эшелонами и пойманной автором."""
    path = golden_dir(ws) / f"{safe_file_stem(test.test_id)}.json"
    guard.write_text(path, json.dumps(test.model_dump(), ensure_ascii=False, indent=2) + "\n")
    return path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _config_hash(ws: Workspace) -> str:
    """Отпечаток конфига проверок: только ключи `CONFIG_KEYS` (переключение тома, папка архива, пороги
    расходов отчёт регрессии не устаревают). Битый конфиг — по тексту файла."""
    from .config import load_config

    try:
        cfg = load_config(ws)
    except Exception:  # noqa: BLE001
        path = ws.root / "конфиг.yaml"
        return _sha(path.read_bytes()) if path.exists() else ""
    data = {k: getattr(cfg, k) for k in CONFIG_KEYS}
    return _sha(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def _manifest_hash(ws: Workspace) -> str:
    """Отпечаток манифеста без текущего тома (`том открыть/закрыть` — не смена конфигурации проверок)."""
    path = ws.root / "проект.yaml"
    if not path.exists():
        return ""
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        return _sha(raw)
    if isinstance(data, dict) and isinstance(data.get("проект"), dict):
        data = {**data, "проект": {k: v for k, v in data["проект"].items() if k != "текущий_том"}}
    return _sha(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def environment_hashes(ws: Workspace) -> dict[str, str]:
    """Отпечаток конфигурации, к которой относится отчёт регрессии (FR-RG-4): модели ролей, ключи конфига
    проверок, манифест (без текущего тома), шаблоны/промпты/методики проекта и движка, нормы.
    Изменилось — отчёт устарел."""

    def file_hash(path: Path) -> str:
        return _sha(path.read_bytes()) if path.exists() else ""

    h = hashlib.sha256()
    for folder in (ws.templates, ws.root / "промпты", ws.root / "методики"):
        if folder.exists():
            for f in sorted(p for p in folder.rglob("*") if p.is_file()):
                h.update(f.relative_to(folder).as_posix().encode("utf-8"))
                h.update(b"\0")
                h.update(f.read_bytes())
                h.update(b"\0")
    engine = hashlib.sha256()
    for f in sorted(p for p in Path(str(resources.files("konveyer").joinpath("шаблоны"))).glob("*") if p.is_file()):
        engine.update(f.read_bytes())
    for folder in ("модули", "методики"):
        for f in sorted(p for p in Path(str(resources.files("konveyer").joinpath(folder))).rglob("*") if p.is_file()):
            engine.update(f.read_bytes())
    return {
        "конфиг.yaml": _config_hash(ws),
        "проект.yaml": _manifest_hash(ws),
        "шаблоны": h.hexdigest(),
        "шаблоны_движка": engine.hexdigest(),
        "norms.json": file_hash(ws.exports / "norms.json"),
        "модели": _sha(json.dumps({r: [m.provider, m.model, m.params] for r, m in _config_roles(ws).items()},
                                  ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")),
    }


def _config_roles(ws: Workspace) -> dict:
    from .config import load_config

    try:
        return load_config(ws).roles()
    except Exception:  # noqa: BLE001 — битый конфиг: отпечаток без моделей
        return {}


def _brief_from_context(ctx: dict) -> Brief:
    return Brief(
        chapter=int(ctx.get("chapter", 1)),
        volume=int(ctx.get("volume", 1)),
        year=ctx.get("year"),
        focal=ctx.get("focal", ""),
        volume_words=ctx.get("volume_words"),
        not_knows=ctx.get("not_knows", []),
    )


def _split(raised: set[str], test: GoldenTest) -> tuple[list[str], list[str], list[str]]:
    expected = set(test.expected_flags)
    ignore = set(test.ignore_flags) - expected
    return sorted(raised & expected), sorted(expected - raised), sorted(raised - expected - ignore)


def run_e1_test(ws: Workspace, test: GoldenTest) -> tuple[list[str], list[str], list[str]]:
    """Прогон Э1 по фрагменту: (поймано, пропущено, лишние flag-и по check_id; `ignore_flags` лишними не считаются)."""
    ctx = test.context_slice
    brief = _brief_from_context(ctx)
    checks = verifier1.analyze_text(ws, brief.chapter if ctx.get("chapter") else 0, test.fragment, brief=brief,
                                    window_raw=str(ctx.get("window", "")), use_corpus=bool(ctx.get("use_corpus")))
    return _split({c.check_id for c in checks if c.status != "PASS"}, test)


def e2_prompt(ws: Workspace, cfg: Config, test: GoldenTest) -> tuple[str, str]:
    """(system, user) Верификатора-2 для золотого теста Э2."""
    system = verifier2.system_prompt(ws, cfg)
    ctx = test.context_slice
    lines = [
        f"# Регрессионный тест {test.test_id}",
        f"- Фокал: {ctx.get('focal', '')}",
        f"- Год: {ctx.get('year', '')}",
    ]
    not_knows = [str(x) for x in (ctx.get("not_knows") or [])]
    if not_knows:
        lines.append("- Фокал НЕ знает: " + "; ".join(not_knows))
    lines.append(
        "- Бриф: фрагмент вне брифа; любые факты, мотивировки и сентенции, "
        "отсутствующие в этом контексте и в срезе канона ниже, — самоволка или нарушение брифа."
    )
    window = str(ctx.get("window") or "").strip()
    if window:
        # срез канона золотого теста (реестры, запреты, закладки, континуити): тест самодостаточен
        lines += ["", "## СРЕЗ КАНОНА", "", verifier2.FENCE_OPEN, window, verifier2.FENCE_CLOSE]
    lines += ["", "## ТЕКСТ", "", verifier2.FENCE_OPEN, test.fragment, verifier2.FENCE_CLOSE]
    return system, "\n".join(lines)


def _flags_raised(raw: str, cfg: Config) -> set[str]:
    flags = verifier2.parse_flags(raw, cfg.e2_quote_words)
    return {f.type for f in flags} | {"самоволка" for f in flags if f.kind == "samovolka"}


class E2Unparsed(ValueError):
    """Ответ Верификатора-2 не разобран; сырой ответ сохранён в `path`."""

    def __init__(self, message: str, path: Path):
        super().__init__(message)
        self.path = path


def run_e2_test(ws: Workspace, cfg: Config, test: GoldenTest) -> tuple[list[str], list[str], list[str]]:
    """Прогон Э2 по фрагменту: ответ из `регрессия/ответы/<id>.json` (ручной прогон), иначе API Верификатора-2.
    Без API — `ManualModeNeeded`, промпт сохранён в `регрессия/промпты/<id>.md`; нечитаемый ответ — `E2Unparsed`."""
    stem = safe_file_stem(test.test_id)
    answer = ws.regression / ANSWERS_DIR / f"{stem}.json"
    system, user = e2_prompt(ws, cfg, test)
    if answer.exists():
        raw = answer.read_text(encoding="utf-8")
        source = f"регрессия/{ANSWERS_DIR}/{answer.name}"
    else:
        try:
            raw = adapters.call_role(cfg, "верификатор2", system, user, ws.logs, role="верификатор-2 (регрессия)")
        except adapters.ManualModeNeeded:
            guard.write_text(ws.regression / PROMPTS_DIR / f"{stem}.md",
                             f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n\n"
                             f"<!-- ответ модели (JSON-список флагов) положите в регрессия/{ANSWERS_DIR}/{stem}.json -->\n")
            raise
        source = "API"
    try:
        raised = _flags_raised(raw, cfg)
    except ValueError as e:
        raw_path = ws.regression / ANSWERS_DIR / f"{stem}_сырой.md"
        guard.write_text(raw_path, raw)
        raise E2Unparsed(f"ответ Верификатора-2 ({source}) не разобран: {e}", raw_path) from None
    return _split(raised, test)


def run_regression(ws: Workspace, llm: bool = False, cfg: Config | None = None) -> dict:
    """FR-RG-2: Э1 всегда; Э2 — по флагу --llm (при недоступном API — пропуск с пометкой и сохранённым промптом;
    ответ, положенный автором файлом, принимается и без API)."""
    tests = load_tests(ws)
    results = []
    for test in tests:
        if test.echelon == "Э2":
            has_answer = (ws.regression / ANSWERS_DIR / f"{safe_file_stem(test.test_id)}.json").exists()
            if not llm and not has_answer:
                results.append({"test_id": test.test_id, "skipped": "Э2 (запустите с --llm или положите ответ в "
                                                                    f"регрессия/{ANSWERS_DIR}/)"})
                continue
            cancel.check(f"регрессия: перед {test.test_id}")
            try:
                caught, missed, extra = run_e2_test(ws, cfg or Config(), test)
            except adapters.ManualModeNeeded as e:
                results.append({"test_id": test.test_id, "skipped": f"Э2: API недоступен ({e.reason}); промпт — "
                                                                    f"регрессия/{PROMPTS_DIR}/{safe_file_stem(test.test_id)}.md"})
                continue
            except E2Unparsed as e:
                results.append({"test_id": test.test_id, "skipped": f"Э2: {e}", "не_разобран": e.path.name})
                continue
        else:
            caught, missed, extra = run_e1_test(ws, test)
        results.append(
            {"test_id": test.test_id, "поймано": caught, "пропущено": missed, "лишние": extra}
        )
    missed_total = [r["test_id"] for r in results if r.get("пропущено")]
    executed = [r["test_id"] for r in results if not r.get("skipped")]
    # «зелёная» — только когда что-то действительно проверено: пустой корпус или
    # сплошь пропущенные Э2-тесты доказательством ничего не являются (FR-RG-3)
    green = bool(executed) and not missed_total
    if not tests:
        reason = "корпус пуст"
    elif not executed:
        reason = "все тесты пропущены (Э2 без --llm/API)"
    elif missed_total:
        reason = "пропущены ожидаемые флаги"
    else:
        reason = ""
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "всего": len(tests),
        "выполнено": len(executed),
        "зелёная": green,
        "причина": reason,
        "провалено": missed_total,
        "результаты": results,
        "хэши": environment_hashes(ws),
    }
    guard.write_text(
        ws.regression / "report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return report


def load_report(ws: Workspace) -> dict | None:
    path = ws.regression / "report.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def is_stale(ws: Workspace) -> bool:
    """Отчёт есть, но конфигурация (модели/ключи конфига проверок/шаблоны/нормы) с тех пор изменилась."""
    report = load_report(ws)
    return report is not None and report.get("хэши") != environment_hashes(ws)


def is_green(ws: Workspace) -> bool | None:
    """None — регрессия ещё не запускалась ИЛИ отчёт устарел (изменились конфиг.yaml,
    шаблоны или нормы — FR-RG-3 требует нового прогона)."""
    report = load_report(ws)
    if report is None or report.get("хэши") != environment_hashes(ws):
        return None
    return bool(report.get("зелёная"))
