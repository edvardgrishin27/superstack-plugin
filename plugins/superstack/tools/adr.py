#!/usr/bin/env python3
"""SUPERSTACK — архитектурные решения: почему сделано так и что отвергли.

Зачем они вообще и почему НЕ для каждого проекта.

Спецификация умирает в день сдачи, и вместе с ней — каждое «почему так»:
причина схемы данных, то, что сборка доказала на четвёртом таске, какое слово
проект использует в своём смысле. Через полгода следующая сессия читает
работающий код и не находит ни одной причины, поэтому бодро переоткрывает
решённое и выбирает вариант, который уже пробовали и отвергли.

Для лендинга это не стоит ничего: решать там было нечего. Для мобильного
приложения, крупного сайта или SaaS решение о границах принимается один раз и
стоит дорого при пересмотре. Поэтому ярус T2+ — условие, а не украшение.

Три правила, и второе — то, ради чего файл существует.

  1. ADR БЕЗ ОТВЕРГНУТОГО ВАРИАНТА НЕ ЗАПИСЫВАЕТСЯ. Решение, у которого не
     было альтернативы, ничему не учит и разбавляет те, что учат: «взяли
     единственную библиотеку по единственной причине» — это не решение.
     Отвергнутый вариант БЕЗ причины бесполезен так же: пиши, чем не подошёл.

  2. РЕШЕНИЕ ВЛАДЕЕТ ЗОНОЙ, И ЗОНА ПРОВЕРЯЕТСЯ. У AutoPilot ADR пишутся и
     дальше живут надеждой: «прочитай, когда решение вот-вот отменят». Здесь
     ADR называет зону кода, которой правит, — и `governing()` отдаёт её тому,
     кто собирается в ней писать. Передача таска вкладывает такой ADR в промпт
     исполнителя КОДОМ. «Важно, чтобы на него смотрели» превращается из
     пожелания в невозможность писать в зоне, не увидев её решения.

  3. ПРОПАВШАЯ ЗОНА — ПРОТУХШИЙ ADR. Документ, чей код переехал, хуже
     отсутствующего: он выглядит источником правды и тихо расходится с кодом.
     Отсутствие пути — красное, а не примечание.

  python3 adr.py new   <каталог> <название> --zone src/bot/ --serves R01
                       --context ... --decision ... --because ...
                       --rejected "Postgres — нужен сервер, которого нет"
                       --consequences ...
  python3 adr.py check <каталог> [--tier T2] [--manifest m.json] [--root .]
  python3 adr.py governing <каталог> --zone src/bot/

  код 0 — чисто, 1 — нарушено, 2 — не смог проверить, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: С какого яруса решения стоят отдельной папки. Ниже — их просто не бывает
#: столько, чтобы папка окупилась; то немногое живёт в памяти проекта.
TIER_FLOOR = "T2"
TIERS_ORDER = ("T0", "T1", "T2", "T3")

_NAME = re.compile(r"^(\d{4})-[a-z0-9][a-z0-9-]*\.md$")
_FM = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_REJECTED = re.compile(r"^###\s+Отвергнут\w*\s*$", re.M)


def _split(text: str) -> "tuple[dict, str]":
    """Разобрать заголовок и тело. Свой разбор, потому что зависимостей нет.

    Формат намеренно узкий — `ключ: значение` и списки в квадратных скобках.
    Полный YAML здесь был бы лишней свободой: чем больше форм у заголовка, тем
    больше способов написать его так, что проверка промолчит.
    """
    m = _FM.match(text)
    if not m:
        return {}, text
    head = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            head[k.strip()] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        else:
            head[k.strip()] = v
    return head, m.group(2)


def read_all(d: Path) -> "tuple[list, list]":
    """Все ADR каталога. Возвращает (решения, беды разбора)."""
    if not d.is_dir():
        # Отсутствие каталога — ФАКТ, а не поломка: у лендинга решать нечего,
        # и требовать от него папку значит приучить пропускать красное. Нужен
        # ли каталог, решает ярус, и это решается в check().
        return [], []
    out, bad = [], []
    for f in sorted(d.glob("*.md")):
        m = _NAME.match(f.name)
        if not m:
            bad.append(f"{f.name}: имя не по форме NNNN-краткое-название.md")
            continue
        head, body = _split(f.read_text("utf-8", errors="replace"))
        out.append({"n": int(m.group(1)), "file": f.name, "path": f,
                    "zone": head.get("zone") or [], "serves": head.get("serves") or [],
                    "status": head.get("status") or "accepted",
                    "title": (body.strip().split("\n") or [""])[0].lstrip("# ").strip(),
                    "body": body})
    return out, bad


def _zones_touch(a: list, b: list) -> bool:
    for x in a or []:
        for y in b or []:
            nx, ny = x.rstrip("/") + "/", y.rstrip("/") + "/"
            if nx == ny or nx.startswith(ny) or ny.startswith(nx):
                return True
    return False


def governing(adrs: list, zone: list) -> list:
    """Какие решения правят этой зоной. Отменённые не отдаются.

    Это и есть точка, ради которой ADR перестаёт быть надеждой: тот, кто
    собирается писать в зоне, получает её решения не потому, что вспомнил
    о папке, а потому, что их вложили в его промпт.
    """
    return [a for a in adrs
            if a["status"].startswith("accepted") and _zones_touch(a["zone"], zone)]


def check(adrs: list, parse_bad: list, root: Path, tier: str = None,
          manifest: dict = None) -> dict:
    broken, unmeasured = list(parse_bad), []

    if tier and tier not in TIERS_ORDER:
        unmeasured.append(f"неизвестный ярус: {tier}")
        tier = None

    required = bool(tier) and TIERS_ORDER.index(tier) >= TIERS_ORDER.index(TIER_FLOOR)
    if required and not adrs:
        broken.append(
            f"ярус {tier}: решений нет ни одного — на этом размере границы и "
            "схема выбираются один раз и дорого пересматриваются; без записи "
            "их переоткроют через полгода и выберут уже отвергнутое")
    if not tier:
        unmeasured.append("ярус не передан (--tier) — нужны ли решения, неизвестно")

    nums = [a["n"] for a in adrs]
    if len(set(nums)) != len(nums):
        dup = sorted({n for n in nums if nums.count(n) > 1})
        broken.append(f"номера решений повторяются: {dup} — ссылка «см. 0003» "
                      "перестаёт указывать на одно")

    for a in adrs:
        # Отвергнутый вариант — то единственное, чего нет в коде. Код показывает,
        # что выбрали, и молчит о том, что рассмотрели и почему не взяли.
        if not _REJECTED.search(a["body"]):
            broken.append(f"{a['file']}: нет раздела «### Отвергнуто» — решение "
                          "без альтернативы ничему не учит и разбавляет те, "
                          "что учат")
        else:
            # Смотреть надо ТОЛЬКО до следующего заголовка. Иначе пустой раздел
            # «Отвергнуто» выглядит заполненным за счёт текста «Последствий»,
            # идущих ниже, — проверка молчит ровно на том файле, ради которого
            # написана.
            tail = a["body"][_REJECTED.search(a["body"]).end():]
            own = []
            for ln in tail.splitlines():
                if ln.startswith("#"):
                    break
                own.append(ln)
            if not [ln for ln in own if ln.strip()]:
                broken.append(f"{a['file']}: раздел «Отвергнуто» пуст")

        if not a["zone"]:
            unmeasured.append(f"{a['file']}: не названа зона — вложить это "
                              "решение в промпт исполнителя нечем, и оно снова "
                              "живёт надеждой, что кто-то откроет папку")
        for z in a["zone"]:
            if not (root / z.rstrip("/")).exists():
                broken.append(f"{a['file']}: зона `{z}` не существует — решение "
                              "протухло; документ, разошедшийся с кодом, хуже "
                              "отсутствующего, потому что выглядит правдой")
        if a["status"].startswith("superseded-by-"):
            ref = a["status"].split("superseded-by-", 1)[1].strip()
            if not ref.isdigit() or int(ref) not in nums:
                broken.append(f"{a['file']}: отменено решением {ref!r}, которого нет")

    # Каждая находка сборки обязана дожить до ADR: `D##` — это дорога, уже
    # пройденная и найденная закрытой, и она ценнее прочих записей.
    if manifest is not None:
        served = {s for a in adrs for s in a["serves"]}
        # Находка закрывается не только решением. Часть из них — уточнения, у
        # которых отвергнутого варианта не существует: «килобайт здесь
        # двоичный», «команда проверки не фильтрует по подстроке». Их место в
        # спеке, и когда спека их вобрала (статус `in-spec` и дальше), находка
        # прожила ровно то, ради чего записана, — она не потерялась.
        #
        # Требовать ADR на каждую значит либо писать решения с выдуманным
        # отвергнутым вариантом, либо не проходить гейт никогда. Первое хуже:
        # выдуманный отвергнутый вариант обесценивает те решения, где он
        # настоящий, и читать перестают все.
        absorbed = {r["id"] for r in manifest.get("requirements", [])
                    if r.get("kind") == "discovered"
                    and r.get("status") in ("in-spec", "in-ticket", "done",
                                            "deferred", "dropped")}
        d_rows = [r["id"] for r in manifest.get("requirements", [])
                  if r.get("kind") == "discovered"]
        lost = [d for d in d_rows if d not in served and d not in absorbed]
        if lost:
            broken.append(
                f"находки сборки без решения: {', '.join(lost)} — это дороги, "
                "уже пройденные и найденные закрытыми; без записи по ним "
                "пойдут снова")
    else:
        unmeasured.append("манифест не передан — сверить находки сборки не с чем")

    return {"status": "fail" if broken else ("unknown" if unmeasured else "pass"),
            "count": len(adrs), "broken": broken, "unmeasured": unmeasured,
            "detail": (f"{len(broken)} нарушений" if broken
                       else f"{len(adrs)} решений, зоны на месте")}


TEMPLATE = """---
zone: [{zone}]
serves: [{serves}]
status: accepted
---
# {n:04d} — {title}

