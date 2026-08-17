#!/usr/bin/env python3
"""SUPERSTACK — ревью по трём осям. Оси не смешиваются, находка знает адрес.

Зачем три оси, а не одно «ревью».

Изменение может пройти одну проверку и провалить другую, и хуже всего сочетание
«чисто написано» с «сделано не то»: оно выглядит нормально ровно до тех пор,
пока оси не разнесены. Поэтому здесь их три, и отчёты по ним НЕ СКЛАДЫВАЮТСЯ —
складывание позволяет одной оси замаскировать другую.

  · МАНИФЕСТ — доставлено ли то, о чём просил человек, ЕГО СЛОВАМИ.
  · СПЕКА    — реализовано ли то, что решила спецификация.
  · РЕМЕСЛО  — годится ли код как основание для следующего.

Что здесь принуждается кодом:

  1. КАЖДЫЙ РЕВЬЮЕР ПОЛУЧАЕТ ТОЛЬКО СВОЁ. Ревьюер, которому дали материал по
     чужой оси, будет судить и её — плохо и не говоря об этом. Пакет собирается
     здесь, и лишнее в него не кладётся.

  2. НАХОДКА БЕЗ ОСИ НЕ ПРИНИМАЕТСЯ. Ось — это не ярлык, а адрес: она решает,
     КУДА пойдёт починка.

  3. МАРШРУТ СЧИТАЕТСЯ ОДНИМ ВОПРОСОМ: мог ли исполнитель знать? Он видел свой
     таск, названные им разделы спеки и границы уже построенного — и больше
     ничего. Не мог знать — дефект в спеке или в нарезке, и гнать находку ему
     бессмысленно: он не видел этих слов. Именно здесь ревью чаще всего винит
     исполнителя за чужую ошибку.

  4. ПОТОЛОК В ДВА ДОЗАПРОСА. Третий — это уже не дешёвая починка: контекст
     перестал быть тем свежим, ради которого всё затевалось. Дальше свежий
     контекст И СМЕНА ПОДХОДА.

  5. РЕВЬЮЕР ПЕРЕЖИВАЕТ ТАСК. Новый ревьюер на каждый таск не видит, что таск
     05 противоречит таску 02, — целый класс находок, недостижимый иначе.
     Смена ревьюера вне границы волны здесь считается дефектом процесса.

  python3 review.py pack <состояние.json> <id> --axis manifest|spec|craft ...
  python3 review.py find <файл> --axis X --where f:12 --what "..." --must "..."
  python3 review.py route <файл>
  python3 review.py show <файл>

  код 0 — чисто, 1 — есть блокирующее, 2 — не смог проверить, 3 — вызов
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MANIFEST, SPEC, CRAFT = "manifest", "spec", "craft"
AXES = (MANIFEST, SPEC, CRAFT)

#: Куда идёт починка. Ось решает адрес, а не тяжесть находки.
TO_EXECUTOR, TO_SPEC, TO_CUT = "исполнителю", "в спецификацию", "в нарезку"

AXIS_RU = {MANIFEST: "манифест", SPEC: "спека", CRAFT: "ремесло"}

#: Что кладётся в пакет каждой оси. Списки РАЗНЫЕ намеренно: ревьюер, которому
#: дали чужой материал, судит по нему молча и выдаёт две половины ревью вместо
#: одного целого.
PACKET = {
    MANIFEST: ("diff", "requirements", "quotes", "acceptance"),
    SPEC: ("diff", "spec_sections", "interfaces", "acceptance"),
    CRAFT: ("diff", "interfaces", "conventions", "craft_rules"),
}

MAX_FOLLOWUPS = 2

EMPTY = {"schema": "superstack.review.v1", "task": None,
         "findings": [], "reviewers": {}, "followups": 0, "updated": None}


def load(path: Path) -> dict:
    if not path.is_file():
        return json.loads(json.dumps(EMPTY))
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(EMPTY))
    for k, v in EMPTY.items():
        d.setdefault(k, json.loads(json.dumps(v)))
    return d


def save(path: Path, data: dict, now: str = None) -> None:
    from datetime import datetime, timezone
    data["updated"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def pack(axis: str, material: dict) -> dict:
    """Пакет одной оси — и ничего сверх неё.

    Лишнее здесь не безобидно: ревьюер, получивший материал по чужой оси,
    судит и её, не сообщая об этом, и две перекрывающиеся половины ревью — это
    ровно то, против чего оси и разнесены.
    """
    if axis not in AXES:
        raise ValueError(f"неизвестная ось: {axis}")
    allowed = PACKET[axis]
    given = {k: v for k, v in material.items() if v}
    extra = sorted(set(given) - set(allowed))
    missing = [k for k in allowed if k not in given]
    return {"axis": axis, "material": {k: given[k] for k in allowed if k in given},
            "withheld": extra, "missing": missing,
            "status": "unknown" if missing else "pass",
            "why_withheld": ("это материал чужой оси: судить по нему будут молча"
                             if extra else "")}


def add_finding(data: dict, axis: str, where: str, what: str, must: str,
                blocking: bool = True) -> dict:
    """Записать находку. Без оси и без условия — не записывается.

    Находка, сформулированная пожеланием («стоило бы аккуратнее»), не может
    уйти исполнителю как есть: её придётся переписать, а переписать можно
    только прочитав дифф — то есть заплатив ровно тем контекстом оркестратора,
    ради экономии которого ревью и вынесено наружу.
    """
    if axis not in AXES:
        raise ValueError(f"неизвестная ось: {axis} — ось это адрес починки, "
                         "а не ярлык")
    if not must.strip():
        raise ValueError("находка обязана называть УСЛОВИЕ, которое должно "
                         "стать верным, а не пожелание: пожелание нельзя "
                         "отправить исполнителю не переписав, а переписать "
                         "его можно только прочитав дифф")
    if not what.strip():
        raise ValueError("находка обязана называть, что не так")
    data["findings"].append({"axis": axis, "where": where, "what": what.strip(),
                             "must": must.strip(), "blocking": bool(blocking)})
    return data


def route(finding: dict) -> dict:
    """Куда идёт починка. Один вопрос: мог ли исполнитель знать?

    Он видел свой таск, названные им разделы спеки и границы уже построенного.
    Слов человека он не видел никогда — поэтому находка по оси «манифест» это
    не его ошибка, и дозапрос к нему бессмыслен: ему пришлось бы догадаться о
    том, чего ему не показывали.
    """
    if finding["axis"] == MANIFEST:
        return {"to": TO_SPEC, "could_have_known": False,
                "why": "исполнитель не видел слов человека — требование "
                       "потеряно по дороге вниз, в спеке или в нарезке; "
                       "чинить надо там, а не в его контексте"}
    return {"to": TO_EXECUTOR, "could_have_known": True,
            "why": "исполнитель видел этот материал — дефект кода, чинится "
                   "в этом таске дозапросом"}


def followup_allowed(data: dict) -> dict:
    """Можно ли ещё один дозапрос в тот же контекст."""
    n = data.get("followups", 0)
    if n < MAX_FOLLOWUPS:
        return {"allowed": True, "used": n,
                "why": f"дозапрос {n + 1} из {MAX_FOLLOWUPS}"}
    return {"allowed": False, "used": n,
            "why": f"дозапросов уже {n} — контекст перестал быть тем свежим, "
                   "ради которого это было дёшево; дальше свежий контекст И "
                   "СМЕНА ПОДХОДА, а повтор той же попытки с надеждой не "
                   "считается попыткой"}


#: Две причины, по которым работа возвращается, и они лечатся по-разному.
#:
#: НЕДОДЕЛКА — исполнитель мог и не сделал: красный тест, блокирующая находка.
#: Его контекст ещё держит задачу, и дозапрос стоит одной строки-условия.
#:
#: ОТКАЗ — исполнитель пробовал и не смог: BLOCKED, кончившийся контекст,
#: уже провалившаяся починка. Дозапрос сюда бесполезен: тот же контекст той же
#: дорогой приведёт туда же. Нужен свежий контекст, а на второй раз — другая
#: дорога.
#:
#: Раньше оба случая шли одним маршрутом, и «не смог» получал дозапрос за
#: дозапросом — три попытки повторить то, что уже не вышло.
SHORTFALL, REFUSAL = "недоделка", "отказ"
KINDS = (SHORTFALL, REFUSAL)

#: Лестница целиком: 2 дозапроса + 1 свежий контекст + 1 другой подход.
#: Пятой попытки не бывает — дальше это не упрямая задача, а неверная нарезка
#: или неверно понятое требование, и чинить надо их.
MAX_RETRIES, MAX_APPROACHES = 1, 1


def repair_route(data: dict, kind: str, reason: str = "") -> dict:
    """Куда отправлять починку и на каком она шаге лестницы.

    Причина обязательна, и это не формальность. «Почини, чтобы проходило» —
    прямое приглашение лечить симптом: подогнать тест, обернуть пустым catch,
    захардкодить значение. Названная причина превращает починку в работу с
    источником; её отсутствие означает, что источник не найден, а это не
    ремонт, а отказ.
    """
    if kind not in KINDS:
        raise ValueError(f"неизвестный вид возврата: {kind} — есть "
                         + ", ".join(KINDS))
    if not reason.strip():
        return {"to": "свежий контекст", "step": "отказ",
                "allowed": True, "why":
                "причина не названа. Починка без названной причины лечит "
                "симптом: подгоняет тест, глушит ошибку, вписывает значение. "
                "Это отказ, а не ремонт — нужен свежий контекст"}

    f = data.get("followups", 0)
    r = data.get("retries", 0)
    a = data.get("approaches", 0)

    if kind == SHORTFALL and f < MAX_FOLLOWUPS:
        return {"to": "тому же исполнителю", "step": "дозапрос",
                "allowed": True,
                "why": f"дозапрос {f + 1} из {MAX_FOLLOWUPS}: его контекст ещё "
                       "держит задачу, и условия хватит"}
    if r < MAX_RETRIES:
        return {"to": "свежий контекст", "step": "повтор",
                "allowed": True,
                "why": "тот же путь, но с чистой головой: контекст исчерпан "
                       "или дозапросы кончились"}
    if a < MAX_APPROACHES:
        return {"to": "свежий контекст, другой подход", "step": "смена подхода",
                "allowed": True,
                "why": "повтор той же дорогой уже не сработал — нужен другой "
                       "путь: иная схема, другая библиотека, иной порядок"}
    return {"to": "человеку", "step": "сдача", "allowed": False,
            "why": f"исчерпано: {f} дозапросов, {r} повтор, {a} смена подхода. "
                   "Дальше это не упрямая задача, а неверная нарезка или "
                   "неверно понятое требование — чинить надо их, а не пробовать "
                   "пятый раз"}


def count_repair(data: dict, step: str) -> dict:
    """Отметить израсходованную попытку. Считает КОД, а не память ведущего."""
    key = {"дозапрос": "followups", "повтор": "retries",
           "смена подхода": "approaches"}.get(step)
    if key:
        data[key] = data.get(key, 0) + 1
    return data


def reviewer_continuity(data: dict, axis: str, who: str, wave: int) -> dict:
    """Не сменился ли ревьюер посреди волны.

    Новый ревьюер на каждый таск не видит, что таск 05 противоречит таску 02.
    Обновлять его на границе волны нормально — там перекрёстная память уже
    устарела; менять внутри волны значит платить за настройку и не получать
    того, за что платили.
    """
    prev = (data.get("reviewers") or {}).get(axis)
    data.setdefault("reviewers", {})[axis] = {"who": who, "wave": wave}
    if prev and prev.get("who") != who and prev.get("wave") == wave:
        return {"ok": False,
                "why": f"ревьюер оси «{AXIS_RU[axis]}» сменился внутри волны "
                       f"{wave}: перекрёстная память потеряна, а настройка "
                       "оплачена заново"}
    return {"ok": True, "why": "ревьюер тот же либо смена на границе волны"}


def verdict(data: dict) -> dict:
    by = {a: [f for f in data["findings"] if f["axis"] == a] for a in AXES}
    blocking = [f for f in data["findings"] if f["blocking"]]
    return {
        # Оси отдаются РАЗДЕЛЬНО. Сложенные в один список, они позволяют одной
        # замаскировать другую: чистое ремесло гасит впечатление от «сделано не то».
        "by_axis": {AXIS_RU[a]: by[a] for a in AXES},
        "counts": {AXIS_RU[a]: len(by[a]) for a in AXES},
        "blocking": len(blocking),
        "routes": [{**f, **route(f)} for f in data["findings"]],
        "followups": followup_allowed(data),
        "status": "fail" if blocking else "pass",
    }


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


_TAKES = {"--axis", "--where", "--what", "--must", "--who", "--wave",
          "--kind", "--reason"}


def _one(argv, name, default=""):
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
    if len(plain) < 2 or plain[0] not in ("find", "route", "show", "followup", "repair"):
        print("вызов: review.py find|route|show|followup|repair <файл> ...",
              file=sys.stderr)
        return 3
    cmd, path = plain[0], Path(plain[1])
    data = load(path)

    try:
        if cmd == "find":
            data = add_finding(data, _one(argv, "--axis"), _one(argv, "--where"),
                               _one(argv, "--what"), _one(argv, "--must"),
                               "--advisory" not in argv)
            save(path, data)
        elif cmd == "followup":
            r = followup_allowed(data)
            if r["allowed"]:
                data["followups"] = data.get("followups", 0) + 1
                save(path, data)
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0 if r["allowed"] else 1
        elif cmd == "repair":
            kind = _one(argv, "--kind") or SHORTFALL
            r = repair_route(data, kind, _one(argv, "--reason") or "")
            if r["allowed"]:
                data = count_repair(data, r["step"])
                save(path, data)
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0 if r["allowed"] else 1
    except ValueError as e:
        print(f"НЕ УДАЛОСЬ: {e}", file=sys.stderr)
        return 3

    v = verdict(data)
    if "--json" not in argv:
        print("РЕВЬЮ: " + ", ".join(f"{k} — {n}" for k, n in v["counts"].items()),
              file=sys.stderr)
        for f in v["routes"]:
            print(f"  [{AXIS_RU[f['axis']]}] {f['where']}: {f['what']} → "
                  f"{f['to']}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    # Запись находки — не вердикт о состоянии. Раньше `find` возвращал 1 сразу
    # после успешной записи: находка ЗАПИСАЛАСЬ, а код сообщал «ревью не
    # пройдено» — то есть первая же находка выглядела отказом инструмента.
    # Скрипт, который на это смотрит, бросает работу на середине ревью; человек
    # видит красное там, где механизм отработал ровно как задуман. Вердикт о
    # состоянии дают `route` и `show`, и там код 1 честен.
    if cmd == "find":
        return 0
    return 1 if v["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
