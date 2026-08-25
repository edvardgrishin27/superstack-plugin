#!/usr/bin/env python3
"""SUPERSTACK — /fix. Называет ЗВЕНО, а не человека.

Зачем это отдельный инструмент, а не абзац в инструкции.

«Что-то сломалось» — это симптом, а не диагноз. Модель, отвечающая на такую
жалобу без прибора, либо гадает причину по памяти разговора, либо вываливает
стектрейс, который целевой человек читать не умеет. Оба исхода одинаково
бесполезны: они не называют, ЧТО именно проверить дальше.

Здесь состояние — это ЦЕПОЧКА звеньев, проверяемых ПО ПОРЯДКУ. Порядок — не
случаен: каждое следующее звено имеет смысл проверять только если предыдущее
уже в порядке (нет смысла разбирать хуки, если python3 не найден вовсе).
Проверка останавливается на ПЕРВОМ разорванном звене — а не собирает все
поломки разом, потому что чинить есть смысл по одной, и список из пяти
причин человек всё равно read'ит только до первой.

Словарь вывода — «ещё не подключено», не «ошибка» и не «баг»: чинить не
человека, а звено.

  python3 fix.py [--json]

  код 0 — все звенья в порядке, 1 — звено разорвано (это диагноз, а не
          отказ вызова)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# Соседние инструменты лежат в этом же каталоге, но при импорте из теста
# (spec_from_file_location) каталог скрипта в sys.path не попадает — тогда
# «import verify» упал бы там, где при обычном запуске работает.
sys.path.insert(0, str(Path(__file__).resolve().parent))


HOME = Path.home()
CLAUDE = HOME / ".claude"
STATE = CLAUDE / "superstack"
ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# звенья — каждое возвращает (ok, что_пробовал, подробность)
# --------------------------------------------------------------------------
def _link_python3() -> tuple:
    ok = shutil.which("python3") is not None
    detail = "" if ok else "python3 не найден в PATH — без него не работает ни один инструмент системы"
    return ok, "искал python3 в PATH", detail


def _link_tools() -> tuple:
    required = ("verify.py", "apply.py", "spec_lint.py", "skill_test.py")
    missing = [t for t in required if not (ROOT / "tools" / t).is_file()]
    detail = ("не хватает файлов в tools/: " + ", ".join(missing)) if missing else ""
    return not missing, f"проверил {len(required)} файлов в tools/", detail


def _link_hooks() -> tuple:
    hooks_json = ROOT / "hooks" / "hooks.json"
    if not hooks_json.is_file():
        return False, "искал hooks/hooks.json", "файла с хуками нет на месте"
    try:
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, "прочитал hooks/hooks.json", f"файл не разбирается: {e}"

    missing = []
    for entries in data.get("hooks", {}).values():
        for entry in entries:
            for h in entry.get("hooks", []):
                for token in h.get("command", "").split():
                    if "${CLAUDE_PLUGIN_ROOT}" not in token:
                        continue
                    rel = token.replace("${CLAUDE_PLUGIN_ROOT}", "").strip('"\'')
                    target = ROOT / rel.lstrip("/")
                    if not target.is_file():
                        missing.append(str(target))
    detail = ("хук ссылается на файл, которого нет: " + ", ".join(missing)) if missing else ""
    return not missing, "прочитал hooks/hooks.json и проверил файлы внутри", detail


def _link_pause() -> tuple:
    flag = STATE / "PAUSE"
    if not flag.exists():
        return True, "проверил флаг паузы", ""
    try:
        since = flag.read_text(encoding="utf-8").strip() or "?"
    except OSError:
        since = "?"
    return False, "проверил флаг паузы", f"система стоит на паузе с {since}"


def _link_writable() -> tuple:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        probe = STATE / ".fix-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return False, "попробовал записать пробный файл в каталог состояния", str(e)
    return True, "попробовал записать пробный файл в каталог состояния", ""


#: (id, человеческое имя звена, проверка, команда для починки).
#: Команда — статичный текст: значение STATE в момент импорта устареет для
#: тестов, которые подменяют STATE после загрузки модуля, поэтому в тексте
#: команды нет интерполяции переменных состояния.
LINKS = (
    ("python3", "python3", _link_python3,
     "поставь Python 3 и повтори"),
    ("tools", "файлы инструментов", _link_tools,
     "переустанови плагин — часть его файлов пропала"),
    ("hooks", "подключение хуков", _link_hooks,
     "переустанови плагин — хук ссылается на файл, которого нет"),
    ("pause", "пауза", _link_pause,
     "sh tools/pause.sh off"),
    ("writable", "права на запись", _link_writable,
     "проверь права на каталог ~/.claude/superstack"),
)


# --------------------------------------------------------------------------
# цепочка
# --------------------------------------------------------------------------
def diagnose() -> dict:
    """Первое разорванное звено — и ровно оно. Дальше цепочка не идёт."""
    total = len(LINKS)
    checked = []
    for i, (link_id, label, fn, fix_cmd) in enumerate(LINKS, start=1):
        ok, tried, detail = fn()
        checked.append({"id": link_id, "label": label, "ok": ok,
                        "tried": tried, "detail": detail})
        if not ok:
            return {
                "status": "broken", "broken_index": i, "total": total,
                "link": label, "tried": tried, "detail": detail,
                "ok_count": i - 1, "next": fix_cmd, "checked": checked,
            }
    return {
        "status": "ok", "total": total, "ok_count": total, "checked": checked,
        "next": ("все звенья в порядке — расскажи двумя словами, что не "
                 "получилось, и дальше разберёмся по шагам"),
    }


HEAD = {"broken": "ЗВЕНО ЕЩЁ НЕ ПОДКЛЮЧЕНО", "ok": "ВСЕ ЗВЕНЬЯ В ПОРЯДКЕ"}


def human(v: dict) -> str:
    if v["status"] == "broken":
        lines = [f"{HEAD['broken']}: звено {v['broken_index']} из {v['total']} "
                f"({v['link']}) — ещё не подключено."]
        lines.append(f"  пробовал: {v['tried']}")
        if v["detail"]:
            lines.append(f"  подробность: {v['detail']}")
        lines.append(f"  в порядке: {v['ok_count']} из {v['total']}")
        lines.append(f"  дальше: {v['next']}")
    else:
        lines = [f"{HEAD['ok']}: {v['ok_count']} из {v['total']}"]
        lines.append(f"  дальше: {v['next']}")
    return "\n".join(lines)


def main() -> int:
    args = sys.argv[1:]
    quiet = "--json" in args
    v = diagnose()
    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 0 if v["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
