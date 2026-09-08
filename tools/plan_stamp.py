#!/usr/bin/env python3
"""SUPERSTACK — отметка о сверке карты механизмов с планом.

Зачем это вообще нужно.

`data/plan-coverage.json` написала та же модель, которая по нему строила. Ворота
«план» проверяют соответствие КАРТЕ, а не соответствие карты ПЛАНУ — неполнота
карты невидима для них всех. Это уже случилось: «34 из 34» горело зелёным, пока
независимая сверка не нашла 14 групп пропусков, и карта выросла до 137.

Сверку провели один раз. Механизмом она не стала: ни один инструмент не смотрел
на файл плана, и следующая его правка разошлась бы с картой ровно так же молча.
Здесь — самая дешёвая часть, которую можно посчитать кодом: **отпечаток плана,
с которым карту сверяли**. Совпал — сверка относится к нынешнему плану; не
совпал — план правился, и полнота карты снова НЕИЗВЕСТНА.

Чем это НЕ является, и это важнее того, чем является. Отметка не доказывает,
что карта полна: её ставит тот, кто сверял, и врать ей можно. Она доказывает
ровно одно — что после сверки план не менялся. Полноту по-прежнему устанавливает
чтение плана другой моделью; механизм лишь не даёт расхождению пройти незаметно.

  python3 plan_stamp.py --by <кто сверял> [--note <чем закончилось>]
  python3 plan_stamp.py --show
  python3 plan_stamp.py --snapshot                      пересобрать снимок плана
  python3 plan_stamp.py --by <кто> --map <своя карта>   (для тестов)

  код 0 — отметка поставлена, 2 — плана нет, 3 — ошибка вызова
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAP = REPO / "data" / "plan-coverage.json"

#: Имя переносимого снимка плана. Лежит РЯДОМ С КАРТОЙ, а не по своему пути:
#: сверка относится к карте, и разнесённые файлы разъезжаются при первом же
#: переносе — а разъехавшись, молчат.
SNAPSHOT_NAME = "plan-snapshot.json"

#: Раздел плана — от заголовка второго уровня до следующего. Разбивка нужна
#: не ради красоты отчёта: без неё расхождение звучит как «план правился», и
#: перечитывать приходится тысячу шестьсот строк вместо двух разделов.
_SECTION = re.compile(r"(?m)^##\s")

#: Путь к плану переопределяется переменной среды — иначе ни один тест не
#: сможет проверить эту сверку, не читая настоящий план на машине автора.
PLAN_ENV = "SUPERSTACK_PLAN"
PLAN_DEFAULT = Path.home() / ".claude" / "plans" / "typed-waddling-hamster.md"


def plan_path() -> Path:
    v = os.environ.get(PLAN_ENV)
    return Path(v).expanduser() if v else PLAN_DEFAULT


def digest(p: Path) -> str:
    """Отпечаток плана. Любая правка — новый отпечаток, и это намеренно строго:
    решать, «существенная» ли правка, значит снова судить прозой."""
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def body_digest(тело: dict) -> str:
    """Отпечаток самого снимка — по канонической записи БЕЗ поля `self`.

    Без него снимок доказывает ровно то, что в нём написано: любой может
    переписать отпечаток плана строкой в файле, и сверка в клоне сведётся к
    «в файле сказано, что сходится».
    """
    голое = {k: v for k, v in тело.items() if k != "self"}
    return "sha256:" + hashlib.sha256(json.dumps(
        голое, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def build_snapshot(p: Path) -> dict:
    """Переносимый ОБЕЗЛИЧЕННЫЙ снимок плана: отпечатки и числа, без текста.

    Зачем он рядом с картой. Путь к плану по умолчанию вёл в домашний каталог
    автора. Независимый клон этого файла не содержит — и ворота честно
    отвечали «не смог сверить», а итог сборки читал такой ответ как
    предупреждение и заканчивался зелёным. Ограничение было названо и всё
    равно уехало в «все ворота прошли».

    Чего в снимке НЕТ и почему это условие, а не оговорка: ни строки плана, ни
    заголовков, ни путей. Файл уезжает в публичное дерево вместе с картой, и
    единственный безопасный вид содержимого здесь — необратимый отпечаток.
    """
    сырой = p.read_bytes()
    текст = сырой.decode("utf-8", errors="replace")
    разделы = [{"digest": "sha256:" + hashlib.sha256(
        кусок.encode("utf-8")).hexdigest(), "lines": кусок.count("\n")}
        for кусок in _SECTION.split(текст)]
    тело = {
        "schema": "superstack.plan-snapshot.v1",
        "why": "Переносимый отпечаток плана, с которым сверялась карта "
               "механизмов. Нужен, чтобы ворота «сверка» работали в любом "
               "клоне, а не только там, где лежит личный файл автора. "
               "Содержимого плана здесь нет — только отпечатки и числа.",
        "plan_digest": digest(p),
        "bytes": len(сырой),
        "lines": текст.count("\n") + (0 if текст.endswith("\n") else 1),
        "sections": разделы,
    }
    return {**тело, "self": body_digest(тело)}


def write_snapshot(p: Path, путь: Path = None) -> dict:
    """Положить снимок рядом с картой. Возвращает то, что записано."""
    s = build_snapshot(p)
    (Path(путь) if путь else REPO / "data" / SNAPSHOT_NAME).write_text(
        json.dumps(s, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return s


def read_snapshot(путь: Path = None) -> dict:
    """Прочитать снимок и сказать, цел ли он. Причина называется, а не глотается."""
    p = Path(путь) if путь else REPO / "data" / SNAPSHOT_NAME
    try:
        s = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "why": f"снимок плана не прочитан: {e}"}
    if not isinstance(s, dict) or not s.get("plan_digest"):
        return {"ok": False, "why": "в снимке плана нет отпечатка плана"}
    if s.get("self") != body_digest(s):
        return {"ok": False, "why": "снимок плана правился помимо своего отпечатка"}
    return {"ok": True, "why": "", "snapshot": s}


def main() -> int:
    argv = sys.argv[1:]

    def opt(name: str) -> "str | None":
        return argv[argv.index(name) + 1] if name in argv and \
            argv.index(name) + 1 < len(argv) else None

    путь_карты = Path(opt("--map")) if opt("--map") else MAP
    try:
        карта = json.loads(путь_карты.read_text("utf-8"))
    except (OSError, ValueError) as e:
        print(f"НЕ УДАЛОСЬ: карта не прочитана: {e}", file=sys.stderr)
        return 3

    if "--show" in argv:
        print(json.dumps(карта.get("reconciled") or {}, ensure_ascii=False, indent=1))
        return 0

    if "--snapshot" in argv:
        # Пересборка снимка без переотметки: план на месте, сверка та же,
        # а снимка рядом с картой ещё нет.
        p = plan_path()
        if not p.is_file():
            print(f"НЕ УДАЛОСЬ: плана нет по пути {p} — снимать нечего",
                  file=sys.stderr)
            return 2
        s = write_snapshot(p, путь_карты.parent / SNAPSHOT_NAME)
        print(f"СНИМОК ПЛАНА СОБРАН: разделов {len(s['sections'])}, "
              f"строк {s['lines']}", file=sys.stderr)
        print(json.dumps({k: v for k, v in s.items() if k != "sections"},
                         ensure_ascii=False, indent=1))
        return 0

    кто = opt("--by")
    if not кто:
        # Отметка без имени — это «кто-то когда-то сверял»: ровно то, что уже
        # один раз прошло за проверку и оказалось ничем.
        print("НЕ УДАЛОСЬ: нужно --by <кто сверял>. Отметка без имени "
              "сверяющего ничего не утверждает", file=sys.stderr)
        return 3

    p = plan_path()
    if not p.is_file():
        print(f"НЕ УДАЛОСЬ: плана нет по пути {p} — сверять не с чем",
              file=sys.stderr)
        return 2

    было = (карта.get("reconciled") or {}).get("digest")
    карта["reconciled"] = {
        "digest": digest(p),
        "date": dt.date.today().isoformat(),
        "by": кто,
        "mechanisms": len(карта.get("mechanisms") or []),
        # Путь пишется через `~`: абсолютный тащит в файл домашний
        # каталог человека, а файл уезжает в публичный репозиторий.
        "plan": str(p).replace(str(Path.home()), "~"),
        "note": opt("--note") or "",
    }
    путь_карты.write_text(json.dumps(карта, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    # Снимок обновляется ТЕМ ЖЕ вызовом, что и отметка. Отдельная команда,
    # которую надо не забыть, забывается на первой же правке плана — и снимок
    # начинает подтверждать сверку, которой больше нет.
    write_snapshot(p, путь_карты.parent / SNAPSHOT_NAME)
    print(f"СВЕРКА ОТМЕЧЕНА: {кто}, механизмов {карта['reconciled']['mechanisms']}",
          file=sys.stderr)
    if было and было != карта["reconciled"]["digest"]:
        print("  план с прошлой сверки менялся — отпечаток обновлён",
              file=sys.stderr)
    print(json.dumps(карта["reconciled"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
