#!/usr/bin/env python3
"""SUPERSTACK — человеческий русский для всего, что видит человек.

Зачем это код, а не установка «пиши понятно».

Жаргон невозможно вычитать самому: чтобы заметить непонятное слово, нужно
перестать его понимать. Написав «таск 06 — страница по принесённой системе; я
жду возврата», я перечитал строку и не увидел в ней ничего плохого — каждое
слово честное. Человек, который не пишет код, не понял НИ ОДНОГО.

Правило «пиши по-русски» без принуждения держится ровно до следующей строки,
написанной в спешке. Поэтому список слов лежит данными, замена ищется машиной,
а находка — код возврата.

Что здесь НЕ делается. Автозамена в тексте не выполняется: русский язык
склоняет, и «таском» → «задачаом» хуже оригинала. Инструмент называет слово и
предлагает замену; переписывает человек или модель.

  python3 plain_ru.py check <файл> [файл...]   -> найти жаргон
  python3 plain_ru.py check -                  -> прочитать из потока
  python3 plain_ru.py say <роль|статус|фаза>   -> как это сказать по-русски
  python3 plain_ru.py words                    -> весь словарь

  код 0 — чисто, 1 — найден жаргон, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "plain-ru.json"


def load(path: Path = DATA) -> dict:
    return json.loads(path.read_text("utf-8"))


def _stems(data: dict) -> dict:
    return data["jargon"]["words"]


def find_jargon(text: str, data: dict = None) -> list:
    """Найденный жаргон: [(как написано, основа, чем заменить)].

    Ищется ОСНОВА со свободным хвостом: русский склоняет, и «таском», «таски»,
    «таска» — то же самое слово. Поиск по точной форме пропустил бы почти все
    живые вхождения, оставаясь при этом зелёным.
    """
    data = data or load()
    out = []
    for stem, better in _stems(data).items():
        # У слов на «-й» падеж съедает саму «й»: «деплой» → «деплоя», «деплою»;
        # «репозиторий» → «репозитория». Поиск по полной основе был зелёным и
        # пропускал почти все живые формы — нашлось тестом, не глазами.
        core = stem[:-1] if stem.endswith("й") else stem
        for m in re.finditer(rf"\b{re.escape(core)}\w*", text, re.IGNORECASE):
            out.append((m.group(0), stem, better))
    return out


def role_word(owner: str, data: dict = None) -> str:
    """Как назвать держателя хода человеку.

    Неизвестная роль возвращается как есть: выдумывать перевод опаснее, чем
    показать сырое слово — второе видно и чинится, первое врёт молча.
    """
    data = data or load()
    return data["roles"]["map"].get(owner, owner)


def phase_hint(name: str, data: dict = None) -> str:
    data = data or load()
    return data["phases"]["map"].get(name, "")


def status_word(status: str, data: dict = None) -> str:
    data = data or load()
    return data["statuses"]["map"].get(status, status)


def copy(key: str, data: dict = None) -> str:
    data = data or load()
    return data["copy"]["map"].get(key, "")


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    if not argv:
        print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
        return 3
    cmd, rest = argv[0], argv[1:]
    data = load()

    if cmd == "words":
        print(json.dumps({"жаргон": _stems(data),
                          "роли": data["roles"]["map"],
                          "статусы": data["statuses"]["map"]},
                         ensure_ascii=False, indent=1))
        return 0

    if cmd == "say":
        if not rest:
            print("НЕ УДАЛОСЬ: нечего переводить", file=sys.stderr)
            return 3
        word = rest[0]
        for fn in (role_word, status_word, phase_hint):
            said = fn(word, data)
            if said and said != word:
                print(said)
                return 0
        print(word)
        return 0

    if cmd != "check":
        print(f"НЕ УДАЛОСЬ: неизвестная команда {cmd}", file=sys.stderr)
        return 3
    if not rest:
        print("НЕ УДАЛОСЬ: нечего проверять", file=sys.stderr)
        return 3

    found_any = False
    for name in rest:
        text = sys.stdin.read() if name == "-" else Path(name).read_text("utf-8")
        hits = find_jargon(text, data)
        if not hits:
            continue
        found_any = True
        print(f"{name}: слова, которых человек не поймёт", file=sys.stderr)
        seen = set()
        for written, stem, better in hits:
            if written.lower() in seen:
                continue
            seen.add(written.lower())
            print(f"  «{written}» → {better}", file=sys.stderr)
    return 1 if found_any else 0


if __name__ == "__main__":
    sys.exit(main())
