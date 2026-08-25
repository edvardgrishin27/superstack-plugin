#!/usr/bin/env python3
"""SUPERSTACK — что проверить на безопасность и нагрузку. Набором, а не по памяти.

Зачем это отдельный инструмент.

Прогон сборки отвечает на вопрос «работает ли то, что построили». Вопрос «что
из непостроенного нас убьёт» он не задаёт вовсе — и не может: в коде видно
написанное, а дыра выглядит как чистое место. Пропущенная проверка владения
объектом неотличима от аккуратного эндпоинта, отсутствующий лимит попыток — от
обычной формы входа.

Поэтому список задаётся снаружи, целиком и заранее. Он не выводится из кода и
не вспоминается по ходу: вспоминают то, что уже знают, а забывают ровно то,
чего не знали.

Две вещи, которые здесь принуждаются:

  · НАБОР ЗАВИСИТ ОТ ТОГО, ЧТО ПОСТРОЕНО. Лендингу нечего проверять про роли;
    SaaS без проверки владения отдаёт чужие данные в первый же день. Тип
    называет человек — угадывать за него нельзя.

  · ОТМЕТКА «ПРОВЕРЕНО» ТРЕБУЕТ ССЫЛКИ. «Посмотрел, всё хорошо» звучит ровно
    так же, как непроверенное, и стоит столько же.

  python3 security_pack.py list                          все проверки
  python3 security_pack.py for <тип> [--level X] [--has вход,платежи,ии]
                                                         набор под продукт
  python3 security_pack.py topics <тип> [--level X] [--has ...]
                                                         темы и сколько в каждой
  python3 security_pack.py what                          какие бывают возможности
  python3 security_pack.py show <id>                     готовый вопрос к коду
  python3 security_pack.py done <файл> <id> --where "файл:строки"
  python3 security_pack.py status <файл> --kind <тип>    что осталось

  код 0 — всё пройдено, 1 — есть непроверенное, 2 — нечего проверять,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "security-checks.json"
#: Полный каталог — 571 проверка, собранная исследованием по первоисточникам.
#: Старый файл остаётся: в нём формулировки заказчика, проверенные разговором,
#: и они входят в каталог первыми.
CATALOG = Path(__file__).resolve().parent.parent / "data" / "checks-catalog.json"

LEVELS = ("обязательный", "важный", "дополнительный")


def load(path: Path = DATA) -> dict:
    return json.loads(path.read_text("utf-8"))


def catalog(path: Path = CATALOG) -> dict:
    return json.loads(path.read_text("utf-8"))


def needed_topics(has: list, data: dict = None) -> list:
    """Темы, которые ИМЕЮТ СМЫСЛ для продукта с такими возможностями.

    Тема без единой своей возможности не показывается вовсе: набор, где
    лендингу требуют двухфакторку и защиту платёжного вебхука, человек
    закрывает целиком — и вместе с ним закрывает те проверки, которые ему
    действительно были нужны.
    """
    data = data or catalog()
    rules = data["возможности"]["тема_требует"]
    have = set(has or [])
    unknown = have - set(data["возможности"]["список"])
    if unknown:
        raise ValueError("неизвестные возможности: " + ", ".join(sorted(unknown))
                         + " — есть " + ", ".join(sorted(data["возможности"]["список"])))
    return [t for t, need in rules.items() if not need or (set(need) & have)]


def pick(kind: str, level: str = None, topic: str = None,
         data: dict = None, has: list = None) -> list:
    """Проверки под тип продукта, с отбором по уровню и теме.

    Полный набор для SaaS — пять с половиной сотен пунктов, и человек, которому
    их показали списком, не сделает ни одной. Поэтому отбор обязателен: сначала
    то, без чего нельзя выпускать, потом по одной теме за раз.
    """
    data = data or catalog()
    kinds = set(data["типы_продукта"])
    if kind not in kinds:
        raise ValueError(f"неизвестный тип продукта: {kind} — есть "
                         + ", ".join(sorted(kinds)))
    if level and level not in LEVELS:
        raise ValueError(f"неизвестный уровень: {level} — есть " + ", ".join(LEVELS))
    out = [c for c in data["проверки"] if kind in c.get("типы", [])]
    if has is not None:
        ok = set(needed_topics(has, data))
        out = [c for c in out if c.get("тема") in ok]
    if level:
        out = [c for c in out if c.get("уровень") == level]
    if topic:
        out = [c for c in out if c.get("тема") == topic]
    return out


def topics_of(kind: str, level: str = None, data: dict = None,
              has: list = None) -> list:
    """Темы с числом проверок — по ним работу и делят на заходы."""
    from collections import Counter
    c = Counter(x.get("тема", "") for x in pick(kind, level, None, data, has))
    return sorted(c.items(), key=lambda kv: -kv[1])


def kinds(data: dict = None) -> list:
    return sorted((data or load())["kinds"]["map"])


def pack_for(kind: str, data: dict = None) -> list:
    """Набор проверок под тип продукта.

    Неизвестный тип не заменяется «общим набором»: тихая подстановка выдала бы
    неполную проверку за полную. Лучше отказ с перечнем известных.
    """
    data = data or load()
    m = data["kinds"]["map"]
    if kind not in m:
        raise ValueError(f"неизвестный тип продукта: {kind} — есть "
                         + ", ".join(sorted(m)))
    by_id = {c["id"]: c for c in data["checks"]}
    return [by_id[i] for i in m[kind] if i in by_id]


def state(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {"schema": "superstack.security-state.v1", "done": {}}


def mark_done(st: dict, check_id: str, where: str) -> dict:
    if not where.strip():
        raise ValueError("отметка «проверено» требует ссылки на файл и строки: "
                         "без неё она звучит так же, как непроверенное")
    st.setdefault("done", {})[check_id] = where.strip()
    return st


def status(st: dict, kind: str, data: dict = None) -> dict:
    need = [c["id"] for c in pack_for(kind, data)]
    done = st.get("done") or {}
    left = [i for i in need if i not in done]
    return {"kind": kind, "need": len(need), "done": len(need) - len(left),
            "left": left,
            "status": "pass" if not left else "fail",
            "detail": (f"пройдено {len(need) - len(left)} из {len(need)}"
                       if left else f"пройдены все {len(need)}")}


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
        print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
        return 3
    cmd, rest = plain[0], plain[1:]
    data = load()

    def _flag(name):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else ""

    try:
        if cmd == "list":
            for c in data["checks"]:
                print(f"{c['id']:18} {c['что']}")
            print(f"\nтипы продукта: {', '.join(kinds(data))}")
            return 0
        if cmd == "what":
            c = catalog()
            for k, v in c["возможности"]["список"].items():
                print(f"{k:14} {v}")
            print("\n" + c["возможности"]["правило"])
            return 0
        if cmd == "topics":
            if not rest:
                print("НЕ УДАЛОСЬ: нужен тип продукта", file=sys.stderr)
                return 3
            has = [x.strip() for x in (_flag("--has") or "").split(",") if x.strip()]
            rows = topics_of(rest[0], _flag("--level") or None,
                             None, has or None)
            for name, n in rows:
                print(f"{n:>4}  {name}")
            print(f"\nвсего: {sum(n for _, n in rows)}")
            return 0
        if cmd == "for":
            if not rest:
                print("НЕ УДАЛОСЬ: нужен тип продукта: " + ", ".join(kinds(data)),
                      file=sys.stderr)
                return 3
            has = [x.strip() for x in (_flag("--has") or "").split(",") if x.strip()]
            level = _flag("--level") or None
            if has or level:
                found = pick(rest[0], level, _flag("--topic") or None, None,
                             has or None)
                for c in found:
                    print(f"[{c.get('уровень','')[:3]}] {c['что'][:96]}")
                print(f"\nвсего: {len(found)}")
            else:
                for c in pack_for(rest[0], data):
                    print(f"{c['id']:18} {c['что']}")
                print(f"\nи отдельно — нагрузка: {data['load']['why'][:80]}…")
            return 0
        if cmd == "show":
            if not rest:
                print("НЕ УДАЛОСЬ: нужен id проверки", file=sys.stderr)
                return 3
            if rest[0] == "load":
                print(data["load"]["промпт"])
                return 0
            c = next((x for x in data["checks"] if x["id"] == rest[0]), None)
            if not c:
                print(f"НЕ УДАЛОСЬ: нет проверки {rest[0]}", file=sys.stderr)
                return 3
            print(c["промпт"])
            return 0
        if cmd == "done":
            if len(rest) < 2:
                print("НЕ УДАЛОСЬ: нужен файл состояния и id проверки",
                      file=sys.stderr)
                return 3
            p = Path(rest[0])
            st = mark_done(state(p), rest[1], _flag("--where"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(st, ensure_ascii=False, indent=1),
                         encoding="utf-8")
            print(json.dumps({"done": rest[1]}, ensure_ascii=False))
            return 0
        if cmd == "status":
            if not rest:
                print("НЕ УДАЛОСЬ: нужен файл состояния", file=sys.stderr)
                return 3
            v = status(state(Path(rest[0])), _flag("--kind") or "сайт", data)
            if "--json" in argv:
                print(json.dumps(v, ensure_ascii=False, indent=1))
            else:
                print(f"БЕЗОПАСНОСТЬ: {v['detail']}", file=sys.stderr)
                for i in v["left"]:
                    c = next(x for x in data["checks"] if x["id"] == i)
                    print(f"  не проверено: {i} — {c['что']}", file=sys.stderr)
            return 0 if v["status"] == "pass" else 1
    except ValueError as e:
        print(f"НЕ УДАЛОСЬ: {e}", file=sys.stderr)
        return 3

    print(f"НЕ УДАЛОСЬ: неизвестная команда {cmd}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