## Контекст

{context}

## Решение

{decision}

## Почему

{because}

### Отвергнуто

{rejected}

## Последствия

{consequences}
"""


def new(d: Path, title: str, *, zone: list, serves: list, context: str,
        decision: str, because: str, rejected: list, consequences: str) -> Path:
    """Записать решение. Без отвергнутого варианта — не записывается."""
    if not rejected or not any(r.strip() for r in rejected):
        raise ValueError(
            "решение без отвергнутого варианта не записывается: код и так "
            "показывает, что выбрали, — ценно только то, что рассмотрели и "
            "почему не взяли")
    if not zone:
        raise ValueError(
            "решение без зоны нечем вложить в промпт исполнителя — оно снова "
            "будет жить надеждой, что кто-то откроет папку")
    adrs, _ = read_all(d)
    n = max((a["n"] for a in adrs), default=0) + 1
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "reshenie"
    # Латиницы в русском заголовке может не остаться — тогда имя вырождается.
    if not re.match(r"^[a-z0-9]", slug):
        slug = f"reshenie-{n:04d}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{n:04d}-{slug}.md"
    p.write_text(TEMPLATE.format(
        n=n, title=title, zone=", ".join(zone), serves=", ".join(serves),
        context=context, decision=decision, because=because,
        rejected="\n".join(f"- {r}" for r in rejected if r.strip()),
        consequences=consequences), encoding="utf-8")
    return p


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


_TAKES = {"--zone", "--serves", "--context", "--decision", "--because",
          "--rejected", "--consequences", "--tier", "--manifest", "--root"}


def _many(argv: list, name: str) -> list:
    out = []
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            out += [x.strip() for x in argv[i + 1].split(",") if x.strip()] \
                if name in ("--zone", "--serves") else [argv[i + 1]]
    return out


def _one(argv: list, name: str, default=""):
    return argv[argv.index(name) + 1] if name in argv and \
        argv.index(name) + 1 < len(argv) else default


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    plain, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in _TAKES:
            skip = True
        elif not a.startswith("--"):
            plain.append(a)

    if not plain or plain[0] not in ("new", "check", "governing"):
        print("вызов: adr.py new|check|governing <каталог> ...", file=sys.stderr)
        return 3
    cmd = plain[0]
    if len(plain) < 2:
        print("НЕ УДАЛОСЬ: нужен каталог решений", file=sys.stderr)
        return 3
    d = Path(plain[1])

    if cmd == "new":
        if len(plain) < 3:
            print("НЕ УДАЛОСЬ: нужно название решения", file=sys.stderr)
            return 3
        try:
            p = new(d, plain[2], zone=_many(argv, "--zone"),
                    serves=_many(argv, "--serves"),
                    context=_one(argv, "--context"),
                    decision=_one(argv, "--decision"),
                    because=_one(argv, "--because"),
                    rejected=_many(argv, "--rejected"),
                    consequences=_one(argv, "--consequences"))
        except ValueError as e:
            print(f"НЕ УДАЛОСЬ: {e}", file=sys.stderr)
            return 3
        print(json.dumps({"written": str(p)}, ensure_ascii=False, indent=1))
        return 0

    adrs, bad = read_all(d)
    if cmd == "governing":
        zone = _many(argv, "--zone")
        hit = governing(adrs, zone)
        print(json.dumps({"zone": zone,
                          "governing": [{"file": a["file"], "title": a["title"],
                                         "body": a["body"]} for a in hit]},
                         ensure_ascii=False, indent=1))
        return 0

    manifest = None
    mp = _one(argv, "--manifest")
    if mp:
        try:
            manifest = json.loads(Path(mp).read_text("utf-8"))
        except (OSError, ValueError) as e:
            print(f"НЕ УДАЛОСЬ: манифест не прочитан: {e}", file=sys.stderr)
            return 3

    v = check(adrs, bad, Path(_one(argv, "--root", ".")),
              _one(argv, "--tier", None) or None, manifest)
    if "--json" not in argv:
        head = {"pass": "РЕШЕНИЯ В ПОРЯДКЕ", "fail": "РЕШЕНИЯ НАРУШЕНЫ",
                "unknown": "ПРОВЕРИТЬ НЕ СМОГ"}
        print(head[v["status"]], file=sys.stderr)
        print(f"  {v['detail']}", file=sys.stderr)
        for b in v["broken"]:
            print(f"  ! {b}", file=sys.stderr)
        for u in v["unmeasured"]:
            print(f"  ? {u}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
