#!/usr/bin/env python3
"""SUPERSTACK — сколько стоил прогон. По логам, а не по ощущению.

Зачем это существует.

Все пороги в системе — «не больше шестнадцати частей», «потолок контекста»,
«два дозапроса» — до сих пор опирались на самооценку модели. Модель не видит
своих токенов: она не может ни подтвердить порог, ни заметить, что его давно
пробили. Разбор чужого инструмента (nick-vels/skills, `tools/measure-run.py`)
показал единственный источник правды, который есть у всех и который никто не
читает: журналы сессий, которые Claude Code пишет сам.

Что здесь считается и почему именно это.

  · ВЫХОД — то, за что платят дороже всего и что растёт от многословия;
  · ЧТЕНИЕ КЭША — почти бесплатно, но именно оно раздувает «объём» в отчётах,
    создавая ложное впечатление огромной работы;
  · ЗАПИСЬ КЭША — цена входа в новый контекст: каждый свежий помощник платит
    её заново, и отсюда видно, сколько стоила нарезка на части;
  · РАЗМЕР КОНТЕКСТА — сколько ходов уместилось; максимум показывает, кто
    подошёл к краю;
  · ВРЕМЯ — календарное и рабочее. Разница между ними это ожидание человека,
    и её полезно видеть отдельно: она не стоит денег, но стоит вечера.

Денег здесь нет намеренно. Цены меняются, тарифы разные, а придуманный рубль
в отчёте выглядит как измеренный. Вместо этого — нормированная единица, по
которой прогоны сравнивают между собой: выход вчетверо дороже записи кэша,
чтение кэша почти даром.

  python3 measure_run.py <каталог проекта> [--json] [--last N]

  код 0 — измерено, 2 — журналов нет (не смог измерить), 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

#: Во сколько раз дороже единица каждого вида. Не деньги — соотношение, по
#: которому прогоны сравнимы между собой при любых тарифах.
COST = {"out": 5.0, "cache_write": 1.25, "cache_read": 0.1, "in": 1.0}

#: Разрыв, после которого пауза считается ожиданием человека, а не работой.
#: Пять минут: меньше — обычная задумчивость между ходами, больше — человек
#: ушёл. Величина выбрана, а не измерена, и потому названа.
IDLE_GAP_SEC = 300

#: Потолок контекста, о котором предупреждён исполнитель. Здесь он нужен, чтобы
#: посчитать, сколько раз его прошли на самом деле: правило без замера остаётся
#: пожеланием.
CONTEXT_CEILING = 120_000


def logs_dir(project: Path) -> Path:
    """Каталог журналов Claude Code для этого проекта.

    Путь превращается в имя заменой разделителей на дефис — так делает сам
    Claude Code; ничего умнее здесь придумывать нельзя, иначе измеритель будет
    честно считать пустоту.
    """
    import re
    # Дефисом становится КАЖДЫЙ символ вне латиницы и цифр, а не только слэш:
    # у каталога «Super стэк» имя журнала — «...-Super-----», по одному дефису
    # на букву. Наивная замена одних слэшей давала несуществующий путь, и
    # измеритель честно докладывал «журналов нет» на живом проекте — то есть
    # молча превращался в ноль.
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(project.resolve()))
    return Path.home() / ".claude" / "projects" / slug


def _iso(v) -> "datetime | None":
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def measure_file(path: Path) -> dict:
    """Один журнал сессии."""
    out = cache_w = cache_r = inp = 0
    sizes, stamps = [], []
    tool_calls = 0
    # Одно сообщение приходит в журнал НЕСКОЛЬКО раз — по записи на каждый блок
    # ответа. Проверено на живом журнале: 5244 записи с расходом при 2505
    # сообщениях, отдельные повторялись девять раз. Считать построчно значило
    # завышать расход вдвое и объявлять контексты, которых не бывает.
    #
    # Инструмент, который считает неверно, хуже отсутствующего: его числам
    # верят и по ним меняют пороги.
    seen_msgs, seen_tools = set(), set()
    helpers_over = []

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message") or {}
            u = msg.get("usage")
            when = _iso(d.get("timestamp"))
            if when:
                stamps.append(when)
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    key = b.get("id") or f"{msg.get('id')}:{len(seen_tools)}"
                    if key not in seen_tools:
                        seen_tools.add(key)
                        tool_calls += 1
            mid = msg.get("id")
            if not u or (mid and mid in seen_msgs):
                continue
            if mid:
                seen_msgs.add(mid)
            out += u.get("output_tokens", 0) or 0
            cache_w += u.get("cache_creation_input_tokens", 0) or 0
            cache_r += u.get("cache_read_input_tokens", 0) or 0
            inp += u.get("input_tokens", 0) or 0
            size = ((u.get("input_tokens", 0) or 0)
                    + (u.get("cache_read_input_tokens", 0) or 0)
                    + (u.get("cache_creation_input_tokens", 0) or 0))
            sizes.append(size)
            # Потолок 120к — про ПОМОЩНИКОВ, а не про ведущую сессию: у неё
            # окно на порядок больше, и её большой контекст это норма работы, а
            # не тревога. Сравнивать их одной меркой значит показывать тысячи
            # «превышений» там, где всё в порядке, и утопить в них настоящие.
            if d.get("isSidechain") and size > CONTEXT_CEILING:
                helpers_over.append(size)

    stamps.sort()
    active = idle = 0.0
    for a, b in zip(stamps, stamps[1:]):
        gap = (b - a).total_seconds()
        if gap <= IDLE_GAP_SEC:
            active += gap
        else:
            idle += gap

    return {
        "file": path.name,
        "answers": len(sizes),
        "tokens": {"out": out, "cache_write": cache_w, "cache_read": cache_r,
                   "in": inp},
        "cost": round(out * COST["out"] + cache_w * COST["cache_write"]
                      + cache_r * COST["cache_read"] + inp * COST["in"]),
        "context": {"max": max(sizes) if sizes else 0,
                    "avg": round(sum(sizes) / len(sizes)) if sizes else 0,
                    "helpers_over_ceiling": len(helpers_over)},
        "tool_calls": tool_calls,
        "started": stamps[0].isoformat(timespec="seconds") if stamps else None,
        "finished": stamps[-1].isoformat(timespec="seconds") if stamps else None,
        "active_sec": round(active),
        "idle_sec": round(idle),
    }


def measure(project: Path, last: int = 0) -> dict:
    d = logs_dir(project)
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"status": "unknown", "dir": str(d), "sessions": [],
                "detail": "журналов сессий нет — измерять нечего. Это не «ноль "
                          "расхода», а отсутствие данных, и путать их нельзя"}
    if last:
        files = files[:last]

    sessions = [measure_file(f) for f in files]
    tot = {k: sum(s["tokens"][k] for s in sessions) for k in COST}
    return {
        "status": "pass",
        "dir": str(d),
        "sessions": sessions,
        "total": {
            "tokens": tot,
            "cost": sum(s["cost"] for s in sessions),
            "answers": sum(s["answers"] for s in sessions),
            "tool_calls": sum(s["tool_calls"] for s in sessions),
            "active_sec": sum(s["active_sec"] for s in sessions),
            "idle_sec": sum(s["idle_sec"] for s in sessions),
            "helpers_over_ceiling": sum(s["context"]["helpers_over_ceiling"]
                                        for s in sessions),
            "context_max": max((s["context"]["max"] for s in sessions), default=0),
        },
        "detail": f"измерено сессий: {len(sessions)}",
    }


def _hours(sec: int) -> str:
    h, m = divmod(round(sec / 60), 60)
    return f"{h} ч {m} мин" if h else f"{m} мин"


def _k(n: int) -> str:
    return f"{n / 1000:.0f}к" if n >= 1000 else str(n)


def human(v: dict) -> str:
    """Отчёт человеческими словами: что израсходовано и что это значит."""
    if v["status"] != "pass":
        return v["detail"]
    t = v["total"]
    tok = t["tokens"]
    work = tok["out"]
    reread = tok["cache_read"]
    entry = tok["cache_write"]
    lines = [
        f"Сессий измерено: {len(v['sessions'])} · ответов помощников: {t['answers']}",
        f"Написано ответов: {_k(work)} · перечитано из памяти: {_k(reread)} · "
        f"вход в новые контексты: {_k(entry)}",
        f"Сравнимая цена прогона: {_k(t['cost'])} условных единиц "
        "(написанное дороже перечитанного в пятьдесят раз)",
        f"Самый длинный разговор: {_k(t['context_max'])} · помощников, "
        f"перешагнувших свой потолок {_k(CONTEXT_CEILING)}: "
        f"{t['helpers_over_ceiling']}",
        f"Работа шла {_hours(t['active_sec'])}, ждали друг друга "
        f"{_hours(t['idle_sec'])}",
    ]
    if reread > work * 20:
        lines.append("Перечитывание сильно больше написанного — это цена длинных "
                     "контекстов: работа идёт поверх всего сказанного раньше.")
    return "\n".join(lines)


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
        print("вызов: measure_run.py <каталог проекта> [--json] [--last N]",
              file=sys.stderr)
        return 3
    project = Path(plain[0])
    if not project.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {project}", file=sys.stderr)
        return 3
    try:
        last = int(argv[argv.index("--last") + 1]) if "--last" in argv else 0
    except (ValueError, IndexError):
        print("НЕ УДАЛОСЬ: --last ждёт число", file=sys.stderr)
        return 3

    v = measure(project, last)
    if "--json" in argv:
        print(json.dumps(v, ensure_ascii=False, indent=1))
    else:
        print(human(v))
    return 0 if v["status"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
