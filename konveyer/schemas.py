"""Схемы данных конвейера (раздел 5 ТЗ): выгрузки, вердикты, флаги, правки.

Выгрузки валидируются этими схемами перед записью (FR-X2); невалидная
выгрузка не записывается.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- выгрузки 5.2


class StopRule(BaseModel):
    """Строка stoplists.json — из 03/04 и Р-016 (усилители)."""

    scope: Literal["0.3", "0.4"]
    rule_id: str
    items: list[str]
    applies_to: dict = Field(default_factory=dict)  # {focal?|year?|all}
    action: Literal["запрет", "флаг"] = "запрет"
    kind: Literal["лексика", "усилитель", "проза"] = "лексика"  # «проза» — запреты линий 03 фразами, не словами


class ChronicleEvent(BaseModel):
    """Строка chronicle.json — историческая хроника 17 (анахронизмы, чек-лист 4.2)."""

    date: str
    event: str
    status: str = "✓"        # ✓ подтверждено, ⚠ требует проверки (Конституция: опираться только на ✓)
    month: int | None = None  # для среза «месяц главы ± 1»


class NarrationRules(BaseModel):
    """narration.json — правила повествования: общие законы (текст в окно) и таблица фокалов (имена)."""

    file: str = ""
    laws: str = ""
    focals_text: str = ""
    focal_names: list[str] = Field(default_factory=list)


class MethodNote(BaseModel):
    """method.json — замысел серии: тема и принципы (материал аналитика драматургии, Писателю не идёт)."""

    file: str = ""
    theme: str = ""
    principles: str = ""


class WorldEntry(BaseModel):
    """world.json / objects.json / places.json — запись мира: организация, предмет, место."""

    name: str
    description: str = ""
    note: str = ""
    kind: str = ""
    file: str = ""


class VolumePlan(BaseModel):
    """volumes.json — план томов."""

    volume: int
    period: str = ""
    theme: str = ""
    chapters: str = ""


class Decision(BaseModel):
    """decisions.json — решение автора из журнала решений."""

    decision_id: str
    title: str = ""
    date: str = ""
    statement: str = ""
    rationale: str = ""
    text: str = ""
    line: int = 0


class Checklist(BaseModel):
    """checklists.json — чек-лист верификации (текст документа целиком, для Э2)."""

    file: str = ""
    text: str = ""


class ChronologyEvent(BaseModel):
    """Строка chronology.json — генеральная хронология фабулы 12 («документ-позвоночник» цикла).

    Формат строки канона: `**Ф-ГОД-№** · дата · событие · участники · [видимость] · хвост`.
    Видимость: `[Читатель: т.N гл.M]` — где читатель узнаёт; `[Скрыто]`; `[Фон]`."""

    event_id: str                 # «Ф-1926-02»
    year: int | None = None       # год из идентификатора
    date: str = ""                # «15.04», «май 1913», «≈1921–24»
    event: str = ""
    participants: str = ""
    visibility: str = ""          # «[Читатель: т.1 гл.32]» целиком
    volumes: list[int] = Field(default_factory=list)   # тома из видимости
    chapters: list[int] = Field(default_factory=list)  # главы из видимости
    volume: int | None = None     # том раздела документа («## Том 2 — 1927 «Джентльмен»»)
    section: str = ""             # заголовок раздела
    section_years: list[int] = Field(default_factory=list)  # годы раздела («1946–1947» → [1946, 1947])
    historical: bool = False      # «(ист.)» — обязательна сверка с хроникой
    hidden: bool = False          # «[Скрыто]»
    background: bool = False      # «[Фон]»
    open_question: bool = False   # «⚠» — открытое решение автора
    note: str = ""                # хвост после видимости («Закладка → т.6», «(Р-007)»)
    line: int = 0                 # строка документа 12 (для находок линтера)


class MatrixFact(BaseModel):
    """Строка matrix.json — из 31. from_chapter=None → субъект НЕ знает."""

    fact_id: str
    fact: str
    subject: str
    from_chapter: int | None = None
    source: str = ""
    note: str = ""


class Plant(BaseModel):
    """Строка plants.json (закладки) — из 3.2/реестра тома."""

    plant_id: str
    what: str
    placed: dict  # {vol, ch}
    chapters: list[int] = Field(default_factory=list)  # все главы, где лежит (реестр §7)
    fires: list[dict] = Field(default_factory=list)  # [{vol, ch?}]
    status: str = ""


class Norm(BaseModel):
    """Числовой порог Э1 — из таблицы норм документа стиля. Единственный источник порогов (FR-V1-2)."""

    min: float | None = None
    max: float | None = None
    brak: float | None = None
    unit: str = ""
    source: str = ""   # документ и секция, откуда норма (ставит извлечение типа «стиль»)


class ContinuityEvent(BaseModel):
    """Строка continuity.json — из 33."""

    date: str
    event: str
    chapters: str = ""
    note: str = ""


class Scene(BaseModel):
    """Карточка сцены поглавника (23): место · участники · цель · входит/выходит · кладём.

    Поля хранятся как в поглавнике (без фильтра): что из них видит Писатель, решает компилятор
    (клаузы, адресованные читателю/инструменту, в окно не выводятся — FR-C3)."""

    number: str = ""        # «5.1»
    place: str = ""
    time: str = ""          # «за полночь», «утро» — вынесено из места
    participants: str = ""  # строка как в поглавнике («Иван; Пётр (появление в финале)»)
    goal: str = ""
    enters: str = ""        # чем входит фокал
    exits: str = ""         # чем выходит фокал
    plants: list[str] = Field(default_factory=list)  # «кладём: …» по элементам через «;»


class Brief(BaseModel):
    """Глава плана глав (briefs.json)."""

    chapter: int
    line: int = 0                                          # строка документа плана (для находок линтера)
    volume: int = 1
    date: str = ""
    year: int | None = None
    focal: str = ""
    scenes: list[str] = Field(default_factory=list)          # строки сцен (совместимость панели/Э2)
    scene_cards: list[Scene] = Field(default_factory=list)   # структурные карточки сцен (23)
    participants: list[str] = Field(default_factory=list)  # персонажи сцен главы
    beats: list[str] = Field(default_factory=list)
    bans: list[str] = Field(default_factory=list)       # запреты
    not_knows: list[str] = Field(default_factory=list)  # явные «НЕ знает»
    volume_words: int | None = None
    plants: list[str] = Field(default_factory=list)     # plant_id, назначенные главе
    # документы-вставки главы из поглавника («→ ДОКУМЕНТ №N (после главы): …»)
    documents: list[str] = Field(default_factory=list)
    # колонка сетки «Что нового знает читатель» — для Э2/автора/линтера; Писателю не передаётся
    reader_learns: str = ""


class Dose(BaseModel):
    """Строка doses.json — §5 реестра доз прошлого (канал воспоминаний фокала).

    Единственный разрешённый канал прошлого внутри тома; в окно идёт ТОЛЬКО доза своей главы (FR-C3)."""

    dose_id: str            # «№1»
    chapter: int
    volume: int = 1
    trigger: str = ""
    reader_gets: str = ""       # «Что получает читатель»
    reader_not_gets: str = ""   # «Чего НЕ получает»
    form: str = ""              # вводный абзац §5 (что такое доза) — общий для всех доз
    rule: str = ""              # «Правило доз» — фразы, относящиеся к этой дозе (общие + адресные «в дозе №N»)


class DocumentSpec(BaseModel):
    """Строка documents.json — §6 реестра документов-вставок (рапорты, письма, протоколы)."""

    number: int
    after_chapter: int
    volume: int = 1
    kind: str = ""          # из заголовка раздела: «рапорты», «письма»
    style: str = ""
    divergence: str = ""    # «Расхождение с правдой, которую видел читатель»
    form: str = ""          # вводный абзац §6 (как верстается документ) — общий для всех
    scale: str = ""         # строка языковой шкалы, относящаяся к этому номеру («№1–3 — …»)


class Dossier(BaseModel):
    """Карточка персонажа для окна (FR-C1): профиль, физика, речевой паспорт, опознавательный код, отношения.
    Служебные поля (статус, арка, файл, строки секций) — для линтера; Писателю не показываются (FR-DT-3)."""

    name: str
    profile: str = ""
    physique: str = ""
    speech: str = ""
    code: str = ""  # «Опознавательный код»: приметы, по которым персонажа опознают (перстень, перчатка…)
    relations: dict[str, str] = Field(default_factory=dict)
    status: str = ""          # «Статус по томам» (жив/гибнет/фокален с т.N) — линтеру, не Писателю
    arc: str = ""             # арка по томам — линтеру и аналитику, не Писателю
    file: str = ""            # документ канона (относительно библиотеки)
    line: int = 0             # строка заголовка карточки
    sections: dict[str, int] = Field(default_factory=dict)  # секция → строка (для находок)
    born_year: int | None = None   # «Рожд. ≈ГГГГ» — для проверки возраста
    ages: dict[str, int] = Field(default_factory=dict)      # «т.1» → 55: возраст по томам
    refs: list[str] = Field(default_factory=list)           # ссылки [[Имя]] в карточке


class InfoBan(BaseModel):
    """Запрет информрежима (FR-WN-3/FR-WN-4): резервы будущих томов и тайны с главой раскрытия."""

    ban_id: str
    text: str
    known_text: str = ""  # колонка «кто знает» как в документе (разбирается в known_by по известным именам)
    line: int = 0
    until_volume: int | None = None
    # реестр тайн: глава, в которой читатель узнаёт (до неё — «НЕ упоминать»)
    until_chapter: int | None = None
    secret: bool = False  # текст — содержание тайны: Писателю сообщать нельзя (FR-C3)
    # кто из персонажей знает тайну и с какой главы (0 = всегда) — колонка «Персонажи знают»
    known_by: dict[str, int] = Field(default_factory=dict)
    # маркеры фильтра окна: фразы досье с этими словами вычищаются, пока фокал тайну не знает
    markers: list[str] = Field(default_factory=list)

    def known_to(self, name: str, chapter: int) -> bool:
        return name in self.known_by and self.known_by[name] <= chapter


# --------------------------------------------------------- вердикты и флаги


class CircleStep(BaseModel):
    """Шаг круга истории (Р-020): для тома/части — диапазон глав, для главы — место в тексте."""

    n: int
    name: str
    text: str = ""
    chapters: str = ""               # «гл. 1–3» / «сц. 5.1» — как в каноне
    from_chapter: int | None = None  # разобранный диапазон (только том/часть)
    to_chapter: int | None = None


class Act(BaseModel):
    """Акт тома (acts.json — таблица актов документа каркасов или отдельного документа актов)."""

    act: int
    title: str = ""
    from_chapter: int
    to_chapter: int
    chapters_text: str = ""
    parts: str = ""   # какие части реестра 2.2 покрывает («III–IV»)
    steps: str = ""   # шаги круга тома, за которые отвечает акт («5–6 «Обретение», «Расплата»»)


class StoryCircle(BaseModel):
    """Круг истории (circles.json — из 2.1): несущий каркас драматургии тома, акта, главы."""

    scope: Literal["книга", "акт", "глава"]
    key: int | None = None
    title: str = ""
    summary: str = ""
    weak_spot: str = ""
    steps: list[CircleStep] = Field(default_factory=list)

    def steps_for_chapter(self, chapter: int) -> list[CircleStep]:
        """Шаги тома/акта, на которые приходится глава."""
        return [
            st for st in self.steps
            if st.from_chapter is not None and st.from_chapter <= chapter <= (st.to_chapter or st.from_chapter)
        ]


class Arc(BaseModel):
    """Строка таблицы арок тома (arcs.json — документ 2.5 `22_Арки_Том{N}.md`, Р-025, по Уэйланд):
    персонаж × акт. Писателю выводится ТОЛЬКО `visible` («что видно снаружи», через фильтр тайн Р-022);
    ложь/желание/потребность/положение на арке — внутренний инструмент автора и аналитика кругов."""

    character: str
    act: int
    lie: str = ""        # «Ложь» — во что персонаж верит ошибочно
    want: str = ""       # «Желание» — чего добивается
    need: str = ""       # «Потребность» — что ему на самом деле нужно
    position: str = ""   # «Где на арке» в этом акте
    visible: str = ""    # «Что видно снаружи» — единственная колонка для окна Писателя

    @property
    def filled(self) -> bool:
        """Есть ли в строке хоть одна заполненная ячейка (пустые и «⚠ заполнить» — скелет)."""
        return any(v and "⚠" not in v for v in (self.lie, self.want, self.need, self.position, self.visible))


class CheckResult(BaseModel):
    """Результат одной проверки Э1 (FR-V1.9)."""

    check_id: str
    status: Literal["PASS", "FLAG", "BRAK"]
    threshold: str
    actual: str
    quotes: list[str] = Field(default_factory=list)
    rule_source: str = ""
    note: str = ""


class Verdict(BaseModel):
    chapter: int
    draft: int
    checks: list[CheckResult]

    @property
    def has_brak(self) -> bool:
        return any(c.status == "BRAK" for c in self.checks)

    @property
    def flags(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status != "PASS"]


class Flag(BaseModel):
    """Флаг Э2 (FR-V2.2). kind=samovolka требует решения автора."""

    flag_id: str = Field(pattern=r"^[\w.\-]+$")  # попадает в id/href разметки — только безопасные символы
    type: str
    severity: Literal["критично", "важно", "мелочь"] = "важно"
    quote: str
    rule: str
    recommendation: str = ""
    kind: Literal["violation", "samovolka"] = "violation"


class Resolution(BaseModel):
    """Решение автора по самоволке (FR-V2.5)."""

    flag_id: str = Field(pattern=r"^[\w.\-]+$")
    decision: Literal["вычеркнуть", "канонизировать", "отклонить"] | None = None
    target_registry: str | None = Field(default=None, pattern=r"^[\w.\-]+$")
    reason: str = ""  # причина отклонения флага (FR-RV-2) — уходит в журнал отклонённых флагов


# ------------------------------------------------------------------- правки


class Edit(BaseModel):
    """Строка правки.jsonl (5.3). Класс проставляет Канонист, подтверждает автор."""

    chapter: int
    seq: int
    before: str
    after: str
    class_: Literal["вкус", "факт", "канон"] | None = Field(default=None, alias="class")
    note: str = ""

    model_config = {"populate_by_name": True}


class DiffReport(BaseModel):
    """Отчёт дифф-контроля (FR-V1.10)."""

    chapter: int
    draft_before: int
    draft_after: int
    applied_share: float
    not_applied: list[int] = Field(default_factory=list)      # seq невнесённых правок
    unauthorized: list[str] = Field(default_factory=list)     # самовольные изменения
    # свободные указания (УКАЗАНИЕ:): механически не проверяемы, приёмку не блокируют
    unverifiable: list[int] = Field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.not_applied and not self.unauthorized


# ---------------------------------------------------------------- регрессия


class GoldenTest(BaseModel):
    """Золотой тест (FR-R1)."""

    test_id: str
    fragment: str
    context_slice: dict = Field(default_factory=dict)  # chapter, focal, year, window?
    expected_flags: list[str] = Field(default_factory=list)  # check_id / type
    echelon: Literal["Э1", "Э2"] = "Э1"


# ------------------------------------------------------------- линтер канона


class LintFix(BaseModel):
    """Механическое исправление: в файле file на строке line заменить old → new (применяет автор)."""

    file: str
    line: int
    old: str
    new: str
    note: str = ""


class LintFinding(BaseModel):
    code: str
    severity: Literal["ошибка", "предупреждение", "заметка"]
    file: str
    line: int | None = None
    message: str
    quote: str = ""
    fix: LintFix | None = None
    source: Literal["машина", "модель"] = "машина"


class LintReport(BaseModel):
    ts: str
    fingerprint: str = ""   # отпечаток канона (+ том), для которого снят отчёт (FR-LT-5)
    files_checked: int = 0
    findings: list[LintFinding] = Field(default_factory=list)
    errors: int = 0
    warnings: int = 0
    notes: int = 0

    @property
    def ok(self) -> bool:
        return self.errors == 0
