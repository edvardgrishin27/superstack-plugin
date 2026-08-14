#!/usr/bin/env python3
"""SUPERSTACK — экипаж: волны, зоны, ярус. Считается кодом, а не на глаз.

Зачем это скрипт.

Три вещи в разбивке работы детерминированы полностью, и модели незачем их
«прикидывать»: волна таска — это `1 + max(волна блокеров)`, пересечение зон —
это сравнение путей, ярус — это диапазон по числу тасков. AutoPilot оставляет
все три модели («work them out once»), и там же честно называет цену: план,
чьи таски независимы, летит в два-три раза дольше нужного, потому что кто-то
посчитал волны неправильно и никто этого не заметил.

Четвёртое здесь важнее трёх первых, и его нет ни у кого.

ПОСЛЕДОВАТЕЛЬНЫЙ ПОЛЁТ. Волна обязана уходить ОДНИМ сообщением: два вызова
субагента в двух сообщениях исполняются один за другим, и параллельность,
посчитанная в плане, молча выбрасывается на доставке. Про этот отказ у
AutoPilot сказано, что изнутри он «выглядит точно как правильный полёт», —
то есть увидеть его нельзя.

Увидеть нельзя, а ИЗМЕРИТЬ можно: у товарищей по волне отметки старта обязаны
совпадать с точностью до секунд. Разошлись на минуты — волна летела гуськом,
и это видно из состояния, а не из добрых намерений.

  python3 crew.py waves  <состояние.json>   пересчитать и сверить волны
  python3 crew.py zones  <состояние.json>   пересечения зон внутри волны
  python3 crew.py tier   <состояние.json> [--declared T2]
  python3 crew.py flight <состояние.json>   летела ли волна параллельно
  python3 crew.py check  <состояние.json> [--declared T2]   всё сразу

  код 0 — чисто, 1 — нарушено, 2 — не смог проверить, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

#: Ярус читается из ПРОДУКТА, а не из длины спеки. Здесь проверяется лишь то,
#: что объявленный ярус не разошёлся с фактом: считать ярус по числу тасков
#: значит превратить ручку глубины в множитель нарезки.
TIERS = [("T0", 0, 0), ("T1", 2, 3), ("T2", 4, 8), ("T3", 9, 16)]
CEILING = 16

#: Насколько врозь могут стартовать товарищи по волне, чтобы это всё ещё был
#: один запуск. Порог щедрый намеренно: он ловит гуськовый полёт (минуты между
#: тасками), а не разброс планировщика.
TOGETHER_SECONDS = 45


def rows(state: dict) -> list:
    return [t for w in (state.get("waves") or {}).values() for t in w]


def declared_waves(state: dict) -> dict:
    return {t["id"]: int(w) for w, lst in (state.get("waves") or {}).items()
            for t in lst}


def compute_waves(tasks: list) -> "tuple[dict, list]":
    """Волна = 1 + max(волна блокеров). Возвращает (волны, беды).

    Цикл в блокерах здесь не исключение, а обычная опечатка в плане: он не даёт
    посчитать ничего и обязан назваться, а не разрешиться наугад.
    """
    by = {t["id"]: t for t in tasks}
    unknown = sorted({b for t in tasks for b in (t.get("blockedBy") or [])
                      if b not in by})
    if unknown:
        return {}, [f"таски ссылаются на несуществующие блокеры: {', '.join(unknown)}"]

    wave, guard = {}, len(by) + 1
    for _ in range(guard):
        moved = False
        for t in tasks:
            blockers = t.get("blockedBy") or []
            if all(b in wave for b in blockers):
                w = 1 + max((wave[b] for b in blockers), default=0)
                if wave.get(t["id"]) != w:
                    wave[t["id"]] = w
                    moved = True
        if not moved:
            break
    stuck = sorted(t["id"] for t in tasks if t["id"] not in wave)
    if stuck:
        return wave, [f"цикл в блокерах — не удалось разложить: {', '.join(stuck)}"]
    return wave, []


def zones_overlap(a: list, b: list) -> list:
    """Пересечение зон владения. Зона — каталог или модуль, не список файлов.

    Пересечением считается и совпадение, и вложенность: `src/bot/` и
    `src/bot/intake/` — одна территория, и два субагента там перезапишут друг
    друга. Потеря при этом молчаливая, поэтому сомнение решается в сторону
    «пересекается».
    """
    out = []
    for x in a or []:
        for y in b or []:
            nx, ny = x.rstrip("/") + "/", y.rstrip("/") + "/"
            if nx == ny or nx.startswith(ny) or ny.startswith(nx):
                out.append((x, y))
    return out


def zone_clashes(tasks: list, waves: dict) -> list:
    by_wave = {}
    for t in tasks:
        by_wave.setdefault(waves.get(t["id"]), []).append(t)
    bad = []
    for w, lst in sorted(by_wave.items(), key=lambda kv: (kv[0] is None, kv[0])):
        for i, a in enumerate(lst):
            for b in lst[i + 1:]:
                hit = zones_overlap(a.get("zone"), b.get("zone"))
                if hit:
                    bad.append({"wave": w, "a": a["id"], "b": b["id"],
                                "zones": [f"{x} ∩ {y}" for x, y in hit]})
    return bad


def missing_zones(tasks: list) -> list:
    return [t["id"] for t in tasks if not (t.get("zone") or [])]


def tier_of(n: int) -> "str | None":
    for name, lo, hi in TIERS:
        if lo <= n <= hi:
            return name
    return None


def tier_check(tasks: list, declared: str = None) -> dict:
    n = len(tasks)
    if n > CEILING:
        return {"status": "fail", "tasks": n,
                "detail": f"тасков {n} при потолке {CEILING} — мелкая нарезка не "
                          "покупает надёжность, она покупает расход; либо "
                          "обоснуй в спеке, либо раздели на два прогона"}
    actual = tier_of(n)
    if actual is None:
        return {"status": "fail", "tasks": n,
                "detail": f"тасков {n} — это между ярусами (T0 без разбивки, "
                          "T1 от двух): один таск дороже, чем ноль или два"}
    if declared and declared != actual:
        return {"status": "fail", "tasks": n, "tier": actual,
                "detail": f"объявлен {declared}, а тасков {n} — это {actual}; "
                          "ярус читается из продукта, и разойтись с фактом он "
                          "не может"}
    return {"status": "pass", "tasks": n, "tier": actual,
            "detail": f"{n} тасков — ярус {actual}"}


def _at(t: dict) -> "datetime | None":
    for key in ("started", "startedAt"):
        v = t.get(key)
        if v:
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None
    return None


def serial_flight(tasks: list, waves: dict, tolerance: int = TOGETHER_SECONDS) -> dict:
    """Летела ли волна одним запуском — по отметкам старта.

    Единственный способ поймать отказ, который «изнутри выглядит как
    правильный полёт». Стоит одного вычитания дат и ловит потерю, измеряемую
    часами ожидания человека.
    """
    by_wave = {}
    for t in tasks:
        by_wave.setdefault(waves.get(t["id"]), []).append(t)

    serial, unmeasured = [], []
    for w, lst in sorted(by_wave.items(), key=lambda kv: (kv[0] is None, kv[0])):
        if len(lst) < 2:
            continue
        stamps = {t["id"]: _at(t) for t in lst}
        missing = [i for i, v in stamps.items() if v is None]
        if missing:
            unmeasured.append(f"волна {w}: нет отметок старта у "
                              f"{', '.join(sorted(missing))}")
            continue
        lo, hi = min(stamps.values()), max(stamps.values())
        spread = (hi - lo).total_seconds()
        if spread > tolerance:
            serial.append({"wave": w, "spread_seconds": round(spread),
                           "tasks": sorted(stamps),
                           "why": "товарищи по волне стартовали врозь — волна "
                                  "ушла не одним сообщением, и посчитанная в "
                                  "плане параллельность выброшена на доставке"})
    return {"serial": serial, "unmeasured": unmeasured}


def check(state: dict, declared: str = None) -> dict:
    tasks = rows(state)
    if not tasks:
        return {"status": "unknown", "detail": "тасков нет — план не нарезан",
                "problems": [], "unmeasured": ["тасков нет"]}

    computed, wave_bad = compute_waves(tasks)
    stated = declared_waves(state)
    problems = list(wave_bad)

    wrong = [f"{tid}: объявлена волна {stated[tid]}, посчитана {computed[tid]}"
             for tid in sorted(computed) if tid in stated and stated[tid] != computed[tid]]
    if wrong:
        problems.append("волны разошлись с зависимостями — " + "; ".join(wrong[:5]))

    use = computed or stated
    clashes = zone_clashes(tasks, use)
    for c in clashes:
        problems.append(f"волна {c['wave']}: {c['a']} и {c['b']} делят территорию "
                        f"({', '.join(c['zones'])}) — их обязано разнести по волнам, "
                        "иначе они перезапишут друг друга молча")

    t = tier_check(tasks, declared)
    if t["status"] == "fail":
        problems.append(t["detail"])

    unmeasured = []
    nz = missing_zones(tasks)
    if nz:
        unmeasured.append(f"без зоны: {', '.join(nz[:8])} — непересечение волны "
                          "проверить нечем")

    f = serial_flight(tasks, use)
    unmeasured += f["unmeasured"]
    for s in f["serial"]:
        problems.append(f"волна {s['wave']} летела гуськом: разброс старта "
                        f"{s['spread_seconds']} с ({', '.join(s['tasks'])}) — {s['why']}")

    return {"status": "fail" if problems else ("unknown" if unmeasured else "pass"),
            "problems": problems, "unmeasured": unmeasured,
            "computed_waves": computed, "tier": t, "zone_clashes": clashes,
            "serial_flight": f["serial"],
            "detail": (f"{len(problems)} нарушений" if problems
                       else f"{len(tasks)} тасков, волн {len(set(use.values()))}, "
                            "зоны не пересекаются")}


def human(v: dict) -> str:
    head = {"pass": "ЭКИПАЖ РАЗЛОЖЕН", "fail": "РАЗБИВКА НАРУШЕНА",
            "unknown": "ПРОВЕРИТЬ НЕ СМОГ"}
    lines = [head.get(v["status"], v["status"]), f"  {v['detail']}"]
    for p in v.get("problems", []):
        lines.append(f"  ! {p}")
    for u in v.get("unmeasured", []):
        lines.append(f"  ? {u}")
    return "\n".join(lines)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


EXIT = {"pass": 0, "fail": 1, "unknown": 2}


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    declared = None
    if "--declared" in argv:
        i = argv.index("--declared")
        if i + 1 >= len(argv):
            print("НЕ УДАЛОСЬ: --declared ожидает ярус", file=sys.stderr)
            return 3
        declared = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    quiet = "--json" in argv
    argv = [a for a in argv if not a.startswith("--")]

    if len(argv) != 2 or argv[0] not in ("waves", "zones", "tier", "flight", "check"):
        print("вызов: crew.py waves|zones|tier|flight|check <состояние.json> "
              "[--declared T2]", file=sys.stderr)
        return 3
    cmd, path = argv[0], Path(argv[1])
    try:
        state = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        print(f"НЕ УДАЛОСЬ: состояние не прочитано: {e}", file=sys.stderr)
        return 3

    full = check(state, declared)
    if cmd == "check":
        v = full
    else:
        # Одна проверка НИКОГДА не даёт «чисто» за весь экипаж: остальные
        # остаются в отчёте непроверенными и держат код 2. Иначе «разложен»
        # покупается самой дешёвой из четырёх.
        keep = {"waves": "волны", "zones": "зоны", "tier": "ярус",
                "flight": "гуськом"}[cmd]
        picked = [p for p in full["problems"] if _belongs(p, cmd)]
        v = {"status": "fail" if picked else "unknown",
             "problems": picked, "part": keep,
             "unmeasured": [f"проверена одна часть ({keep}) — остальные не смотрели"],
             "detail": f"{len(picked)} нарушений в части «{keep}»"}

    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return EXIT.get(v["status"], 2)


def _belongs(problem: str, cmd: str) -> bool:
    marks = {"waves": ("волны разошлись", "цикл в блокерах", "несуществующие блокеры"),
             "zones": ("делят территорию",),
             "tier": ("ярус", "тасков", "потолке"),
             "flight": ("гуськом",)}
    return any(m in problem for m in marks[cmd])


if __name__ == "__main__":
    sys.exit(main())
