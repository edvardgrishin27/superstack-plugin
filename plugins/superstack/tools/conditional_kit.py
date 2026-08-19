#!/usr/bin/env python3
"""SUPERSTACK — добавки сверх базы: условие и то, что ставится, лежат данными.

Зачем это не список в голове.

Базовый набор получает каждый без вопросов: он одинаково полезен всем. Всё
остальное полезно НЕ ВСЕМ, и ставить его «на всякий случай» значит платить
контекстом и вниманием за то, чего у человека нет. Обратная ошибка тише и
дороже: нужное не ставится, потому что в момент установки никто не вспомнил
спросить про размер дерева или про вторую машину.

Поэтому добавки заданы парой «условие → что ставится», условие проверяется по
ФАКТАМ машины тем же движком, что и правила, а решение остаётся человеку.

Три правила:

  1. УСЛОВИЕ — ДАННЫЕ, А НЕ КОД. Новая добавка это строка в файле, а не
     правка инструмента: иначе список растёт только там, где кто-то полез в
     исходник.
  2. НЕВЫЧИСЛИМОЕ УСЛОВИЕ ОБЪЯВЛЯЕТСЯ. Нет факта — запись помечается
     пропущенной вместе с причиной. Молча пропущенная добавка неотличима от
     невыполнимой, и обе выглядят как «не нужно».
  3. ЭТО ПРЕДЛОЖЕНИЕ, А НЕ УСТАНОВКА. Инструмент называет, что и почему;
     ставит — установщик, по согласию человека.

  python3 conditional_kit.py <facts.json> [--json]

  код 0 — добавок нет, 1 — есть предложения, 2 — прочитать не удалось,
  3 — ошибка вызова
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
СПИСОК = HERE.parent / "data" / "conditional-kit.json"


def _движок():
    """Вычислитель условий берётся у правил, а не пишется рядом.

    Своя копия грамматики разошлась бы с настоящей молча: условия выглядели бы
    одинаково, а считались по-разному — и разницу нашли бы на чужой машине.
    """
    p = HERE / "adjudicate.py"
    s = importlib.util.spec_from_file_location("ss_adjudicate_kit", p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def каталог(path: Path = СПИСОК) -> tuple:
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"список добавок не прочитан: {e}"
    if not (d.get("additions") or []):
        return None, "список добавок пуст"
    return d, ""


def proposals(добавки: list, значения: dict, evaluate) -> dict:
    """Что предложить, что пропустить и почему. Пропуск НАЗЫВАЕТСЯ."""
    предложить, мимо, пропущено = [], [], []
    for a in добавки:
        try:
            подходит = bool(evaluate(a["when"], значения))
        except Exception as e:                          # noqa: BLE001
            # Молча пропущенная добавка неотличима от невыполнимой, и обе
            # выглядят как «не нужно».
            пропущено.append({"id": a["id"], "why": f"условие не вычислено: {e}"})
            continue
        (предложить if подходит else мимо).append(
            {"id": a["id"], "what": a["what"], "why": a["why"],
             "class": a.get("class", "GATE"), "when": a["when"]})
    return {"propose": предложить, "not_applicable": мимо, "skipped": пропущено}


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
    if len(plain) != 1:
        print("вызов: conditional_kit.py <facts.json> [--json]", file=sys.stderr)
        return 3
    d, отказ = каталог()
    if d is None:
        print(f"НЕ УДАЛОСЬ: {отказ}", file=sys.stderr)
        return 2
    try:
        сырьё = json.loads(Path(plain[0]).read_text("utf-8"))
    except (OSError, ValueError) as e:
        print(f"НЕ УДАЛОСЬ: файл фактов не прочитан: {e}", file=sys.stderr)
        return 2
    значения = {к: (v.get("value") if isinstance(v, dict) and "value" in v else v)
                for к, v in сырьё.items()}

    v = proposals(d["additions"], значения, _движок().evaluate)
    v["status"] = "fail" if v["propose"] else "pass"
    v["detail"] = (f"предложений: {len(v['propose'])}" if v["propose"]
                   else "добавок сверх базы не нужно")
    if v["skipped"]:
        v["detail"] += f", пропущено непроверяемых: {len(v['skipped'])}"
    if "--json" not in argv:
        print(("ЕСТЬ ЧТО ДОБАВИТЬ: " if v["propose"] else "БАЗЫ ДОСТАТОЧНО: ")
              + v["detail"], file=sys.stderr)
        for a in v["propose"]:
            print(f"  + {a['id']}: {a['what']}", file=sys.stderr)
            print(f"      почему: {a['why']}", file=sys.stderr)
        for a in v["skipped"]:
            print(f"  ? {a['id']}: {a['why']}", file=sys.stderr)
        if v["propose"]:
            print("  ставит установщик и по согласию человека — это "
                  "предложение, а не установка", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 1 if v["propose"] else 0


if __name__ == "__main__":
    sys.exit(main())
