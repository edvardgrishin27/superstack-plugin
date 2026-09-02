#!/usr/bin/env python3
"""SUPERSTACK — /what. Состояние одной строкой, а не меню и не пересказ.

Зачем это отдельный инструмент, а не абзац в инструкции.

Целевой человек не помнит, на чём остановился, и не будет читать список
вариантов. Вопрос «где я и что дальше» имеет ровно один честный ответ —
и этот ответ обязан читаться из фактов на диске, а не из памяти разговора:
модель, отвечающая по памяти, рано или поздно перепутает сессию или соврёт
про состояние, которое сама не проверяла.

Источники состояния — уже существующие гейты системы, не новые эвристики:

  · флаг паузы (tools/pause.sh) — если он есть, всё остальное неважно;
  · ход прогона /go в .superstack/ — состояние панели (state.json, его пишет
    tools/progress.py) и спека (spec.md, её пишет фаза 2). Это ПЕРВЫЙ источник
    после паузы: раньше здесь смотрели только в .claude/specs/ — папку, в
    которую не пишет ни один инструмент системы, — и на второй день после
    полного прогона человек слышал «работа ещё не начата»;
  · спека в .claude/specs/ — запасной источник, оставлен ради проектов,
    заведённых до того, как /go начал писать в .superstack/. Форму держит
    tools/spec_lint.py, это НЕ переизобретено здесь;
  · гейт верификации (tools/verify.py) — то самое место, которое решает,
    можно ли закрыть ход в /go.

Порядок проверки — приоритет, а не список. Печатается РОВНО ОДНО состояние:
то, что важнее всего прямо сейчас. Пауза важнее незаписанной спеки, спека
важнее красных тестов — потому что, пока система стоит на паузе, разбираться
в тестах бессмысленно.

  python3 what.py [--json] [каталог-проекта]

  код 0 — состояние определено (само состояние не является ни успехом, ни
          провалом — это факт, а не вердикт), 3 — ошибка вызова
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Соседние инструменты лежат в этом же каталоге, но при импорте из теста
# (spec_from_file_location) каталог скрипта в sys.path не попадает — тогда
# «import verify» упал бы там, где при обычном запуске работает.
sys.path.insert(0, str(Path(__file__).resolve().parent))


import plain_ru  # noqa: E402
from spec_lint import lint_text  # noqa: E402
import verify  # noqa: E402

HOME = Path.home()
CLAUDE = HOME / ".claude"
STATE = CLAUDE / "superstack"

#: Папка прогона /go внутри проекта. Всё, что /go знает о работе, лежит здесь:
#: state.json (этап и на ком ход), spec.md, бриф. Имя совпадает с тем, которое
#: называет фаза 0 скилла go, и меняться в одиночку не может.
RUN = ".superstack"


# --------------------------------------------------------------------------
# источники состояния
# --------------------------------------------------------------------------
def pause_since() -> str | None:
    """Содержимое флага паузы — или None, если система не на паузе.

    Нечитаемый флаг (права, гонка удаления) считается ПАУЗОЙ С НЕИЗВЕСТНОГО
    момента, а не отсутствием паузы: файл на диске есть, и трактовать его
    как «паузы нет» означало бы соврать про самый важный из фактов.
    """
    flag = STATE / "PAUSE"
    if not flag.exists():
        return None
    try:
        return flag.read_text(encoding="utf-8").strip() or "?"
    except OSError:
        return "?"


def run_phase(project: Path) -> dict | None:
    """Этап прогона /go и тот, на ком ход — из `.superstack/state.json`.

    Читается ровно то, что пишет progress.py set_phase: name, owner, detail.
    Ни файла, ни разбора, ни имени этапа — None, и вопрос уходит следующему
    источнику. Молчание здесь дешевле догадки: выдуманный этап человек примет
    за факт и пойдёт продолжать не оттуда.
    """
    try:
        data = json.loads((project / RUN / "state.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    phase = data.get("phase") if isinstance(data, dict) else None
    if not isinstance(phase, dict) or not phase.get("name"):
        return None
    return phase


def run_spec(project: Path) -> Path | None:
    """Спека прогона /go — `.superstack/spec.md`. Нет файла — None."""
    spec = project / RUN / "spec.md"
    return spec if spec.is_file() else None


def find_specs(project: Path) -> list[Path]:
    """Спеки в `.claude/specs/` — ЗАПАСНОЙ источник, не основной.

    Сюда не пишет ни один инструмент системы: /go кладёт спеку в
    `.superstack/spec.md`. Папка оставлена ради проектов, заведённых руками до
    того, как это стало правилом; самая свежая первой, каталога нет — пусто.
    """
    specs_dir = project / ".claude" / "specs"
    if not specs_dir.is_dir():
        return []
    files = [f for f in specs_dir.glob("*.md") if f.is_file()]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


# --------------------------------------------------------------------------
# вычисление состояния
# --------------------------------------------------------------------------
def evaluate(project: Path) -> dict:
    """Состояние человека прямо сейчас: одна причина, одна строка, один шаг.

    Возвращает {"reason", "line", "next"} и хвост доказательств под ключом
    "evidence" — по нему можно перепроверить, откуда взялась строка, не
    поверив ей на слово.
    """
    paused = pause_since()
    if paused is not None:
        return {
            "reason": "paused",
            "line": f"SUPERSTACK на паузе с {paused}.",
            # Человек мог попасть сюда через /stop — скилл, написанный ровно
            # для того, чтобы останавливать систему СЛОВОМ, не открывая
            # терминал. Команда оболочки или путь к tools/ в ответе на
            # «где я» отменяла бы это обещание: не-разработчик такую строку
            # не выполнит. /stop сам умеет снимать паузу с фразы «включи»
            # (skills/stop/SKILL.md) — next называет именно её, а не файл.
            "next": "скажи «включи» или позови /stop — сама пауза не пройдёт",
            "evidence": {"pause_flag": str(STATE / "PAUSE")},
        }

    phase = run_phase(project)
    if phase is not None:
        name = " ".join(str(phase["name"]).split())
        owner = str(phase.get("owner") or "")
        # Держателя называем теми же словами, что и панель: plain_ru берёт их
        # из data/plain-ru.json, откуда их читает и live_panel.py. Вторая
        # формулировка того же факта означала бы, что экран и ответ на
        # «где я» расходятся, — и человек не знает, чему верить.
        who = plain_ru.role_word(owner) if owner else "чей сейчас ход — не записано"
        detail = " ".join(str(phase.get("detail") or "").split())
        if owner == "человек":
            step = f"дело за тобой: {detail}" if detail else "дело за тобой — скажи, что решил"
        else:
            step = "скажи «продолжи» — подхвачу с этого места"
        return {
            "reason": "go-phase",
            "line": f"Остановились на этапе «{name}»: {who}.",
            "next": step,
            "evidence": {"state": str(project / RUN / "state.json"),
                         "phase": phase},
        }

    spec = run_spec(project)
    if spec is not None:
        return {
            "reason": "go-spec",
            "line": "Спека написана, стройка не начиналась.",
            "next": "скажи «продолжи» — разобью работу на части и начну строить",
            "evidence": {"spec": str(spec)},
        }

    specs = find_specs(project)
    if not specs:
        return {
            "reason": "no-spec",
            "line": "Работа ещё не начата: ни одной спеки нет.",
            "next": "скажи, что хочешь построить — это начнёт /go",
            "evidence": {"run_dir": str(project / RUN),
                         "specs_dir": str(project / ".claude" / "specs")},
        }

    newest = specs[0]
    try:
        text = newest.read_text(encoding="utf-8-sig")
    except OSError as e:
        return {
            "reason": "spec-unreadable",
            "line": f"Спеку «{newest.name}» не удалось прочитать.",
            "next": f"почини доступ к файлу: {newest}",
            "evidence": {"spec": str(newest), "error": str(e)},
        }

    lint = lint_text(text)
    if lint["status"] != "clean":
        first = lint["problems"][0]
        return {
            "reason": "spec-problems",
            "line": f"Спека «{newest.name}» недописана: {first['message']}.",
            "next": "дополни спеку и вызови /go снова",
            "evidence": {"spec": str(newest), "problems": lint["problems"]},
        }

    checks = verify.detect_checks(project)
    unrunnable = verify.unrunnable_checks(project)
    if not checks and not unrunnable:
        return {
            "reason": "no-checks",
            "line": f"Спека «{newest.name}» готова, но само дело ещё нечем проверить.",
            "next": "заведи хотя бы один тест — иначе «готово» нечем подтвердить",
            "evidence": {"spec": str(newest), "project": str(project)},
        }

    results = [verify._run(c, project) for c in checks]
    v = verify.verdict(results, project, tuple(unrunnable))

    if v["status"] == "pass":
        return {
            "reason": "verified",
            "line": "Всё сделано и проверено: гейт зелёный.",
            "next": "можно показывать результат человеку",
            "evidence": {"spec": str(newest), "verify": v},
        }
    if v["status"] == "fail":
        return {
            "reason": "verify-fail",
            "line": f"Тесты красные: {v['blockers'][0]}.",
            "next": v["next"],
            "evidence": {"spec": str(newest), "verify": v},
        }
    return {
        "reason": "verify-absent",
        "line": f"Проверить получилось не всё: {v['blockers'][0]}." if v["blockers"]
                else "Проверить получилось не всё.",
        "next": v["next"],
        "evidence": {"spec": str(newest), "verify": v},
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--json"]
    quiet = "--json" in sys.argv[1:]
    if len(args) > 1:
        print("вызов: what.py [--json] [каталог-проекта]", file=sys.stderr)
        return 3
    project = Path(args[0]).expanduser().resolve() if args else Path.cwd()
    if not project.is_dir():
        print(f"НЕ УДАЛОСЬ: каталога нет — {project}", file=sys.stderr)
        return 3

    result = evaluate(project)
    if not quiet:
        print(result["line"], file=sys.stderr)
        print(f"дальше: {result['next']}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
