#!/usr/bin/env python3
"""SUPERSTACK — можно ли ЭТО показывать человеку.

Зачем понадобился отдельный инструмент.

Страница прошла всё: четыре токена системы совпали значение в значение, теней
нет, отступы кратны четырём, контраст выше нормы, восемьдесят семь тестов
зелёные, три линтера вернули ноль. Человек открыл её и сказал: «выглядит убого,
словно вообще не сделали».

Обе стороны правы, и в этом суть. Проверки считали СООТВЕТСТВИЕ ПРАВИЛАМ, а
человек смотрел на РЕЗУЛЬТАТ. Между ними помещается целый класс провалов:
страница, где каждый элемент по системе, а смотреть не на что, потому что в ней
нет ни одного настоящего факта — шесть подписей «[ВПИШИ: ...]» и три пустых
состояния вместо содержимого.

Заглушки при этом правильны: выдумывать за человека цену и адрес запрещено.
Ошибка не в них, а в том, что каркас показали как результат.

Что здесь считается:

  · СКОЛЬКО ЗАГЛУШЕК видно на странице — не в коде, а глазами;
  · СКОЛЬКО ПУСТЫХ СОСТОЯНИЙ показано вместо содержимого;
  · ЕСТЬ ЛИ хоть одно изображение и хоть один акцент.

Вердикт грубый намеренно: «это каркас» или «это страница». Тонкие суждения о
красоте машине не по силам, а вот отличить наполненную страницу от решётки
заглушек она может — и именно этого не хватало.

  python3 page_check.py <корень проекта> [--json]

  код 0 — можно показывать, 1 — это каркас, показывать как результат нельзя,
  2 — нечего смотреть (страницы нет), 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: Сколько видимых заглушек превращает страницу в каркас. Одна-две — нормальная
#: незаполненность; от трёх человек перестаёт видеть продукт и видит форму
#: ввода данных, которую ему предлагают принять за сайт.
PLACEHOLDER_LIMIT = 2

#: Метка заглушки. Одна на всю систему: договорённость, а не догадка по тексту.
PLACEHOLDER = "[ВПИШИ:"

#: Где искать то, что окажется на экране.
LOOK_IN = ("src/**/*.ts", "src/**/*.tsx", "src/**/*.js", "index.html",
           "content/*.json")

#: Признаки пустого состояния в разметке и текстах — по классам системы и по
#: словам, которыми такие блоки подписывают.
EMPTY_MARKS = ("empty-state", "EmptyState", "пока пусто", "скоро появятся",
               "объявлю скоро", "мест нет")


def _files(root: Path) -> list:
    out = []
    for pat in LOOK_IN:
        out.extend(p for p in root.glob(pat) if p.is_file())
    return out


def scan(root: Path) -> dict:
    """Что окажется перед глазами человека."""
    files = _files(root)
    if not files:
        return {"status": "unknown", "detail": "страницы нет — смотреть нечего"}

    placeholders, empties = [], []
    images = accents = 0
    for f in files:
        try:
            t = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        # Тестовые файлы отражают страницу, а не являются ею: их вхождения
        # удвоили бы счёт и превратили нормальную страницу в «каркас».
        if ".test." in f.name:
            continue
        for m in re.finditer(re.escape(PLACEHOLDER) + r"([^\]]*)", t):
            what = m.group(1).strip()
            # «[ВПИШИ: ...]» из примера в комментарии — это не незаполненное
            # место, а образец записи. Считать его значило бы просить человека
            # заполнить многоточие.
            if what and what != "...":
                placeholders.append(what)
        for mark in EMPTY_MARKS:
            empties.extend([mark] * t.count(mark))
        images += len(re.findall(r"<img|\.webp|\.avif|\.jpg|\.png|<svg", t))
        accents += t.count("--accent") + t.count("button--primary")

    seen = sorted(set(placeholders))
    caркас = len(seen) > PLACEHOLDER_LIMIT
    return {
        "status": "fail" if caркас else "pass",
        "placeholders": seen,
        "placeholder_count": len(seen),
        "empty_blocks": len(set(empties)),
        "images": images,
        "accents": accents,
        "detail": (
            f"на странице {len(seen)} незаполненных мест и {len(set(empties))} "
            "пустых блоков — человек увидит не сайт, а форму для ввода своих "
            "данных. Показывать это как результат нельзя: сначала спроси факты"
            if caркас else
            f"незаполненных мест {len(seen)} при пороге {PLACEHOLDER_LIMIT} — "
            "страницу можно показывать"),
    }


def what_to_ask(v: dict) -> list:
    """Что спросить у человека, чтобы страница ожила. По-человечески."""
    words = {
        "название студии": "как называется студия",
        "адрес студии": "куда приходить — адрес",
        "телефон": "телефон для связи",
        "ссылка на инстаграм": "ссылка на инстаграм",
        "цена мастер-класса": "сколько стоит занятие",
        "длительность мастер-класса": "сколько оно длится",
    }
    return [words.get(p, p) for p in v.get("placeholders", [])]


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
        print("вызов: page_check.py <корень проекта> [--json]", file=sys.stderr)
        return 3
    root = Path(plain[0])
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    v = scan(root)
    v["ask"] = what_to_ask(v)
    if "--json" in argv:
        print(json.dumps(v, ensure_ascii=False, indent=1))
    else:
        head = {"pass": "СТРАНИЦУ МОЖНО ПОКАЗЫВАТЬ",
                "fail": "ЭТО ЕЩЁ КАРКАС",
                "unknown": "СМОТРЕТЬ НЕЧЕГО"}[v["status"]]
        print(f"{head}: {v['detail']}", file=sys.stderr)
        for a in v["ask"]:
            print(f"  спросить: {a}", file=sys.stderr)
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
