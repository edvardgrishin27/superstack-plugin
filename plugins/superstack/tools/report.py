#!/usr/bin/env python3
"""SUPERSTACK — финальный отчёт прогона. Из файлов, а не по памяти.

Зачем это код.

К последней фазе контекст ведущего самый загрязнённый за прогон: в нём осели
восемь возвратов, три ревью и десяток починок. Отчёт, написанный по памяти,
получается пересказом впечатлений — и первым из него выпадает то, что человеку
нужнее всего: чего НЕ сделали.

Здесь отчёт собирается из того, что лежит на диске: состояние частей, манифест
требований, вердикт слепой приёмки, находки ревью. Ничего не додумывается: чего
в файлах нет, того нет и в отчёте.

Три правила, ради которых стоило писать инструмент:

  · ГОТОВО — только доказанное кодом возврата. «Со слов помощника» идёт
    отдельной строкой и другим словом.
  · ЧТО ЖДЁТ ЧЕЛОВЕКА — раньше похвал. Заглушки, пустые настройки и открытые
    вопросы приёмки собраны в один список: это единственное, что он должен
    сделать сам.
  · ЧЕГО НЕ ПРОВЕРИЛИ — отдельным разделом. «Не нашли» и «не смогли проверить»
    разные утверждения, и второе нельзя прятать в первое.
  · КАК ОТКРЫТЬ — последним. Прогон, кончившийся текстом, оставляет человека
    с описанием того, чего он ни разу не видел: за весь прогон продукт живьём
    поднимали только помощники, а ему давали ссылки на служебные страницы.

  python3 report.py <каталог .superstack> [--json]

  код 0 — отчёт собран, 2 — прогона нет, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# memory_file.py лежит в той же папке инструментов, но при импорте из теста
# (spec_from_file_location) каталог скрипта в sys.path не попадает — тогда
# «import memory_file» упал бы там, где при обычном запуске работает.
# Импорт НЕ оборачивается в `except`: молча выключенное чтение памяти сказало
# бы «команды запуска не записаны» над файлом, в котором они записаны.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lang as языки  # noqa: E402
import memory_file  # noqa: E402

PROVEN, CLAIMED = "proven", "claimed"

#: Раздел памяти проекта, в который фаза 7 кладёт проверенные команды.
РАЗДЕЛ_КОМАНД = "Команды"

#: По этим словам команда узнаётся как «этим оно поднимается». Список короткий
#: нарочно: названная не та строка хуже честного «не видно, какая запускает» —
#: человек выполнит её, ничего не увидит и решит, что не работает продукт.
ПУСК = ("dev", "start", "serve", "runserver", "preview", " up")

#: Признаки того, что открывать это будут в браузере.
ВЕБ = ("npm", "pnpm", "yarn", "bun", "vite", "next", "nuxt", "serve", "http",
       "runserver", "uvicorn", "gunicorn", "flask", "rails", "django",
       "streamlit", "hugo", "astro", "localhost")

#: Пустое место в таблице команд. Скелет памяти проекта кладёт туда строку
#: «— | заполняется, когда команда впервые отработала», и живой прогон принял
#: этот прочерк за команду: человеку показали тире и стрелку «эта строка
#: поднимает построенное», указывающую на тире. Подтверждение без содержимого —
#: тот же класс поломки, что «успешно» раньше проверки.
ПУСТО = set(" \t-–—|.·*_")

#: Порт берётся ТОЛЬКО оттуда, где он написан буквами. Подставить общеизвестное
#: умолчание («у npm ведь 3000») значит выдать догадку за адрес — и человек
#: упрётся в пустую страницу, считая, что смотрит на нашу работу.
_ПОРТ = re.compile(
    r"(?i)(?:--port[ =]|\bport=|localhost:|0\.0\.0\.0:|127\.0\.0\.1:)(\d{2,5})\b")


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return default if default is not None else {}


def gather(run: Path) -> dict:
    state = _load(run / "state.json")
    manifest = _load(run / "manifest.json")
    tasks = [t for w in (state.get("waves") or {}).values() for t in w]
    reqs = manifest.get("requirements") or []
    blind = manifest.get("blind") or {}

    findings = []
    for f in sorted(run.glob("review-*.json")):
        findings.extend(_load(f).get("findings") or [])

    return {
        "проверено": [t for t in tasks if t.get("status") == PROVEN],
        "со_слов": [t for t in tasks if t.get("status") == CLAIMED],
        "не_закрыто": [t for t in tasks
                       if t.get("status") not in (PROVEN, CLAIMED)],
        "требования": reqs,
        "приёмка": blind,
        "находки": findings,
        "долг": state.get("debt") or {},
        # Язык человека читается ОТТУДА ЖЕ, откуда этап: одна запись прогона,
        # сделанная в фазе 0. Своя догадка по тексту требований разошлась бы
        # с панелью, и человек получил бы экран на одном языке и отчёт на
        # другом.
        "язык": языки.from_state(state),
        # Папка прогона лежит В КОРНЕ проекта, память проекта — рядом с ней.
        "запуск": launch(run.resolve().parent, языки.from_state(state)),
        "этап": (state.get("phase") or {}).get("name", ""),
    }


def _пусто(s: str) -> bool:
    """Строка, в которой нет ни одного знака команды: прочерк, точки, черта."""
    return not s or set(s) <= ПУСТО


def _команды(body: str) -> list:
    """Команды из тела раздела: сначала то, что в обратных кавычках."""
    got = [c.strip() for c in re.findall(r"`([^`\n]+)`", body)]
    if got:
        return [c for c in got if not _пусто(c)]
    out = []
    for ln in body.splitlines():
        s = ln.strip().strip("|").strip()
        if _пусто(s) or s.lower().startswith("команда"):
            continue
        out.append(s.split("|")[0].strip())
    return [c for c in out if not _пусто(c)]


def launch(root: Path, язык: str = языки.RU) -> dict:
    """Чем человек откроет построенное и по какому адресу.

    Раздел команд читается ТЕМ ЖЕ инструментом, который его туда пишет.
    Свой разбор того же файла разошёлся бы с ним молча — и отчёт сказал бы
    «команды запуска не записаны» над файлом, где они лежат.
    """
    try:
        body = memory_file.get_section(root, РАЗДЕЛ_КОМАНД)["body"]
    except OSError as e:
        # Узко и вслух: нечитаемая память — это «не смог посмотреть», а не
        # «команд нет», и последний шаг прогона не имеет права падать из-за
        # чужого файла, до которого не дотянулись.
        return {"command": None, "address": None,
                "missing": языки.t("launch_memory_unreadable", язык,
                                   error=e.strerror or e),
                "next": языки.t("launch_next_name_it", язык)}
    команды = _команды(body)
    выбор = next((c for c in команды if any(h in c.lower() for h in ПУСК)), None)
    if выбор is None and len(команды) == 1:
        выбор = команды[0]
    if выбор is None:
        # Раздела нет, он пуст или в нём один прочерк — это ОДИН случай:
        # открывать нечем. Разделять их значило бы объяснять человеку
        # устройство нашего файла вместо ответа на его вопрос.
        нет = not команды
        return {"command": None, "address": None,
                "missing": языки.t("launch_missing_none" if нет
                                   else "launch_missing_unclear", язык),
                "next": языки.t("launch_next_ask" if нет
                                else "launch_next_ask_which", язык)}
    порт = _ПОРТ.search(выбор)
    if порт:
        адрес = f"http://localhost:{порт.group(1)}"
    elif any(w in выбор.lower() for w in ВЕБ):
        адрес = языки.t("launch_address_self", язык)
    else:
        адрес = None
    return {"command": выбор, "address": адрес, "missing": None, "next": None}


def как_открыть(пуск: dict, язык: str = языки.RU) -> list:
    """Последние строки отчёта: вход в продукт, а не его описание.

    Единственное место, где человеку дают команду. Она уместна ровно потому,
    что это команда ЕГО продукта, — и рядом сказано словами, что она делает:
    строка без объяснения читается как заклинание и не выполняется.
    """
    out = [языки.t("report_how_head", язык)]
    if not пуск.get("command"):
        # Ни стрелки, ни прочерка: строка «эта строка поднимает построенное»
        # над пустым местом — это подтверждение без содержимого, и человек
        # уходит выполнять то, чего ему не дали.
        строка = пуск.get("missing") or языки.t("report_command_unnamed", язык)
        след = пуск.get("next")
        return out + [f"  {строка} — {след}" if след else f"  {строка}"]
    out.append(f"  {пуск['command']}")
    out.append("  " + языки.t("report_arrow", язык))
    if пуск.get("address"):
        out.append("  " + языки.t("report_address", язык,
                                  address=пуск["address"]))
    return out


def human(d: dict) -> str:
    """Отчёт словами, которые понимает человек без опыта в коде."""
    out = []
    # Язык прогона, а не язык этого файла: надписи собираются по ключам, и
    # ключ, которого нет, роняет отчёт здесь — а не показывает человеку
    # пустой заголовок, который никто не заметит.
    я = d.get("язык") or языки.RU
    total = len(d["проверено"]) + len(d["со_слов"]) + len(d["не_закрыто"])
    out.append(языки.t("report_done_head", я))
    if d["проверено"]:
        for t in d["проверено"]:
            out.append(f"  ✓ {t['name']} — "
                       + языки.t("report_proven_by_machine", я))
    else:
        out.append("  " + языки.t("report_nothing_proven", я))
    if d["со_слов"]:
        out.append("")
        out.append(языки.t("report_claimed_head", я))
        for t in d["со_слов"]:
            out.append(f"  · {t['name']}")

    ask = []
    for r in d["требования"]:
        if r.get("status") in ("placeholder", "open"):
            ask.append(r.get("basis") or r.get("quote") or r.get("id"))
    for kind, items in (d["долг"] or {}).items():
        ask.extend(items or [])
    for q in (d["приёмка"].get("undisclosed") or []):
        ask.append(q)
    пуск = d.get("запуск") or {}
    if пуск.get("missing"):
        ask.append(f"{пуск['missing']} — " + языки.t("report_no_way_in", я))
    if ask:
        out.append("")
        out.append(языки.t("report_ask_head", я))
        for a in ask:
            out.append(f"  → {a}")

    checked = d["приёмка"].get("checked") or []
    if checked:
        out.append("")
        out.append(языки.t("report_blind_head", я))
        for c in checked:
            out.append(f"  {c.get('verdict', '?')}: {c.get('where', '')}")

    # Закрытые находки в отчёт не идут: список, где половина строк уже
    # неверна, теряет доверие целиком — проверено на первом же живом отчёте,
    # где из одиннадцати строк открытыми были шесть.
    blocking = [f for f in d["находки"]
                if f.get("blocking") and not f.get("closed")]
    fixed = [f for f in d["находки"] if f.get("closed")]
    if blocking:
        out.append("")
        out.append(языки.t("report_open_head", я))
        for f in blocking:
            out.append(f"  ! {f.get('what', '')}")

    if fixed:
        out.append("")
        out.append(языки.t("report_fixed_head", я))
        for f in fixed:
            out.append(f"  ✓ {f.get('what', '')[:90]}")
            out.append("      " + языки.t("report_fixed_with", я)
                       + f" {f.get('closed', '')[:100]}")

    out.append("")
    out.append(языки.t("report_totals", я, total=total,
                       proven=len(d["проверено"])))

    out.append("")
    out.extend(как_открыть(пуск, я))
    return "\n".join(out)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    plain = [a for a in argv if not a.startswith("--")]
    if not plain:
        print("вызов: report.py <каталог .superstack> [--json]", file=sys.stderr)
        return 3
    run = Path(plain[0])
    if not (run / "state.json").is_file():
        print(f"НЕ УДАЛОСЬ: в {run} нет состояния прогона", file=sys.stderr)
        return 2
    d = gather(run)
    if "--json" in argv:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(human(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
