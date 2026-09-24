"""`доктор` называет дубли документов типов с множественностью «один»/«по_тому» (каркас стартового комплекта рядом
с документом того же типа после онбординга) и подсказывает, какой из них — незаполненный каркас (FR-LC-2, 5.5)."""

from pathlib import Path

from konveyer import manifest as manifest_mod, project


def test_доктор_называет_дубли_типов(tmp_path: Path):
    created = project.create(project.ProjectSpec(root=tmp_path / "серия", name="Проба", git=False, volumes=1))
    lib = created.library
    checks = project.readiness(created.root, lib)
    assert not any("представлен дважды" in c.label for c in checks)
    # второй документ стиля (как после онбординга материалов автора) рядом с каркасом комплекта
    style = next(p for p in lib.glob("*.md") if "Стиль" in p.name)
    second = lib / "Мой_стиль.md"
    second.write_text("# Стиль\n\n| id | мин | макс | брак |\n|---|---|---|---|\n| объём_главы | 2000 | 4000 | — |\n", encoding="utf-8")
    man = manifest_mod.load(created.root)
    man.библиотека.append(manifest_mod.LibraryEntry(файл=second.name, тип="стиль"))
    manifest_mod.save(created.root, man)
    checks = project.readiness(created.root, lib)
    dup = next(c for c in checks if "представлен дважды" in c.label)
    assert dup.ok is False and "стиль" in dup.label and style.name in dup.label and second.name in dup.label
    assert style.name in dup.hint and "--без-комплекта" in dup.hint  # каркас с «⚠ заполнить» назван как лишний
    assert not project.ready_for_tact(checks)
