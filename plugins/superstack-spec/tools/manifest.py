#!/usr/bin/env python3
"""SUPERSTACK — манифест требований: то, что просил человек, и что с этим стало.

Зачем отдельный инструмент, а не раздел спеки.

Спека — уже пересказ просьбы. Требование, потерянное при её написании, потеряно
навсегда и одинаково: и в спеке, и в коде, и в отчёте. Сверять их между собой
бесполезно — они сойдутся. Единственный способ поймать пропажу — держать рядом
СОБСТВЕННЫЕ СЛОВА человека, пронумерованные, со статусом у каждого.

Что здесь принуждается кодом, а не просьбой:

  1. ЦИТАТА ОБЯЗАНА НАХОДИТЬСЯ В БРИФЕ. «Дословно» — проверяемое утверждение,
     а не намерение: строка ищется в файле брифа, и требование без совпадения
     не записывается. Пересказ, выданный за цитату, — это и есть тот дрейф,
     против которого построен весь манифест.
  2. БРИФ НЕ РЕДАКТИРУЕТСЯ. Его отпечаток снимается при заведении и сверяется
     на каждой операции. Подправленный бриф — эталон, подогнанный под результат;
     дальше сверять не с чем.
  3. СНЯТЬ ТРЕБОВАНИЕ МОЖЕТ ТОЛЬКО ЧЕЛОВЕК, СВОЕЙ ЦИТАТОЙ. `dropped` без слов
     человека не записывается вовсе. Молчание отменой не считается: забыл и
     решил не должны выглядеть одинаково.
  4. УДАЛЕНИЯ СТРОК НЕТ. Команды «убрать требование» не существует — только
     смена статуса. Строка, которую можно стереть, ничего не держит.
  5. ДОБАВКА ЗНАЕТ СВОЕГО РОДИТЕЛЯ И ЗНАЕТ МЕРУ. `A##` без родительского
     требования не записывается; добавок не может быть больше, чем требований
     человека. Свободно висящая добавка — это другой проект.

  python3 manifest.py init <файл> <бриф.md>
  python3 manifest.py add  <файл> <id> --quote "..."   [--where X]
  python3 manifest.py add  <файл> <id> --implied "почему подразумевается"
  python3 manifest.py add  <файл> <id> --answer "..."             (G## из брифинга)
  python3 manifest.py add  <файл> <id> --addition "..." --parent R01
  python3 manifest.py add  <файл> <id> --discovered "что доказал код" --serves R01
  python3 manifest.py set  <файл> <id> <статус> [--where X] [--why "почему статус"]
  python3 manifest.py drop <файл> <id> --said "слова человека, отменяющие это"
  python3 manifest.py show <файл>
  python3 manifest.py md   <файл>

  код 0 — всё в порядке, 1 — манифест нарушен, 2 — не смог проверить, 3 — вызов
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sys
from pathlib import Path

#: Статусы. Порядок — это путь требования от просьбы до готового.
OPEN, IN_SPEC, IN_TICKET, DONE = "open", "in-spec", "in-ticket", "done"
PLACEHOLDER, DEFERRED, DROPPED = "placeholder", "deferred", "dropped"
STATUSES = (OPEN, IN_SPEC, IN_TICKET, DONE, PLACEHOLDER, DEFERRED, DROPPED)

#: Откуда взялось требование. Вид решает, что от него требуется при записи.
EXPLICIT = "explicit"      # из брифа — цитата обязана находиться в брифе
IMPLIED = "implied"        # подразумевается брифом — цитаты нет, нужно основание
ANSWER = "answer"          # из ответа на вопрос брифинга — слова человека
ADDITION = "addition"      # новая возможность — нужен родитель и мера
DISCOVERED = "discovered"  # сборка доказала — нужно основание и кому служит
KINDS = (EXPLICIT, IMPLIED, ANSWER, ADDITION, DISCOVERED)

EMPTY = {
    "schema": "superstack.manifest.v1",
    "brief": None,
    "brief_sha": None,
    "requirements": [],
    # Независимая сверка покрытия (G2) и слепая приёмка (G4). `null` значит
    # «не запускали», и это НЕ то же самое, что «расхождений нет»: гейт,
    # у которого пустота читается как успех, не гейт.
    "coverage": None,
    "blind": None,
    "updated": None,
}

_ID = re.compile(r"^(R\d{2}i?|G\d{2}|A\d{2}|D\d{2})$")

#: Типографика, различия которой не меняют смысла цитаты. Кавычки и тире
#: перенабираются по-разному в одном и том же тексте, и ловить на этом —
#: значит сделать проверку невыполнимой, а не строгой.
_FOLD = {
    "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'",
    "—": "-", "–": "-", "−": "-", " ": " ",
}


def _norm(s: str) -> str:
    for a, b in _FOLD.items():
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


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
    from datetime import datetime, timezone
    data["updated"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def find(data: dict, rid: str) -> "dict | None":
    return next((r for r in data["requirements"] if r["id"] == rid), None)


def brief_path(data: dict, base: Path) -> "Path | None":
    if not data.get("brief"):
        return None
    p = Path(data["brief"])
    return p if p.is_absolute() else (base.parent / p)


def brief_intact(data: dict, base: Path) -> "str | None":
    """Не изменился ли бриф. Возвращает описание беды либо None.

    Бриф — единственное в прогоне, что написал сам человек. Всё остальное —
    его пересказ. Отредактированный эталон подгоняется под результат, и
    сверять после этого не с чем: расхождение исчезает вместе с уликой.
    """
    p = brief_path(data, base)
    if p is None:
        return "бриф не задан — сверять цитаты не с чем"
    if not p.is_file():
        return f"бриф не найден: {p}"
    if data.get("brief_sha") and sha(p) != data["brief_sha"]:
        return (f"бриф изменился после заведения манифеста ({p}) — он эталон и "
                "не редактируется; поздние мысли дописываются отдельным файлом")
    return None


def quote_is_in_brief(quote: str, brief: str) -> "tuple[bool, str]":
    """Находится ли цитата в брифе. Второе значение — подсказка при промахе.

    «Дословно» здесь проверяемое утверждение, а не обещание. Пересказ,
    записанный как цитата, — это тот же дрейф, только заверенный: строка
    выглядит уликой, а уликой не является.

    При промахе возвращается ближайший кусок брифа. Строгость без подсказки
    выключают на второй день — не потому, что она неверна, а потому, что
    непонятно, что чинить.
    """
    nq, nb = _norm(quote), _norm(brief)
    if not nq:
        return False, "пустая цитата"
    if nq in nb:
        return True, ""
    parts = [p.strip() for p in re.split(r"(?<=[.!?\n])\s+|\n", brief) if p.strip()]
    near = difflib.get_close_matches(nq, [_norm(p) for p in parts], n=1, cutoff=0.5)
    return False, (f"ближайшее в брифе: «{near[0][:120]}»" if near
                   else "похожего в брифе нет вовсе")


def add(data: dict, rid: str, kind: str, *, quote: str = "", basis: str = "",
        parent: str = "", where: str = "", brief_text: str = "") -> dict:
    """Завести требование. Каждый вид проверяется по-своему."""
    if not _ID.match(rid):
        raise ValueError(f"неверный id: {rid} — ожидается R01, R06i, G02, A01 или D01")
    if find(data, rid):
        raise ValueError(f"{rid} уже есть — статус меняют через set, а не повторным add")
    if kind not in KINDS:
        raise ValueError(f"неизвестный вид: {kind}")

    if kind == EXPLICIT:
        if not quote:
            raise ValueError(f"{rid}: требование из брифа обязано нести цитату")
        ok, hint = quote_is_in_brief(quote, brief_text)
        if not ok:
            raise ValueError(
                f"{rid}: цитаты нет в брифе — записанное как «дословно» обязано "
                f"находиться в файле. {hint}")
    elif kind == ANSWER:
        if not quote:
            raise ValueError(f"{rid}: ответ брифинга записывается словами человека")
    elif kind == IMPLIED:
        if not basis:
            raise ValueError(f"{rid}: подразумеваемое требование обязано назвать, "
                             "из чего оно следует")
        if not rid.endswith("i"):
            raise ValueError(f"{rid}: подразумеваемое помечается хвостом i — R06i")
    elif kind == ADDITION:
        if not parent:
            raise ValueError(
                f"{rid}: добавка обязана назвать родительское требование — "
                "добавка без родителя это другой проект, а не углубление этого")
        if not find(data, parent):
            raise ValueError(f"{rid}: родителя {parent} нет в манифесте")
        r_g = sum(1 for x in data["requirements"] if x["kind"] in (EXPLICIT, ANSWER))
        a = sum(1 for x in data["requirements"] if x["kind"] == ADDITION)
        if a + 1 > r_g:
            raise ValueError(
                f"{rid}: добавок стало бы {a + 1} при {r_g} требованиях человека — "
                "мера нарушена; углублять заказанное можно без предела, "
                "добавлять своё — нет")
    elif kind == DISCOVERED:
        if not basis:
            raise ValueError(f"{rid}: находка сборки обязана назвать, что доказал код")
        if not parent:
            raise ValueError(f"{rid}: находка обязана назвать требование, которому служит")

    data["requirements"].append({
        "id": rid, "kind": kind, "quote": quote, "status": OPEN,
        "basis": basis, "reason": "", "parent": parent, "where": where,
        "said": ""})
    return data


def set_status(data: dict, rid: str, status: str, where: str = "",
               reason: str = "") -> dict:
    """Сменить статус. `dropped` этим путём НЕ ставится."""
    r = find(data, rid)
    if r is None:
        raise ValueError(f"нет требования {rid}")
    if status not in STATUSES:
        raise ValueError(f"неизвестный статус: {status}")
    if status == DROPPED:
        raise ValueError(
            "снять требование можно только командой drop со словами человека: "
            "статус «отменено», выставленный агентом, — это и есть тихая потеря "
            "требования, ради которой манифест не нужен")
    r["status"] = status
    if where:
        r["where"] = where
    # Причина СТАТУСА пишется отдельно от основания требования. Раньше она
    # затирала `basis` — и у подразумеваемой строки, у которой цитаты нет по
    # определению, стиралось то, чем она вообще является: в таблице для
    # человека вместо «требует, чтобы фотографии работ существовали»
    # оказывалось «подтверждено в брифинге». Требование теряло смысл, оставаясь
    # в списке.
    if reason:
        r["reason"] = reason
    return data


def drop(data: dict, rid: str, said: str) -> dict:
    """Снять требование. Только словами человека, и они сохраняются.

    Единственная дверь к `dropped`, и она узкая намеренно. Требование,
    которое агент может отменить сам, не защищено ничем: «мне показалось
    неважным» и «человек передумал» приводят к одной и той же строке.
    """
    r = find(data, rid)
    if r is None:
        raise ValueError(f"нет требования {rid}")
    if not said.strip():
        raise ValueError(f"{rid}: отмена требует слов человека — без цитаты "
                         "отмены не будет")
    r["status"] = DROPPED
    r["said"] = said.strip()
    return data


def audit(data: dict, base: Path) -> dict:
    """Что в манифесте нарушено прямо сейчас. Отдельно — чего не смогли проверить."""
    broken, unmeasured = [], []

    beef = brief_intact(data, base)
    if beef:
        unmeasured.append(beef)

    reqs = data["requirements"]
    if not reqs:
        unmeasured.append("требований нет — бриф не разобран")

    for r in reqs:
        if r["status"] == DROPPED and not r.get("said"):
            broken.append(f"{r['id']}: снято без слов человека")
        if r["kind"] == ADDITION and not r.get("parent"):
            broken.append(f"{r['id']}: добавка без родительского требования")
        if r["kind"] == EXPLICIT and not r.get("quote"):
            broken.append(f"{r['id']}: требование из брифа без цитаты")

    # Цитаты сверяются с брифом ЗАНОВО, а не один раз при записи: файл
    # манифеста правят руками, и проверка, работающая только на входе,
    # держит ровно до первого такого раза.
    p = brief_path(data, base)
    if p and p.is_file() and not beef:
        text = p.read_text("utf-8", errors="replace")
        for r in reqs:
            if r["kind"] == EXPLICIT and r.get("quote"):
                ok, hint = quote_is_in_brief(r["quote"], text)
                if not ok:
                    broken.append(f"{r['id']}: цитаты нет в брифе — {hint}")

    r_g = sum(1 for x in reqs if x["kind"] in (EXPLICIT, ANSWER))
    a = sum(1 for x in reqs if x["kind"] == ADDITION)
    if a > r_g:
        broken.append(f"добавок {a} при {r_g} требованиях человека — мера нарушена")

    return {"broken": broken, "unmeasured": unmeasured}


def counts(data: dict) -> dict:
    reqs = data["requirements"]
    by = {s: sum(1 for r in reqs if r["status"] == s) for s in STATUSES}
    live = [r for r in reqs if r["status"] not in (DROPPED, DEFERRED)]
    return {"total": len(reqs), "live": len(live), "by_status": by,
            "by_kind": {k: sum(1 for r in reqs if r["kind"] == k) for k in KINDS}}


def report(data: dict, base: Path) -> dict:
    a = audit(data, base)
    return {**data, "counts": counts(data), **a,
            "trustworthy": not a["broken"] and not a["unmeasured"]}


_KIND_RU = {EXPLICIT: "из брифа", IMPLIED: "подразумевается",
            ANSWER: "из брифинга", ADDITION: "добавлено сверх",
            DISCOVERED: "доказано сборкой"}


def to_md(data: dict) -> str:
    rows = ["# Манифест требований", "",
            f"Источник: `{data.get('brief') or '—'}`. "
            "Строку из этого списка может снять **только человек**.", "",
            "| ID | Из брифа (дословно) | Статус | Основание | Где |",
            "|----|---------------------|--------|-----------|-----|"]
    for r in data["requirements"]:
        what = f"«{r['quote']}»" if r["quote"] else f"*({_KIND_RU[r['kind']]})* {r['basis']}"
        # Основание требования и причина статуса — разные колонки. Слитые в
        # одну, они превращают строку в кашу, где непонятно, что это за
        # требование и почему оно в таком состоянии.
        why = r.get("reason") or "—"
        if r["status"] == DROPPED:
            why = f"человек: «{r['said']}»"
        rows.append(f"| {r['id']} | {what} | {r['status']} | {why} | {r['where'] or '—'} |")
    return "\n".join(rows) + "\n"


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
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return default


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
                return _fail("нужен путь к файлу брифа")
            b = Path(rest[0])
            if not b.is_file():
                return _fail(f"брифа нет: {b}")
            data = json.loads(json.dumps(EMPTY))
            data["brief"] = os.path.relpath(b.resolve(), path.resolve().parent)
            data["brief_sha"] = sha(b)
        elif cmd == "add":
            if not rest:
                return _fail("нужен id требования")
            rid = rest[0]
            bp = brief_path(data, path)
            btext = bp.read_text("utf-8", errors="replace") if bp and bp.is_file() else ""
            if (q := _flag(rest, "--quote")) is not None:
                data = add(data, rid, EXPLICIT, quote=q, brief_text=btext,
                           where=_flag(rest, "--where", ""))
            elif (b := _flag(rest, "--implied")) is not None:
                data = add(data, rid, IMPLIED, basis=b)
            elif (a := _flag(rest, "--answer")) is not None:
                data = add(data, rid, ANSWER, quote=a)
            elif (t := _flag(rest, "--addition")) is not None:
                data = add(data, rid, ADDITION, basis=t,
                           parent=_flag(rest, "--parent", ""))
            elif (d := _flag(rest, "--discovered")) is not None:
                data = add(data, rid, DISCOVERED, basis=d,
                           parent=_flag(rest, "--serves", ""))
            else:
                return _fail("нужен один из: --quote | --implied | --answer | "
                             "--addition | --discovered")
        elif cmd == "set":
            if len(rest) < 2:
                return _fail("нужны id и статус")
            data = set_status(data, rest[0], rest[1], _flag(rest, "--where", ""),
                              _flag(rest, "--why", _flag(rest, "--basis", "")))
        elif cmd == "drop":
            if not rest:
                return _fail("нужен id требования")
            said = _flag(rest, "--said")
            if said is None:
                return _fail("отмена требует --said со словами человека")
            data = drop(data, rest[0], said)
        elif cmd == "show":
            out = report(data, path)
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return 0 if out["trustworthy"] else (1 if out["broken"] else 2)
        elif cmd == "md":
            print(to_md(data))
            return 0
        else:
            return _fail(f"неизвестная команда: {cmd}")
    except ValueError as e:
        return _fail(str(e))

    save(path, data)
    out = report(data, path)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    # Код записи отвечает за ЗАПИСЬ, а не за состояние целиком.
    #
    # Раньше `init` на пустом манифесте возвращал 2 («не смог проверить»), а
    # `add` первой находки — 1, потому что общий вердикт был ещё не набран.
    # Конвейер с `set -e` умирал на первой же команде, а человек читал отказ
    # там, где всё записалось верно. «Операция удалась» и «состояние
    # достоверно» — разные вопросы, и смешивать их особенно вредно в
    # инструменте, который сам учит их различать.
    #
    # Нарушение (код 1) остаётся: запись, сделавшая состояние неверным, —
    # это провал записи. Неполнота (2) — нет: она нормальна по дороге.
    return 1 if out["broken"] else 0


if __name__ == "__main__":
    sys.exit(main())
