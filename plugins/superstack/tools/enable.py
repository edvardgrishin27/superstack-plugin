#!/usr/bin/env python3
"""SUPERSTACK — заведён ли он в ЭТОМ проекте.

Зачем это вообще существует.

Плагины Claude Code ставятся глобально: `Scope: user`. Их хуки объявляются без
привязки к проекту, поэтому установленный SUPERSTACK начинает работать во ВСЕХ
проектах на машине — включая те, где человек его никогда не звал. Найдено
заказчиком на живом случае: в соседнем каталоге он пишет обучающие материалы,
а система спрашивала там про уроки и запускала гейт верификации.

Самый неприятный из этих хуков — гейт верификации: в чужом проекте он может не
дать закрыть ход, который человеку нечем закрывать, потому что ни тестов, ни
плана там нет и не предполагалось.

Выключатели у продукта были только глобальные (`SUPERSTACK_DISABLE=1`, файл
`PAUSE`) — то есть «выключить везде». Нужно ровно обратное: «включить там, где
позвали». Отсюда реестр.

ЧТО СЧИТАЕТСЯ ЗАВЕДЕНИЕМ. Явный вызов скилла: `/go`, `/superstack`, `/what`,
`/fix`, `/oops` первой строкой ставят отметку. Скиллы остаются доступны везде —
их вызывают руками, и отзываться они обязаны. Молчат ХУКИ, потому что хук
срабатывает сам, без спроса.

ПОЧЕМУ РЕЕСТР ГЛОБАЛЬНЫЙ, А НЕ ФАЙЛ В ПРОЕКТЕ. Файл в проекте уезжает в чужой
git и заводит систему у того, кто её не ставил. Заведение — решение человека на
его машине, и жить оно должно там же.

Вложенность учитывается: проект внутри заведённого дерева считается заведённым,
иначе работа в подкаталоге репозитория выглядела бы как чужой проект.

Та же вложенность и опасна в другую сторону: заведи каталог ВЫШЕ, чем сам
проект, — и заведённым считается всё дерево под ним. Для домашнего каталога,
его предков и корня файловой системы это дерево — буквально все проекты на
машине, то есть ровно та болезнь, ради которой реестр придуман. В десктопном
приложении новый чат нередко открывается именно в $HOME — это не крайний
случай, а обычный. Поэтому на таких каталогах заведение отказывает вместо
того, чтобы завести наполовину (см. `too_broad()`).

  python3 enable.py .              завести проект (идемпотентно)
  python3 enable.py . --check      только проверить: 0 — заведён, 1 — нет
  python3 enable.py . --forget     забыть проект (или покрывающую запись, если своей нет)
  python3 enable.py --list         показать заведённые

  код 0 — да/сделано, 1 — не заведён, 2 — не смог, 3 — ошибка вызова,
  код 4 — каталог для заведения недопустимо широк (см. too_broad())
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Код возврата у отказа завести слишком широкий каталог. Не переиспользует 3
#: («ошибка вызова»): вызов здесь правильный, каталог существует — это
#: осознанный отказ по существу, а не «не разобрались, что просят».
EXIT_TOO_BROAD = 4


def registry() -> Path:
    state = os.environ.get("SUPERSTACK_STATE_DIR") or \
        str(Path.home() / ".claude" / "superstack")
    return Path(state) / "projects"


def known() -> list:
    try:
        return [ln.strip() for ln in
                registry().read_text("utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


def enabled_for(path: Path) -> "str | None":
    """Заведён ли проект. Возвращает запись, которая его покрывает.

    Совпадение префиксное по СЕГМЕНТАМ пути: `/a/bc` не должен считаться
    заведённым из-за записи `/a/b`, хотя строкой одна начинается с другой.
    """
    p = path.resolve()
    for entry in known():
        e = Path(entry)
        if p == e or e in p.parents:
            return entry
    return None


class TooBroad(ValueError):
    """Заведение отклонено: каталог шире, чем допускает `too_broad()`.

    Это НЕ ошибка вызова — аргументы верные, каталог существует. Это
    осознанный отказ: завести такой каталог значило бы включить SUPERSTACK
    во всех проектах на машине разом, а не в одном.
    """


def too_broad(path: Path) -> "str | None":
    """Причина отказать в заведении — или `None`, если каталог достаточно узкий.

    Покрытие в `enabled_for()` префиксное по дереву: заведи широкий каталог —
    и заведённым автоматически считается всё, что внутри. Для трёх случаев
    ниже «всё, что внутри» — это буквально все проекты на машине, то есть та
    самая болезнь, ради лечения которой реестр придуман (см. докстринг
    модуля). Дом человека вычисляется через `Path.home()`, а не хардкодится
    строками путей: список строк ловит одну ОС и упускает остальные, а свой
    дом система уже знает сама.

      · сам домашний каталог — обычное место, где открывается новый чат в
        десктопном приложении, если человек ещё не зашёл ни в один проект;
      · корень файловой системы — тот же случай в пределе;
      · каталог ВЫШЕ домашнего (например /Users, /home) — под ним лежат
        домашние каталоги всех, кто пользуется этой машиной, а не только автора.
    """
    p = path.resolve()
    home = Path.home().resolve()
    root = Path(p.anchor) if p.anchor else p
    if p == root:
        return f"«{p}» — корень файловой системы, в нём лежит вообще всё"
    if p == home:
        return f"«{p}» — твой домашний каталог, в нём лежат все твои проекты"
    if p in home.parents:
        return (f"«{p}» лежит выше домашнего каталога ({home}) — под ним "
                "домашние каталоги всех, кто пользуется этой машиной")
    return None


def enable(path: Path) -> str:
    reason = too_broad(path)
    if reason:
        raise TooBroad(reason)
    p = str(path.resolve())
    cur = known()
    if p not in cur:
        r = registry()
        r.parent.mkdir(parents=True, exist_ok=True)
        r.write_text("\n".join(cur + [p]) + "\n", encoding="utf-8")
    return p


def forget(path: Path) -> "str | None":
    """Убрать запись, которая покрывает путь. Вернуть то, что реально убрано.

    Ищет тем же способом, каким `enabled_for()` проверяет заведение: сперва
    точное совпадение, а если его нет — покрывающую запись выше по дереву.
    Без второго шага `--forget` из подкаталога заведённого дерева отвечал бы
    «не был заведён» и ничего не откатывал, хотя `--check` там же отвечает
    «да, заведён» — путь назад был бы недоступен именно оттуда, где человек
    находится.
    """
    target = enabled_for(path)
    if target is None:
        return None
    registry().write_text(
        "\n".join(x for x in known() if x != target) + "\n", encoding="utf-8")
    return target


def main() -> int:
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("--")}
    plain = [a for a in argv if not a.startswith("--")]
    unknown = flags - {"--check", "--forget", "--list", "--json"}
    if unknown:
        print(f"НЕ УДАЛОСЬ: неизвестный флаг {', '.join(sorted(unknown))}",
              file=sys.stderr)
        return 3

    if "--list" in flags:
        for p in known():
            print(p)
        return 0
    if len(plain) != 1:
        print("вызов: enable.py <каталог> [--check|--forget] | --list",
              file=sys.stderr)
        return 3

    path = Path(plain[0]).expanduser()
    if not path.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {path}", file=sys.stderr)
        return 3

    if "--check" in flags:
        hit = enabled_for(path)
        if "--json" in flags:
            print(json.dumps({"enabled": hit is not None, "covered_by": hit},
                             ensure_ascii=False))
        return 0 if hit else 1

    if "--forget" in flags:
        here = str(path.resolve())
        removed = forget(path)
        if removed is None:
            print("не был заведён", file=sys.stderr)
        elif removed == here:
            print("ЗАБЫТ", file=sys.stderr)
        else:
            print(f"ЗАБЫТ: {removed}", file=sys.stderr)
            print(f"  своей отметки на «{here}» не было — убрана более "
                  f"широкая, и теперь система молчит не только здесь, а "
                  f"везде внутри «{removed}»: если она нужна в одном из тех "
                  "проектов, позови её там заново", file=sys.stderr)
        return 0

    try:
        p = enable(path)
    except TooBroad as e:
        print(f"НЕ ЗАВЕДЁН: {e}", file=sys.stderr)
        print("  вместо этого перейди в каталог своего проекта и позови "
              "систему там", file=sys.stderr)
        return EXIT_TOO_BROAD
    print(f"ЗАВЕДЁН: {p}", file=sys.stderr)
    print("  хуки SUPERSTACK работают здесь; в остальных проектах молчат",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
