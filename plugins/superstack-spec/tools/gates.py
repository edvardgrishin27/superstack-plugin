#!/usr/bin/env python3
"""SUPERSTACK — четыре гейта над манифестом. Проходятся кодом, не словами.

Зачем это код, а не пункт в инструкции.

Конструкция гейтов взята у AutoPilot, где она описана прозой: «зайди в фазу,
заведи субагента, убедись, что нет строк open». Прозаический гейт проходится
утверждением — модель говорит «проверил», и ничто этого не опровергает. Ровно
эта дыра описана в нашем же плане про `/goal`: оценщик не вызывает инструменты
и удовлетворяется тем, что агент СКАЗАЛ.

Здесь каждый гейт — функция с кодом возврата, и каждый умеет три ответа, а не
два. «Не смог проверить» отделено от «прошло» намеренно: гейт, у которого
незапущенная проверка неотличима от чистой, работает ровно наоборот своему
назначению — он выдаёт неведение за порядок.

  G1  после брифинга   нерешённого без записанной причины не осталось
  G2  после спеки      ноль `open` — И независимая сверка покрытия ЗАПИСАНА
  G3  после плана      прямая и обратная трассируемость требование ↔ таск
  G4  на приёмке       слепая приёмка прошла, и манифест с ней НЕ РАСХОДИТСЯ

Про G2 отдельно. Половина гейта, которая работает, — вторая: спеку писал ты,
поэтому ты не видишь того, чего не написал. Требование, потерянное при
написании, помечается `in-spec` тем же прочтением, которое его потеряло.
Поэтому `coverage: null` — это ПРОВАЛ, а не пустой успех.

Про G4 отдельно. Расхождение слепой приёмки с манифестом — не провал прогона,
а его работа. Провал — оставить манифест утверждающим то, что независимая
проверка отрицает. Поэтому гейт красный, пока они не сойдутся: сойтись можно
и понизив строку до `placeholder`, но не умолчав.

  python3 gates.py <манифест.json> [--gate G1|G2|G3|G4] [--tasks состояние.json]

  код 0 — прошли, 1 — красное, 2 — не проверено, 3 — ошибка вызова
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_gates_manifest", HERE / "manifest.py")
mf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mf)

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

#: Вердикты слепой приёмки. Читаются от независимого проверяющего, который
#: видел бриф и репозиторий и НЕ видел ни спеки, ни манифеста.
BUILT, PARTIAL, ABSENT = "реализовано", "частично", "нет"


def _live(data: dict) -> list:
    return [r for r in data["requirements"] if r["status"] != mf.DROPPED]


def gate_g1(data: dict, tasks: dict = None) -> dict:
    """Ни одного требования, нерешённого МОЛЧА.

    Самый дешёвый из четырёх и единственный, который ловит недоспрошенное:
    строка, оставшаяся без причины после брифинга, — это вопрос, который никто
    не задал, и он всплывёт при сборке решением наугад.
    """
    if not data["requirements"]:
        return {"status": UNKNOWN, "detail": "требований нет — бриф не разобран"}
    # `open` САМ ПО СЕБЕ не провал, и это не послабление, а условие
    # выполнимости. Гейт стоит после брифинга, а раскладывает требования по
    # разделам спека — следующая фаза. Требуя ноль `open`, гейт нельзя было бы
    # пройти в том месте, где он стоит: пришлось бы писать спеку до гейта,
    # который её разрешает. Найдено сквозным прогоном — юнит-тесты его не
    # видели, потому что в них статусы расставлены руками.
    #
    # Провал — это `open` БЕЗ записанной причины: молчаливо нерешённое.
    # Причина отличает «спросили и подтвердили» от «не дошли руки», а без
    # неё эти два состояния выглядят одинаково.
    mute = [r["id"] for r in data["requirements"]
            if r["status"] == mf.OPEN
            and not ((r.get("reason") or "") + (r.get("basis") or "")).strip()]
    if mute:
        return {"status": FAIL,
                "detail": f"нерешённых без причины: {len(mute)} — "
                          + ", ".join(mute[:8]),
                "rows": mute}
    still = sum(1 for r in data["requirements"] if r["status"] == mf.OPEN)
    return {"status": PASS,
            "detail": f"решение есть у всех {len(data['requirements'])}"
                      + (f"; {still} ждут спеки с записанной причиной" if still else "")}


def gate_g2(data: dict, tasks: dict = None) -> dict:
    """Спека покрывает всё — и это подтвердил НЕ ТОТ, кто её писал.

    Две половины, и вторая — та, ради которой гейт существует. Первая
    проверяет твоё же прочтение твоей же спеки: требование, которое ты
    прочитал как покрытое, получит `in-spec` от того самого прочтения,
    которое его и потеряло.
    """
    mine = gate_g1(data)
    if mine["status"] != PASS:
        return {**mine, "half": "своя"}

    not_placed = [r["id"] for r in _live(data)
                  if r["status"] not in (mf.IN_SPEC, mf.IN_TICKET, mf.DONE,
                                         mf.PLACEHOLDER, mf.DEFERRED)]
    if not_placed:
        return {"status": FAIL, "half": "своя",
                "detail": "не разложено по спеке: " + ", ".join(not_placed[:8])}

    cov = data.get("coverage")
    if cov is None:
        return {"status": FAIL, "half": "независимая",
                "detail": "независимая сверка покрытия НЕ ЗАПУСКАЛАСЬ — "
                          "спеку читал только тот, кто её писал; "
                          "незапущенная проверка не то же самое, что чистая"}
    if not isinstance(cov, dict) or "found" not in cov:
        return {"status": UNKNOWN, "half": "независимая",
                "detail": "результат сверки не разобран — ожидается "
                          "{found, fixed, deferred}"}
    unresolved = cov.get("found", 0) - cov.get("fixed", 0) - cov.get("deferred", 0)
    if unresolved > 0:
        return {"status": FAIL, "half": "независимая",
                "detail": f"сверка нашла {cov['found']}, закрыто "
                          f"{cov.get('fixed', 0)}, отложено {cov.get('deferred', 0)} — "
                          f"{unresolved} висит без решения"}
    return {"status": PASS,
            "detail": f"{len(_live(data))} живых требований разложены; "
                      f"независимая сверка: найдено {cov['found']}, всё закрыто"}


def _task_rows(tasks: dict) -> list:
    if not tasks:
        return []
    return [t for w in (tasks.get("waves") or {}).values() for t in w]


def gate_g3(data: dict, tasks: dict = None) -> dict:
    """Трассируемость в ОБЕ стороны, и обратная важнее.

    Прямая ловит требование, которое никто не построит. Обратная ловит работу,
    которую никто не заказывал, — и это дороже: невостребованный таск съедает
    контекст, путает приёмку и не имеет владельца.
    """
    if tasks is None:
        return {"status": UNKNOWN,
                "detail": "состояние стройки не передано (--tasks) — "
                          "сверять требования не с чем"}
    rows = _task_rows(tasks)
    if not rows:
        return {"status": UNKNOWN, "detail": "тасков нет — план не нарезан"}

    need = [r["id"] for r in _live(data)
            if r["status"] in (mf.IN_SPEC, mf.IN_TICKET, mf.DONE, mf.PLACEHOLDER)]
    covered = {rid for t in rows for rid in (t.get("requirements") or [])}

    forward = [rid for rid in need if rid not in covered]
    backward = [t["id"] for t in rows if not (t.get("requirements") or [])]
    unknown_ids = sorted({rid for t in rows for rid in (t.get("requirements") or [])
                          if mf.find(data, rid) is None})

    bad = []
    if forward:
        bad.append(f"требований без таска: {', '.join(forward[:8])}")
    if backward:
        bad.append(f"тасков без требования: {', '.join(backward[:8])} — "
                   "это работа, которую никто не заказывал")
    if unknown_ids:
        bad.append(f"таски ссылаются на несуществующие требования: "
                   f"{', '.join(unknown_ids[:8])}")
    if bad:
        return {"status": FAIL, "detail": "; ".join(bad),
                "forward": forward, "backward": backward, "unknown": unknown_ids}
    return {"status": PASS,
            "detail": f"{len(need)} требований покрыты {len(rows)} тасками, "
                      "лишних тасков нет"}


def drift(data: dict) -> list:
    """Где манифест и слепая приёмка говорят разное.

    Считается ЗДЕСЬ, а не берётся из отчёта проверяющего: расхождение,
    которое надо не забыть переписать в отчёт, однажды не перепишут.
    """
    blind = data.get("blind") or {}
    seen = {c["id"]: c for c in (blind.get("checked") or []) if "id" in c}
    out = []
    for r in data["requirements"]:
        v = seen.get(r["id"])
        if v is None:
            continue
        verdict = v.get("verdict")
        if r["status"] == mf.DONE and verdict in (PARTIAL, ABSENT):
            out.append({"id": r["id"], "manifest": r["status"], "blind": verdict,
                        "why": "манифест утверждает готовность, независимая "
                               "проверка её не находит"})
        elif r["status"] in (mf.DROPPED, mf.DEFERRED) and verdict == BUILT:
            out.append({"id": r["id"], "manifest": r["status"], "blind": verdict,
                        "why": "построено то, что было снято или отложено"})
    for c in (blind.get("checked") or []):
        if c.get("verdict") == BUILT and mf.find(data, c.get("id", "")) is None:
            out.append({"id": c.get("id"), "manifest": "—", "blind": BUILT,
                        "why": "построено то, чего нет в манифесте"})
    return out


def gate_g4(data: dict, tasks: dict = None) -> dict:
    """Слепая приёмка прошла, и манифест с ней не расходится.

    Расхождение — не провал прогона, а его работа: ради этого приёмка и
    слепая. Провал — оставить манифест утверждающим то, что независимая
    проверка отрицает, потому что дальше по нему пишется отчёт человеку.

    Сойтись можно в обе стороны: починить сборку либо честно понизить строку.
    Чего нельзя — умолчать.
    """
    blind = data.get("blind")
    if blind is None:
        return {"status": FAIL,
                "detail": "слепая приёмка НЕ ЗАПУСКАЛАСЬ — всё до сих пор "
                          "сверялось со спекой, то есть с твоим же пересказом "
                          "просьбы"}
    if not isinstance(blind, dict) or not blind.get("checked"):
        return {"status": UNKNOWN,
                "detail": "результат приёмки не разобран — ожидается "
                          "{checked: [{id, verdict, where}]}"}
    d = drift(data)
    if d:
        return {"status": FAIL, "drift": d,
                "detail": f"манифест расходится со слепой приёмкой в {len(d)}: "
                          + "; ".join(f"{x['id']} — {x['why']}" for x in d[:4])}
    return {"status": PASS,
            "detail": f"приёмка сверила {len(blind['checked'])} требований, "
                      "расхождений с манифестом нет"}


GATES = [("G1", gate_g1), ("G2", gate_g2), ("G3", gate_g3), ("G4", gate_g4)]

#: Что делать, когда гейт красный. Диагноз без следующего шага заставляет
#: искать его заново каждый раз — а ищут его те, кто и так уже застрял.
NEXT = {
    "G1": "закрыть оставшиеся вопросы брифинга либо пометить строки placeholder",
    "G2": "запустить независимую сверку покрытия: субагенту дать ТОЛЬКО бриф и "
          "спеку — ни манифеста, ни разговора — и записать её итог в coverage",
    "G3": "дописать требования в таски либо срезать таск, не служащий ничему",
    "G4": "запустить слепую приёмку, затем свести манифест с её вердиктом — "
          "починив сборку или честно понизив строку",
}


def run(data: dict, base: Path, tasks: dict = None, only: str = None) -> dict:
    a = mf.audit(data, base)
    out = []
    for name, fn in GATES:
        if only and name != only:
            # Незапущенные ворота ОСТАЮТСЯ в отчёте: исчезнув, они превращают
            # частичный прогон в неотличимый от полного.
            out.append({"gate": name, "status": "skipped",
                        "detail": "одни ворота (--gate): не проверялось"})
            continue
        try:
            out.append({"gate": name, **fn(data, tasks)})
        except Exception as e:  # noqa: BLE001
            out.append({"gate": name, "status": UNKNOWN,
                        "detail": f"ворота не отработали: {e}"})
    red = [g for g in out if g["status"] == FAIL]
    grey = [g for g in out if g["status"] in (UNKNOWN, "skipped")]
    return {
        "gates": out,
        "manifest_broken": a["broken"],
        "manifest_unmeasured": a["unmeasured"],
        "passed": not red and not grey and not a["broken"] and not a["unmeasured"],
        "next": (NEXT.get(red[0]["gate"], "") if red
                 else (a["broken"] or a["unmeasured"] or [""])[0] if (a["broken"] or a["unmeasured"])
                 else ""),
    }


def human(v: dict) -> str:
    mark = {PASS: "прошло", FAIL: "КРАСНОЕ", UNKNOWN: "не проверено",
            "skipped": "пропущено"}
    lines = ["ГЕЙТЫ ВЗЯТЫ" if v["passed"] else "ГЕЙТЫ НЕ ВЗЯТЫ"]
    for g in v["gates"]:
        lines.append(f"  {g['gate']}  {mark.get(g['status'], g['status']):<13} {g['detail']}")
    for b in v["manifest_broken"]:
        lines.append(f"  ! манифест: {b}")
    for u in v["manifest_unmeasured"]:
        lines.append(f"  ? манифест: {u}")
    if v["next"]:
        lines.append(f"  дальше: {v['next']}")
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
    # Позиционные вычленяются С УЧЁТОМ того, что у флага бывает значение:
    # наивный отсев «всё, что не начинается с --» оставлял `G1` от `--gate G1`
    # позиционным, и инструмент отвечал подсказкой по вызову на каждый вызов
    # с флагом. Поймано первым же живым запуском, не рассуждением.
    TAKES_VALUE = {"--gate", "--tasks"}
    plain, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a in TAKES_VALUE:
            skip = True
        elif not a.startswith("--"):
            plain.append(a)
    if len(plain) != 1:
        print("вызов: gates.py <манифест.json> [--gate G1..G4] [--tasks состояние.json]",
              file=sys.stderr)
        return 3
    path = Path(plain[0])
    only = None
    if "--gate" in argv:
        i = argv.index("--gate")
        if i + 1 >= len(argv) or argv[i + 1] not in {n for n, _ in GATES}:
            print("НЕ УДАЛОСЬ: --gate ожидает G1, G2, G3 или G4", file=sys.stderr)
            return 3
        only = argv[i + 1]

    tasks = None
    if "--tasks" in argv:
        i = argv.index("--tasks")
        if i + 1 < len(argv):
            tp = Path(argv[i + 1])
            try:
                tasks = json.loads(tp.read_text("utf-8"))
            except (OSError, ValueError) as e:
                print(f"НЕ УДАЛОСЬ: состояние стройки не прочитано: {e}",
                      file=sys.stderr)
                return 3

    v = run(mf.load(path), path, tasks, only)
    if "--json" not in argv:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))

    if any(g["status"] == FAIL for g in v["gates"]) or v["manifest_broken"]:
        return 1
    if v["passed"]:
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
