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
  · ход прогона /go в .superstack/ — записанный этап (state.json, его пишет
    tools/progress.py), а если он не записан — этап ПО СЛЕДАМ на диске
    (tools/derive_phase.py: бриф, спека, задачи, возвраты). Это ПЕРВЫЙ источник
    после паузы: раньше здесь смотрели только в .claude/specs/ — папку, в
    которую не пишет ни один инструмент системы, — и на второй день после
    полного прогона человек слышал «работа ещё не начата». Запись этапа —
    отдельная команда, и её забывают: без следов ответ снова уезжал к спеке и
    звучал как «стройка не начиналась» посреди идущих проверок;
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


import derive_phase  # noqa: E402
import lang as языки  # noqa: E402
import plain_ru  # noqa: E402
from spec_lint import lint_text  # noqa: E402
import verify  # noqa: E402

_ПРОБА = None


def _проба():
    """Сборщик фактов как библиотека: резолвер профиля в системе ОДИН.

    Каталог настроек здесь собирался литералом `Path.home() / ".claude"`, а
    хост уважает `CLAUDE_CONFIG_DIR` и уносит туда всё, что документация зовёт
    `~/.claude`. Свой разбор этой переменной был бы вторым списком оговорок, и
    разошёлся бы он молча — в тот день, когда оговорку добавят, а поправят
    только сборщик.

    Не нашёлся — это ОТКАЗ вслух. Тихий переход на домашний каталог и есть тот
    второй резолвер, только включающийся тогда, когда о нём никто не помнит.
    """
    global _ПРОБА
    if _ПРОБА is None:
        import importlib.util
        путь = Path(__file__).resolve().parent / "probe" / "collect.py"
        if not путь.is_file():
            raise SystemExit(
                f"ОТКАЗ: сборщик фактов не найден ({путь}) — спросить, какой "
                f"каталог настроек читает хост, не у кого, а гадать про чужой "
                f"профиль этот инструмент не будет.")
        спек = importlib.util.spec_from_file_location("ss_collect_for_what", путь)
        _ПРОБА = importlib.util.module_from_spec(спек)
        спек.loader.exec_module(_ПРОБА)
    return _ПРОБА


HOME = Path.home()

#: Каталог настроек, который читает ХОСТ, и то, откуда взят этот путь. Пара, а
#: не один путь: состояние без названного профиля нечем перепроверить — человек
#: читает «работа не начата» и не знает, в чьём каталоге её искали.
CLAUDE, CLAUDE_SOURCE = _проба().config_dir()

#: Свой каталог системы — и за назначенным профилем он НЕ едет. Флаг паузы
#: кладёт `tools/pause.py` по домашнему адресу, и там же его ищут остальные
#: инструменты. Инструмент, который смотрит в другое место, не «не нашёл
#: паузу» — он её не соблюдает, и узнаётся это ровно тогда, когда всё уже
#: идёт не так.
STATE = HOME / ".claude" / "superstack"

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


def derived_phase(project: Path) -> dict | None:
    """Этап ПО СЛЕДАМ на диске, когда записанного этапа нет.

    Запись этапа делает отдельная команда, и её забывают: прогон уходит
    вперёд, а `state.json` остаётся без `phase` — либо его пишет не /go, либо
    строку затёрли. Прежде этот случай отправлял человека к спеке, и он слышал
    «стройка не начиналась» на прогоне, где уже шли проверки.

    Спрашивается тот же `derive_phase.py`, которым живёт панель: два своих
    ответа на вопрос «какой сейчас этап» разошлись бы при первой правке, и
    человек не знал бы, чему верить — экрану или ответу на «где я».

    Возвращает None в двух случаях, и оба честные: папки прогона нет вовсе;
    следов работы в ней нет ни одного — тогда назвать этап значило бы выдумать
    его, а выдуманный этап человек примет за факт.
    """
    run = project / RUN
    if not run.is_dir():
        return None
    got = derive_phase.derive(run)
    if not got.get("facts"):
        return None
    return {"name": got["name"], "owner": got.get("owner") or "",
            "detail": got.get("why") or "", "derived": True}


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
    """Состояние человека прямо сейчас плюс профиль, к которому оно относится.

    Возвращает {"reason", "line", "next"} и хвост доказательств под ключом
    "evidence" — по нему можно перепроверить, откуда взялась строка, не
    поверив ей на слово.

    Профиль дописывается ЗДЕСЬ, а не в каждой ветке: веток девять, и первая же
    забытая означала бы состояние без имени системы, к которой оно относится.
    Стоит он в КАЖДОМ ответе, потому что «пусто» в своём профиле и «пусто» в
    чужом читаются одинаково, а означают разное — и различает их только
    названная причина, по которой прочитан именно этот каталог.
    """
    итог = _состояние(project)
    итог["config_dir"] = str(CLAUDE)
    итог["config_dir_source"] = CLAUDE_SOURCE
    return итог


