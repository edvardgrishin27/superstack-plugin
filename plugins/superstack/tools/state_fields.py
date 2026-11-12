#!/usr/bin/env python3
"""SUPERSTACK — узкие команды, которые меняют состояние проекта.

Зачем отдельный файл. Запись хода работ (tools/progress.py) подошла к
границе длины файла, а новые команды состояния всё равно нужны. Они живут
здесь и пишут только через `progress.load` и `progress.save` под тем же
замком: способ записи состояния остаётся один.

  python3 state_fields.py promote <файл-состояния>

promote — мост фазы проверки. Пока части работы возвращаются словами
ведущего, «доказано» без него недостижимо. После зелёной проверки он
переводит ждущие и заявленные части в «доказано» — но только если гейт сам
оставил свежую зелёную запись (tools/gate_record.py). Записи нет, она
красная или устарела — ничего не меняется, человек слышит одну фразу, код 1.
Когда каждую часть начнёт сдавать свой прогон, мост уйдёт вместе с вызовом
из фазы проверки.

  код 0 — переведено, 1 — свежей зелёной записи нет, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_record  # noqa: E402
import progress  # noqa: E402

#: Какие части мост вправе перевести. «В работе» не переводится: её ещё
#: пишут, и зелёный прогон говорит о дереве до её конца.
ПЕРЕВОДИМЫЕ = (progress.WAITING, progress.CLAIMED)


def promote(path: Path) -> int:
    """Перевести ждущие и заявленные части в «доказано» по записи гейта."""
    with progress._Lock(path):
        data = progress.load(path)
        запись = gate_record.свежая_зелёная(path)
        if запись is None:
            print(gate_record.СО_СЛОВ, file=sys.stderr)
            return 1
        части = [(t["id"], t["name"]) for w in data["waves"].values() for t in w
                 if t.get("status") in ПЕРЕВОДИМЫЕ]
        for tid, name in части:
            data = progress.set_task(data, tid, name, None, progress.PROVEN,
                                     exit_code=запись["exit_code"])
        progress.save(path, data)
        progress.снимок_на_панель(path, data)
    print(json.dumps({"promoted": [tid for tid, _ in части]},
                     ensure_ascii=False))
    return 0


def main() -> int:
    progress.halt_if_paused()
    argv = sys.argv[1:]
    if len(argv) != 2 or argv[0] != "promote":
        print("вызов: state_fields.py promote <файл-состояния>", file=sys.stderr)
        return 3
    return promote(Path(argv[1]))


if __name__ == "__main__":
    # Повреждённый файл — одна фраза и код 5, а не трассировка.
    sys.exit(progress.atomic_store.запустить(main))
