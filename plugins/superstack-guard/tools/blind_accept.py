#!/usr/bin/env python3
"""SUPERSTACK — слепая приёмка: сделали ли ТО, о чём просили.

Чего не делает ни один другой гейт. Верификация отвечает «работает ли»,
мутации — «держатся ли тесты», планка — «построено ли». Ни один не отвечает
на вопрос «то ли это». Безупречно сделанное НЕ ТО проходит все шесть ворот.

Почему судью надо ослепить. Спека, план и тикеты — это уже ПЕРЕСКАЗ просьбы,
сделанный тем, кто её понял. Если расхождение возникло на этом шаге, оно
одинаково живёт и в спеке, и в коде: судья, читающий спеку, сверяет пересказ
с пересказом и всегда находит совпадение. Единственный способ поймать дрейф —
дать судье ИСХОДНУЮ просьбу и результат, и больше ничего.

Здесь слепота — свойство ПАКЕТА, а не обещание в инструкции. Скрипт собирает
ровно две вещи и вырезает из изменений сами артефакты пересказа. Инструкция
«не подглядывай в спеку» механизмом не является: подглядывание нечем проверить,
а невключённое нечем прочитать.

Три правила, без которых приёмка становится театром:

  1. НЕТ ИСХОДНОЙ ПРОСЬБЫ — НЕТ ПРИЁМКИ. Не «прошло» и не «провалено», а
     «не смог проверить», код 2. Судить соответствие нечему, если не сохранено,
     о чём просили.
  2. ВЫРЕЗАННОЕ НАЗЫВАЕТСЯ. Пакет перечисляет, какие файлы не показаны судье
     и почему. Молча урезанный вход — это чужой вердикт о чужой работе.
  3. СКРИПТ ГАРАНТИРУЕТ СЛЕПОТУ, НЕ ПРАВОТУ. Вердикт выносит модель, и он
     остаётся мнением. Механизм отвечает лишь за то, что мнение сформировано
     не по пересказу.

  python3 blind_accept.py pack <просьба.txt> <изменения.diff>   -> пакет судье
  python3 blind_accept.py pack --json ...                       -> только JSON

  код 0 — пакет собран, 2 — нечем судить, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

MAX_DIFF = 200_000        # знаков изменений: длиннее судья не читает, а листает

#: Артефакты ПЕРЕСКАЗА. Их судья не видит никогда — в них живёт то же
#: расхождение, что и в коде, и совпадение с ними ничего не доказывает.
RETELLING = (
    re.compile(r"(?:^|/)\.claude/specs?/", re.I),
    re.compile(r"(?:^|/)(?:SPEC|ТЗ|PLAN|ПЛАН|ROADMAP)[\w.-]*\.md$", re.I),
    re.compile(r"(?:^|/)\.planning/", re.I),
    re.compile(r"(?:^|/)(?:tasks?|tickets?|issues?)/[\w.-]+\.(?:md|json|ya?ml)$", re.I),
    re.compile(r"(?:^|/)PLAN\.md$", re.I),
    re.compile(r"(?:^|/)REVIEW\.md$", re.I),
)

_FILE_LINE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.M)


def is_retelling(path: str) -> bool:
    """Является ли файл пересказом просьбы, а не результатом работы."""
    return any(rx.search(path) for rx in RETELLING)


def split_diff(diff: str) -> tuple:
    """Разложить diff на куски по файлам: (путь, текст куска).

    Разбор по заголовкам `diff --git`. Текст до первого заголовка — не файл,
    а шапка, и она отбрасывается вместе с остальным неопознанным: показать
    судье кусок неизвестной принадлежности значит нарушить слепоту вслепую.
    """
    marks = list(_FILE_LINE.finditer(diff))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(diff)
        out.append((m.group(2), diff[m.start():end]))
    return out, bool(marks)


def build_packet(request: str, diff: str) -> dict:
    """Собрать то, и только то, что увидит судья."""
    chunks, parsed = split_diff(diff)
    shown, hidden = [], []
    for path, text in chunks:
        (hidden if is_retelling(path) else shown).append((path, text))

    body = "".join(t for _, t in shown)
    truncated = len(body) > MAX_DIFF
    if truncated:
        body = body[:MAX_DIFF]

    return {
        "gate": "blind-acceptance",
        "request": request.strip(),
        "changes": body,
        "shown_files": [p for p, _ in shown],
        # Вырезанное НАЗЫВАЕТСЯ: молча урезанный вход превращает вердикт
        # в суждение о работе, которой судья не видел.
        "hidden_files": [{"path": p, "why": "пересказ просьбы, а не результат"}
                         for p, _ in hidden],
        "diff_parsed": parsed,
        "truncated": truncated,
    }


def verdict(packet: dict) -> dict:
    """Можно ли вообще судить — и если нет, то почему именно."""
    if not packet["request"]:
        return {**packet, "status": "unknown",
                "next": "исходная просьба не сохранена — судить соответствие нечему; "
                        "сохраняй первое сообщение проекта дословно"}
    if not packet["diff_parsed"]:
        return {**packet, "status": "unknown",
                "next": "изменения не разобраны как diff — судья увидел бы текст "
                        "неизвестной принадлежности"}
    if not packet["shown_files"]:
        why = ("все изменения — пересказ просьбы (спеки, планы, тикеты): "
               "результата работы в них нет"
               if packet["hidden_files"] else "изменений нет вовсе")
        return {**packet, "status": "unknown", "next": why}
    return {**packet, "status": "ready",
            "next": "отдать пакет агенту blind-acceptance: он видит просьбу и "
                    "изменения, и НИЧЕГО больше"}


EXIT = {"ready": 0, "unknown": 2}


def human(v: dict) -> str:
    lines = ["ПАКЕТ ГОТОВ" if v["status"] == "ready" else "СУДИТЬ НЕЧЕМ"]
    lines.append(f"  просьба: {len(v['request'])} знаков")
    lines.append(f"  показано файлов: {len(v['shown_files'])}")
    for h in v["hidden_files"][:8]:
        lines.append(f"  скрыто: {h['path']} — {h['why']}")
    if v["truncated"]:
        lines.append(f"  изменения обрезаны до {MAX_DIFF} знаков")
    lines.append(f"  дальше: {v['next']}")
    return "\n".join(lines)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = [a for a in sys.argv[1:] if a != "--json"]
    quiet = "--json" in sys.argv[1:]
    if len(argv) != 3 or argv[0] != "pack":
        print("вызов: blind_accept.py pack [--json] <просьба.txt> <изменения.diff>",
              file=sys.stderr)
        return 3
    req_p, diff_p = Path(argv[1]), Path(argv[2])
    for p in (req_p, diff_p):
        if not p.is_file():
            print(f"НЕ УДАЛОСЬ: нет файла — {p}", file=sys.stderr)
            return 3
    v = verdict(build_packet(req_p.read_text("utf-8", errors="replace"),
                             diff_p.read_text("utf-8", errors="replace")))
    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return EXIT.get(v["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