def _состояние(project: Path) -> dict:
    """Приоритет источников состояния: печатается РОВНО ОДНО, самое важное."""
    # Язык человека — запись прогона, а не догадка по его последней фразе:
    # /what зовут в любой момент, и текст ответа обязан совпадать с языком
    # панели и отчёта, а не с тем, чем эту команду сегодня позвали.
    я = языки.read(project)
    paused = pause_since()
    if paused is not None:
        return {
            "reason": "paused",
            "line": языки.t("what_paused_line", я, since=paused),
            # Человек мог попасть сюда через /stop — скилл, написанный ровно
            # для того, чтобы останавливать систему СЛОВОМ, не открывая
            # терминал. Команда оболочки или путь к tools/ в ответе на
            # «где я» отменяла бы это обещание: не-разработчик такую строку
            # не выполнит. /stop сам умеет снимать паузу с фразы «включи»
            # (skills/stop/SKILL.md) — next называет именно её, а не файл.
            "next": языки.t("what_paused_next", я),
            "evidence": {"pause_flag": str(STATE / "PAUSE")},
        }

    phase = run_phase(project)
    откуда = {"state": str(project / RUN / "state.json")}
    if phase is None:
        # Записанного этапа нет — спрашиваем следы. Один и тот же вопрос,
        # один и тот же прибор, что у панели.
        phase = derived_phase(project)
        откуда = {"run": str(project / RUN), "по следам": True}
    if phase is not None:
        name = " ".join(str(phase["name"]).split())
        owner = str(phase.get("owner") or "")
        # Держателя называем теми же словами, что и панель: plain_ru берёт их
        # из data/plain-ru.json, откуда их читает и live_panel.py. Вторая
        # формулировка того же факта означала бы, что экран и ответ на
        # «где я» расходятся, — и человек не знает, чему верить.
        who = (языки.role_word(owner, я) if owner
               else языки.t("what_owner_unknown", я))
        detail = " ".join(str(phase.get("detail") or "").split())
        if owner == "человек":
            step = (языки.t("what_step_human", я, detail=detail) if detail
                    else языки.t("what_step_human_bare", я))
        else:
            step = языки.t("what_step_resume", я)
        return {
            "reason": "go-phase",
            # Название этапа переводится по тому же словарю, что и на панели:
            # ключ этапа остаётся русским (он же ключ состояния), на экран
            # идёт пара. Свой перевод здесь означал бы, что экран и ответ на
            # «где я» называют одно место двумя именами.
            "line": языки.t("what_phase_line", я,
                            name=языки.phase_name(name, я), who=who),
            "next": step,
            "evidence": {**откуда, "phase": phase},
        }

    specs = find_specs(project)
    if not specs:
        return {
            "reason": "no-spec",
            "line": языки.t("what_no_spec_line", я),
            "next": языки.t("what_no_spec_next", я),
            "evidence": {"run_dir": str(project / RUN),
                         "specs_dir": str(project / ".claude" / "specs")},
        }

    newest = specs[0]
    try:
        text = newest.read_text(encoding="utf-8-sig")
    except OSError as e:
        return {
            "reason": "spec-unreadable",
            "line": языки.t("what_spec_unreadable_line", я, name=newest.name),
            "next": языки.t("what_spec_unreadable_next", я, path=newest),
            "evidence": {"spec": str(newest), "error": str(e)},
        }

    lint = lint_text(text)
    if lint["status"] != "clean":
        first = lint["problems"][0]
        return {
            "reason": "spec-problems",
            "line": языки.t("what_spec_problems_line", я, name=newest.name,
                            problem=first["message"]),
            "next": языки.t("what_spec_problems_next", я),
            "evidence": {"spec": str(newest), "problems": lint["problems"]},
        }

    checks = verify.detect_checks(project)
    unrunnable = verify.unrunnable_checks(project)
    if not checks and not unrunnable:
        return {
            "reason": "no-checks",
            "line": языки.t("what_no_checks_line", я, name=newest.name),
            "next": языки.t("what_no_checks_next", я),
            "evidence": {"spec": str(newest), "project": str(project)},
        }

    results = [verify._run(c, project) for c in checks]
    v = verify.verdict(results, project, tuple(unrunnable))

    if v["status"] == "pass":
        return {
            "reason": "verified",
            "line": языки.t("what_verified_line", я),
            "next": языки.t("what_verified_next", я),
            "evidence": {"spec": str(newest), "verify": v},
        }
    if v["status"] == "fail":
        return {
            "reason": "verify-fail",
            "line": языки.t("what_fail_line", я, blocker=v["blockers"][0]),
            "next": v["next"],
            "evidence": {"spec": str(newest), "verify": v},
        }
    return {
        "reason": "verify-absent",
        "line": (языки.t("what_absent_line", я, blocker=v["blockers"][0])
                 if v["blockers"] else языки.t("what_absent_bare", я)),
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
        print(языки.t("next_prefix", языки.read(project))
              + f" {result['next']}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
