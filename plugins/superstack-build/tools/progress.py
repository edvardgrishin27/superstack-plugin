#!/usr/bin/env python3
"""SUPERSTACK — состояние стройки: что доказано, что заявлено, что не начато.

Зачем это отдельный инструмент, а не поле в отчёте.

Панель хода работ — самая опасная поверхность продукта. Текст можно не дочитать,
полоску нельзя не увидеть: зелёная шкала читается как факт, даже когда за ней
стоит одно лишь слово агента «готово». Красивый дашборд поверх недоказанного
даёт уверенность ровно там, где её быть не должно.

Поэтому здесь «готово» — не статус, а СЛЕДСТВИЕ. Задача переходит в `proven`
только вместе с кодом возврата гейта, и записать иначе нельзя: функция требует
доказательство отдельным аргументом и отказывается без него. Сказанное агентом
записывается как `claimed` и на экране выглядит иначе — не потому, что мы не
доверяем, а потому, что это другое утверждение.

Три величины, которые обычно копятся молча, здесь считаются наравне с прогрессом:

  · ЗАГЛУШКИ    — что осталось ненаписанным под видом написанного;
  · ДОПУЩЕНИЯ   — что решили за человека, не спросив;
  · НЕИЗВЕСТНЫЕ — что не выяснили и понесли дальше.

Прогресс без этих трёх — половина картины, и именно та половина, которую
приятно показывать.

  python3 progress.py init <файл> <название>
  python3 progress.py stage <файл> <имя> <статус> [--detail X]
  python3 progress.py task  <файл> <id> <имя> --wave N --status X [--exit-code N]
                            [--requirements R01,R02] [--zone src/a/] [--blocked-by 01]
                            [--started ISO]   отметка старта; при --status running
                                              проставляется сама
                            [--goal "..."] [--acceptance "a;b"] [--quotes "..."]
                            [--spec-sections "..."]   — без них таск не передать
                            [--holdout "a;b"]  проверки, скрытые от исполнителя
  python3 progress.py debt  <файл> <вид> <текст>        вид: stub|assumption|env
  python3 progress.py debt  <файл> <вид> --none         смотрели, закрывать нечего
  python3 progress.py source <файл> <путь>              где лежат требования и задачи
  python3 progress.py req   <файл> --total N --covered N [--dropped N] [--deferred N]
  python3 progress.py show  <файл>

  код 0 — прочитано, 2 — состояние неполно, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Состояние задачи. Порядок важен: он же порядок убывания доверия.
PROVEN = "proven"        # гейт вернул ноль — единственное настоящее «готово»
CLAIMED = "claimed"      # агент сказал «готово», доказательства нет
RUNNING = "running"
WAITING = "waiting"
TASK_STATES = (PROVEN, CLAIMED, RUNNING, WAITING)

STAGE_STATES = (PROVEN, CLAIMED, RUNNING, WAITING)
DEBT_KINDS = ("stub", "assumption", "env")

#: Кому и что делать. Долг — не бухгалтерия, а список того, что нужно ОТ
#: ЧЕЛОВЕКА: «сколько долга» и «что от меня требуется» — разные вопросы,
#: и второй единственный, ради которого этот блок стоит смотреть.
DEBT_ASKS = {
    "stub": "заглушки — нужны твои данные",
    "assumption": "решения, принятые за тебя",
    "env": "переменные окружения — нужны твои значения",
}

EMPTY = {
    "schema": "superstack.progress.v1",
    "project": "",
    "stages": [],
    "waves": {},
    "debt": {k: [] for k in DEBT_KINDS},
    "requirements": {"total": None, "covered": None,
                     "dropped": 0, "deferred": 0, "stubbed": 0},
    # Отметка «смотрели и закрывать нечего». Без неё пустой список неотличим
    # от «никто не записывал», а это разные утверждения: первое — результат
    # проверки, второе — её отсутствие. Показывать их одинаково значит выдать
    # неведение за порядок.
    "debt_reviewed": {},
    "updated": None,
    "source": None,
}


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


def save(path: Path, data: dict, now: "str | None" = None) -> None:
    """Записать состояние, проставив отметку времени.

    Панель без «когда обновлено» выглядит живой, даже когда данные недельной
    давности: число без отметки — то же утверждение без провенанса, которое
    система запрещает везде. Часы подаются параметром, иначе тест перестаёт
    давать один и тот же ответ завтра.
    """
    from datetime import datetime, timezone
    data["updated"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _wave_of(data: dict, tid: str) -> int:
    """В какой волне таск лежит сейчас. Новому — первая."""
    for w, lst in data["waves"].items():
        if any(t["id"] == tid for t in lst):
            try:
                return int(w)
            except ValueError:
                return 1
    return 1


def set_task(data: dict, tid: str, name: str, wave: "int | None",
             status: str, exit_code: "int | None" = None,
             requirements: "list | None" = None,
             zone: "list | None" = None,
             blocked_by: "list | None" = None,
             started: "str | None" = None,
             goal: "str | None" = None,
             acceptance: "list | None" = None,
             quotes: "list | None" = None,
             spec_sections: "list | None" = None,
             holdout: "list | None" = None) -> dict:
    """Записать задачу. «Готово» без доказательства записать НЕЛЬЗЯ.

    Единственное место, где решается, будет ли на экране сплошная полоса или
    контурная. Разрешив `proven` по слову, мы получили бы дашборд, на котором
    «агент сказал» неотличимо от «гейт вернул ноль», — то есть ровно тот отказ,
    против которого построена вся система.
    """
    if status not in TASK_STATES:
        raise ValueError(f"неизвестный статус задачи: {status}")
    if status == PROVEN and exit_code != 0:
        raise ValueError(
            "«доказано» требует кода возврата 0 от гейта; без него это «заявлено» "
            f"(получено: {exit_code!r})")
    entry = {"id": tid, "name": name, "status": status}
    if exit_code is not None:
        entry["exit_code"] = exit_code
    # Требования и зона живут НА ТАСКЕ, а не в отдельной таблице соответствий.
    # Отдельная таблица расходится с планом при первой же перенарезке, и
    # расходится молча: обе стороны выглядят правдоподобно. Здесь трассируемость
    # (гейт G3) и непересечение волн (расчёт экипажа) читаются из одного места.
    if requirements is not None:
        entry["requirements"] = list(requirements)
    if zone is not None:
        entry["zone"] = list(zone)
    # Блокеры живут ЗДЕСЬ же, а не в отдельном файле плана. Без них расчёт
    # волн не имеет исходных данных: crew.py видит таски без зависимостей,
    # кладёт все в первую волну и честно докладывает расхождение с
    # объявленными волнами плюс ложные пересечения зон. Найдено сквозным
    # прогоном — поодиночке оба инструмента работали.
    if blocked_by is not None:
        entry["blockedBy"] = list(blocked_by)
    # Отметка старта — единственный источник данных для обнаружения
    # последовательного полёта: товарищи по волне обязаны стартовать в
    # пределах секунд. Без неё механизм, ловящий отказ, который «изнутри
    # выглядит как правильная работа», не имеет что мерить и вечно отвечает
    # «не смог проверить». Часы подаются аргументом, иначе тест перестанет
    # давать один и тот же ответ завтра.
    if started is not None:
        entry["started"] = started
    else:
        prev = next((x for w in data["waves"].values() for x in w
                     if x["id"] == tid), None)
        if prev and prev.get("started"):
            entry["started"] = prev["started"]

    # Поля, без которых таск нельзя ПЕРЕДАТЬ. Их отсутствие здесь было
    # структурным разрывом: инструмент, создающий таски, не мог заполнить то,
    # что требует инструмент, их раздающий, — и передача честно отказывала
    # на каждом таске. Поодиночке оба работали; цепочка не складывалась.
    # `holdout` — проверки, которых исполнитель не увидит. Они живут рядом с
    # критериями и намеренно НЕ попадают в промпт: всё, что исполнитель видит,
    # находится внутри его оптимизационной петли, и имея критерий, он со
    # временем удовлетворит именно его. Часть проверок обязана остаться снаружи.
    for key, val in (("goal", goal), ("acceptance", acceptance),
                     ("quotes", quotes), ("spec_sections", spec_sections),
                     ("holdout", holdout)):
        if val is not None:
            entry[key] = list(val) if isinstance(val, (list, tuple)) else val

    keep = next((x for w in data["waves"].values() for x in w
                 if x["id"] == tid), None)
    if keep:
        # Перезапись таска не должна терять то, чего не передали в этот раз:
        # статус меняют чаще, чем критерии, и молчаливая потеря критериев
        # сделала бы передачу невозможной со второго вызова.
        for key in ("goal", "acceptance", "quotes", "spec_sections",
                    "requirements", "zone", "blockedBy", "started", "holdout"):
            if key not in entry and key in keep:
                entry[key] = keep[key]
    # Волна не передана — таск остаётся там, где лежит. Раньше умолчание было
    # «первая», и обновление существующего таска клало КОПИЮ в первую волну,
    # оставляя исходную запись на месте. Найдено сквозным прогоном: после
    # `task 02 --blocked-by 01` в файле оказались волна 1 с 01,02,03,04 и волна
    # 2 с 02,03,04, а `crew.py` доложил «02 и 02 делят территорию».
    #
    # Цена дефекта — не грязный файл: волна раздаётся субагентам по одному
    # вызову на таск, и два из них получили бы ОДИН таск, то есть писали бы в
    # одну зону одновременно. Ровно та потеря, которую ловит расчёт зон, — с
    # той разницей, что здесь её создаёт сам инструмент планирования.
    where = wave if wave is not None else _wave_of(data, tid)
    # Удаление из ВСЕХ волн до вставки: перенос обязан быть переносом, а не
    # копированием. Один и тот же id в двух волнах — не состояние, а мусор.
    # Позиция внутри волны запоминается: план читают глазами, и таск, уходящий
    # в конец списка при каждой правке статуса, перетасовывает страницу без
    # единой смысловой причины.
    at = None
    for w, lst in data["waves"].items():
        for i in range(len(lst) - 1, -1, -1):
            if lst[i]["id"] == tid:
                if w == str(where):
                    at = i
                del lst[i]
    target = data["waves"].setdefault(str(where), [])
    if at is None:
        target.append(entry)
    else:
        target.insert(at, entry)
    # Опустевшая волна удаляется: пустой ключ «2» читается как «волна есть, в
    # ней никого», и расчёт ярусов считал бы её за волну.
    for w in [k for k, v in data["waves"].items() if not v]:
        del data["waves"][w]
    return data


def set_stage(data: dict, name: str, status: str, detail: str = "") -> dict:
    if status not in STAGE_STATES:
        raise ValueError(f"неизвестный статус этапа: {status}")
    entry = {"name": name, "status": status, "detail": detail}
    for i, s in enumerate(data["stages"]):
        if s["name"] == name:
            data["stages"][i] = entry
            break
    else:
        data["stages"].append(entry)
    return data


def add_debt(data: dict, kind: str, text: str) -> dict:
    if kind not in DEBT_KINDS:
        raise ValueError(f"неизвестный вид долга: {kind}")
    if text not in data["debt"][kind]:
        data["debt"][kind].append(text)
    data["debt_reviewed"][kind] = True
    return data


def review_debt(data: dict, kind: str) -> dict:
    """Отметить: смотрели, закрывать нечего.

    Единственный способ отличить «проверено и чисто» от «никто не смотрел».
    Без явной отметки пустой список честнее показывать как непроверенный.
    """
    if kind not in DEBT_KINDS:
        raise ValueError(f"неизвестный вид долга: {kind}")
    data["debt_reviewed"][kind] = True
    return data


def summary(data: dict) -> dict:
    """Счётная картина — включая то, чего не хватает, чтобы её считать."""
    tasks = [t for w in data["waves"].values() for t in w]
    by = {s: sum(1 for t in tasks if t["status"] == s) for s in TASK_STATES}
    req = data["requirements"]
    debt = data["debt"]

    gaps = []
    if req["total"] is None or req["covered"] is None:
        gaps.append("покрытие требований не измерено")
    if not tasks:
        gaps.append("задач нет")
    unreviewed = [k for k in DEBT_KINDS if not data["debt_reviewed"].get(k)]
    for k in unreviewed:
        gaps.append(f"{DEBT_ASKS[k]}: никто не проверял")

    # Доля считается ТОЛЬКО от доказанного. Заявленное в прогресс не идёт:
    # иначе шкала растёт от слов, и человек видит движение там, где его нет.
    done = by[PROVEN]
    return {
        "tasks_total": len(tasks),
        "by_status": by,
        "progress": (round(100 * done / len(tasks)) if tasks else None),
        "progress_basis": "только доказанные задачи",
        "debt_total": sum(len(v) for v in debt.values()),
        "debt": {k: len(v) for k, v in debt.items()},
        "requirements": req,
        "debt_unreviewed": unreviewed,
        "unmeasured": gaps,
        "trustworthy": not gaps,
    }


def _fail(msg: str) -> int:
    print(f"НЕ УДАЛОСЬ: {msg}", file=sys.stderr)
    return 3


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def _flag(argv: list, name: str, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    if len(argv) < 2:
        print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
        return 3
    cmd, path = argv[0], Path(argv[1])
    rest = argv[2:]
    data = load(path)

    try:
        if cmd == "init":
            if not rest:
                return _fail("нужно название проекта")
            data = json.loads(json.dumps(EMPTY))
            data["project"] = rest[0]
        elif cmd == "stage":
            if len(rest) < 2:
                return _fail("нужны имя этапа и статус")
            data = set_stage(data, rest[0], rest[1], _flag(rest, "--detail", ""))
        elif cmd == "task":
            if len(rest) < 2:
                return _fail("нужны id и имя задачи")
            code = _flag(rest, "--exit-code")
            reqs = _flag(rest, "--requirements")
            zone = _flag(rest, "--zone")
            blk = _flag(rest, "--blocked-by")
            started = _flag(rest, "--started")
            goal = _flag(rest, "--goal")
            acc = _flag(rest, "--acceptance")
            quo = _flag(rest, "--quotes")
            sec = _flag(rest, "--spec-sections")
            hold = _flag(rest, "--holdout")
            # Переход в работу без явной отметки штампуется здесь: пропущенная
            # отметка тише неверной, но обходится дороже — она делает
            # проверку параллельности бессильной, ничего об этом не сказав.
            if started is None and _flag(rest, "--status") == RUNNING:
                from datetime import datetime, timezone
                started = datetime.now(timezone.utc).isoformat(timespec="seconds")
            # None, а не «1»: умолчание «первая волна» превращало обновление
            # существующего таска в его копию (см. set_task). Не передали
            # волну — таск остаётся там, где лежит.
            _w = _flag(rest, "--wave")
            data = set_task(data, rest[0], rest[1],
                            int(_w) if _w is not None else None,
                            _flag(rest, "--status", WAITING),
                            int(code) if code is not None else None,
                            [x.strip() for x in reqs.split(",") if x.strip()]
                            if reqs is not None else None,
                            [x.strip() for x in zone.split(",") if x.strip()]
                            if zone is not None else None,
                            [x.strip() for x in blk.split(",") if x.strip()]
                            if blk is not None else None,
                            started, goal,
                            # Критерии и цитаты режутся по «;», а не по запятой:
                            # запятая внутри критерия — обычное дело, и разбор
                            # по ней порезал бы одно условие на два бессмысленных.
                            [x.strip() for x in acc.split(";") if x.strip()]
                            if acc is not None else None,
                            [x.strip() for x in quo.split(";") if x.strip()]
                            if quo is not None else None,
                            [x.strip() for x in sec.split(";") if x.strip()]
                            if sec is not None else None,
                            [x.strip() for x in hold.split(";") if x.strip()]
                            if hold is not None else None)
        elif cmd == "debt":
            if not rest:
                return _fail("нужен вид долга")
            if len(rest) >= 2 and rest[1] == "--none":
                data = review_debt(data, rest[0])
            elif len(rest) < 2:
                return _fail("нужен текст долга либо --none")
            else:
                data = add_debt(data, rest[0], " ".join(rest[1:]))
        elif cmd == "source":
            if not rest:
                return _fail("нужен путь к источнику требований и задач")
            data["source"] = rest[0]
        elif cmd == "req":
            for k in ("total", "covered", "dropped", "deferred", "stubbed"):
                v = _flag(rest, f"--{k}")
                if v is not None:
                    data["requirements"][k] = int(v)
        elif cmd == "show":
            out = {**data, "summary": summary(data)}
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return 0 if out["summary"]["trustworthy"] else 2
        else:
            return _fail(f"неизвестная команда: {cmd}")
    except ValueError as e:
        return _fail(str(e))

    save(path, data)
    s = summary(data)
    print(json.dumps({**data, "summary": s}, ensure_ascii=False, indent=1))
    # Запись состояния — не место для вердикта о полноте: пока идёт стройка,
    # неполнота нормальна, и код 2 на каждой записи приучил бы его не читать.
    return 0


if __name__ == "__main__":
    sys.exit(main())
