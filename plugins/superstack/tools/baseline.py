#!/usr/bin/env python3
"""SUPERSTACK — базовая линия. Замер конфигурации и сравнение замеров.

Зачем это отдельный инструмент, а не абзац в инструкции.

Самоулучшение без измерения — это накопление, и оно делает систему хуже.
Доказательство лежало на этой самой машине: 283 скилла и бюджет листинга,
превышенный в шесть раз. Каждая добавка по отдельности выглядела улучшением,
и ни одна не выглядела причиной. Без базовой линии нельзя ответить на
единственный вопрос, который вообще имеет смысл задавать после правки:
СТАЛО ЛУЧШЕ ИЛИ ХУЖЕ. А пока на него нельзя ответить, «улучшение» — это
утверждение того, кто правил, о собственной работе.

Поэтому здесь считает код, а не читатель.

Три решения, ради которых всё и написано:

  1. МЕТРИКИ НЕ ВЫДУМЫВАЮТСЯ. Всё берётся из уже существующих проб
     (`probe/collect.py`) и находок (`adjudicate.py`). Новый источник — это
     новая возможность ошибиться и новый повод не сойтись с остальным
     отчётом; замер обязан говорить теми же числами, что и отчёт.
  2. ВРЕМЯ НЕ БЕРЁТСЯ ИЗ ЧАСОВ ВНУТРИ СРАВНЕНИЯ. Метка снимка приходит
     СНАРУЖИ параметром. Сравнение двух снимков не трогает часы вообще —
     иначе один и тот же вход давал бы разный выход, а тест на таком коде
     проверяет не поведение, а погоду.
  3. «НЕ НАШЁЛ» И «НЕ СМОГ ИЗМЕРИТЬ» — РАЗНЫЕ УТВЕРЖДЕНИЯ. Метрика, которой
     нет в одном из снимков, попадает в `unmeasured`, гасит `trustworthy` и
     называется человеку. Она НЕ считается «ну значит не ухудшилось».

Правило отката формулируется словами прямо в выводе: метрика ухудшилась
после правки — правка откатывается, а не привыкается.

  python3 baseline.py snapshot --dir КАТАЛОГ --stamp МЕТКА \
      [--facts facts.json] [--findings findings.json] [--label текст] [--json]

  python3 baseline.py diff [--dir КАТАЛОГ] ДО ПОСЛЕ [--json]
      ДО и ПОСЛЕ — либо пути к файлам снимков, либо метки внутри --dir

  код 0 — не хуже, 1 — хуже, 2 — не смог сравнить, 3 — ошибка вызова
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "superstack.baseline.v1"
DIFF_SCHEMA = "superstack.baseline.diff.v1"

#: Направление метрики. WATCH — «смотрим, но не судим»: число, которое само
#: по себе не приговор. Такое направление существует, чтобы не выдавать
#: наблюдение за вердиктом: рост числа скиллов не является регрессией сам по
#: себе, регрессией является выросшая стоимость листинга.
LOWER = "lower_better"
HIGHER = "higher_better"
WATCH = "watch"

ROLLBACK_RULE = (
    "метрика ухудшилась после правки — правка ОТКАТЫВАЕТСЯ, а не привыкается: "
    "привыкание к ухудшению и есть тот механизм, которым 283 скилла копились "
    "по одному, и каждый выглядел улучшением"
)

#: Метка снимка идёт в ИМЯ ФАЙЛА. Без этой проверки `--stamp ../../settings`
#: превращает «снять замер» в запись произвольного файла вне каталога хранения.
STAMP_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._\-:+]{0,63}")


@dataclass(frozen=True)
class MetricSpec:
    """Одна метрика: откуда берётся и в какую сторону считается ухудшением."""
    key: str
    title: str
    unit: str
    direction: str
    source: str          # "facts" — ключ факта, "findings" — срез находок
    ref: str


#: Набор метрик. Порядок — порядок показа человеку.
METRICS = (
    # Стоимость листинга скиллов — та самая величина, которая на этой машине
    # ушла за бюджет в шесть раз. Она платится КАЖДЫМ запросом, поэтому стоит
    # первой.
    MetricSpec("skills.listing_chars", "стоимость листинга скиллов", "знаков",
               LOWER, "facts", "inv.skills.listing_chars"),
    MetricSpec("skills.over_budget_ratio", "превышение бюджета листинга", "раз",
               LOWER, "facts", "inv.skills.over_budget_ratio"),
    # Число скиллов — WATCH намеренно. Осмысленный скилл, добавленный взамен
    # трёх удалённых, поднимет счётчик и опустит стоимость; судить по счётчику
    # значило бы объявить регрессией правильную правку.
    MetricSpec("skills.count", "число скиллов", "штук",
               WATCH, "facts", "inv.skills.count"),
    # А вот скиллы, у которых есть собственные тесты, — единственное число
    # здесь, где рост означает улучшение: скилл без теста нельзя проверить,
    # и его «улучшение» никем не измеряется. Падение этого числа — регрессия.
    MetricSpec("skills.with_tests", "скиллов с тестами", "штук",
               HIGHER, "facts", "ev.skills_with_tests"),
    # Подключённые против объявленных. Судит РАЗРЫВ, а не каждая из сторон:
    # хук, объявленный в манифесте и не подключённый ни в одном скоупе, —
    # это механизм, которого нет, при документации, которая говорит, что он есть.
    MetricSpec("hooks.declared", "хуков объявлено", "штук",
               WATCH, "facts", "hooks.manifest.count"),
    MetricSpec("hooks.wired", "хуков подключено", "штук",
               WATCH, "facts", "hooks.wired.count"),
    MetricSpec("hooks.dormant", "объявлено, но не подключено", "штук",
               LOWER, "facts", "hooks.dormant.count"),
    # Индекс памяти читается целиком и тоже платится каждым запросом.
    MetricSpec("memory.index_bytes", "самый большой индекс памяти", "байт",
               LOWER, "facts", "mem.largest_index_bytes"),
    # Находки по тяжести — из решателя, теми же числами, что и в отчёте.
    MetricSpec("findings.critical", "находок critical", "штук",
               LOWER, "findings", "critical"),
    MetricSpec("findings.high", "находок high", "штук",
               LOWER, "findings", "high"),
    MetricSpec("findings.medium", "находок medium", "штук",
               LOWER, "findings", "medium"),
    MetricSpec("findings.low", "находок low", "штук",
               LOWER, "findings", "low"),
    MetricSpec("findings.total", "находок всего", "штук",
               LOWER, "findings", "total"),
)


class CallError(Exception):
    """Инструмент позвали неправильно. Код 3."""


class CannotCompare(Exception):
    """Сравнить нечем. Код 2 — и это НЕ «не хуже»."""


# --------------------------------------------------------------------------
# чтение источников
# --------------------------------------------------------------------------

def _read_json(path: Path, what: str) -> dict:
    """Прочитать источник. Битый файл — отказ вслух, а не пустой словарь.

    Тихая подстановка {} превратила бы «источник сломан» в «метрик нет»,
    а «метрик нет» — в «не ухудшилось». Ровно так провал маскируется под успех.
    """
    if not path.is_file():
        raise CallError(f"нет такого файла ({what}): {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise CallError(f"файл не разбирается как JSON ({what}): {path}\n  {e}")
    if not isinstance(doc, dict):
        raise CallError(f"это не {what}: {path} — ожидался объект")
    return doc


def _fact(facts: dict, key: str):
    """Значение факта. Возвращает (есть_ли, значение).

    Сборщик кладёт факт как {"value": …, "provenance": …}; развёрнутое число
    тоже принимается, чтобы замер можно было снять с урезанного среза фактов.
    """
    if not isinstance(facts, dict) or key not in facts:
        return False, None
    raw = facts[key]
    if isinstance(raw, dict) and "value" in raw:
        return True, raw["value"]
    return True, raw


def _number(value):
    """Число или None. bool отвергается намеренно: True прошёл бы как 1 и
    молча стал бы метрикой, которой никто не измерял."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _severity_counts(doc: dict):
    """Срез находок по тяжести. None, если это не файл находок."""
    items = doc.get("findings")
    if not isinstance(items, list):
        return None
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in items:
        sev = f.get("severity") if isinstance(f, dict) else None
        if sev in counts:
            counts[sev] += 1
    # total считается по длине списка, а не суммой известных тяжестей:
    # находка с незнакомой тяжестью обязана быть видна хотя бы в итоге.
    counts["total"] = len(items)
    return counts


# --------------------------------------------------------------------------
# снятие замера
# --------------------------------------------------------------------------

def build_metrics(facts, findings) -> tuple:
    """Собрать метрики из фактов и находок.

    Возвращает (метрики, непроверенное). Непроверенное — это не пустое место:
    каждая невзятая метрика названа вместе с причиной, потому что «пробы не
    было» и «проба показала ноль» лечатся по-разному.
    """
    metrics: dict = {}
    unmeasured: list = []
    sev = _severity_counts(findings) if findings is not None else None
    if findings is not None and sev is None:
        unmeasured.append({"metric": "findings.*",
                           "reason": "в файле находок нет списка findings"})

    for spec in METRICS:
        if spec.source == "facts":
            if facts is None:
                unmeasured.append({"metric": spec.key,
                                   "reason": "файл фактов не задан"})
                continue
            present, raw = _fact(facts, spec.ref)
            if not present:
                unmeasured.append({"metric": spec.key,
                                   "reason": f"в фактах нет ключа {spec.ref}"})
                continue
        else:
            if sev is None:
                unmeasured.append({"metric": spec.key,
                                   "reason": "файл находок не задан или не разобран"})
                continue
            raw = sev.get(spec.ref)

        num = _number(raw)
        if num is None:
            unmeasured.append({"metric": spec.key,
                               "reason": f"значение не число: {raw!r}"})
            continue
        metrics[spec.key] = {
            "value": num,
            "direction": spec.direction,
            "unit": spec.unit,
            "title": spec.title,
            "source": f"{spec.source}:{spec.ref}",
        }
    return metrics, unmeasured


def build_snapshot(stamp: str, facts, findings, label: str,
                   facts_name: str, findings_name: str) -> dict:
    """Замер целиком. Часы здесь НЕ читаются: метка пришла снаружи."""
    metrics, unmeasured = build_metrics(facts, findings)
    # Доверие к находкам приезжает из самого решателя: если часть правил не
    # вычислилась, число находок — это не «сколько их», а «сколько успели
    # посчитать». Сравнивать такие числа можно, но выдавать за чистый замер — нет.
    coverage = None
    if isinstance(findings, dict):
        cov = findings.get("coverage")
        if isinstance(cov, dict) and "trustworthy" in cov:
            coverage = bool(cov["trustworthy"])
    return {
        "schema": SCHEMA,
        "stamp": stamp,
        "label": label or "",
        # Кладём ИМЯ файла, а не путь: абсолютный путь машинно-зависим, и
        # снимок с ним перестал бы быть переносимым артефактом.
        "sources": {"facts": facts_name, "findings": findings_name},
        "coverage_trustworthy": coverage,
        "metrics": metrics,
        "unmeasured": unmeasured,
        "rollback_rule": ROLLBACK_RULE,
    }


def snapshot_path(store: Path, stamp: str) -> Path:
    if not STAMP_RE.fullmatch(stamp or ""):
        raise CallError(
            f"недопустимая метка снимка: {stamp!r}\n"
            "  метка идёт в имя файла: разрешены буквы, цифры и . _ - : +\n"
            "  так метка не может увести запись из каталога хранения")
    return store / f"{stamp}.json"


def write_atomic(path: Path, data: dict) -> None:
    """Запись через временный файл: оборванный снимок не должен подменять
    собой предыдущий и выглядеть базовой линией."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# сравнение
# --------------------------------------------------------------------------

def _check_snapshot(doc, side: str) -> dict:
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise CannotCompare(f"снимок «{side}» не той схемы: ожидалось {SCHEMA}")
    if not isinstance(doc.get("metrics"), dict):
        raise CannotCompare(f"в снимке «{side}» нет раздела metrics")
    return doc


def compare(before: dict, after: dict) -> dict:
    """Сравнить два снимка.

    ЧАСЫ ЗДЕСЬ НЕ ЧИТАЮТСЯ. Ни `time`, ни `datetime`: единственное время в
    выводе — метки, которые лежат в самих снимках. Иначе один и тот же вход
    давал бы разный выход, и тест на этой функции проверял бы не поведение.

    Направление берётся ИЗ СНИМКОВ, а не из таблицы этого файла: снимок —
    самодостаточный артефакт, его сравнивают спустя недели, когда таблица
    могла измениться. Разошедшееся направление — не повод молча выбрать одно
    из двух, а повод назвать метрику непроверенной.
    """
    b = _check_snapshot(before, "до")
    a = _check_snapshot(after, "после")
    bm, am = b["metrics"], a["metrics"]

    worse, better, watch, unmeasured = [], [], [], []
    unchanged = 0

    for key in sorted(set(bm) | set(am)):
        b_row, a_row = bm.get(key), am.get(key)
        if not isinstance(b_row, dict) or not isinstance(a_row, dict):
            missing = "до" if not isinstance(b_row, dict) else "после"
            unmeasured.append({"metric": key,
                               "reason": f"метрики нет в снимке «{missing}»"})
            continue
        direction = a_row.get("direction")
        if direction != b_row.get("direction"):
            unmeasured.append({"metric": key,
                               "reason": "направление метрики разошлось между снимками"})
            continue
        b_val, a_val = _number(b_row.get("value")), _number(a_row.get("value"))
        if b_val is None or a_val is None:
            unmeasured.append({"metric": key, "reason": "значение не число"})
            continue

        delta = a_val - b_val
        row = {"metric": key,
               "title": a_row.get("title", key),
               "unit": a_row.get("unit", ""),
               "direction": direction,
               "before": b_val, "after": a_val, "delta": delta}

        if direction == WATCH:
            # WATCH не судит НИКОГДА, даже при изменении: иначе наблюдение
            # тихо превратилось бы в вердикт.
            if delta:
                watch.append(row)
            else:
                unchanged += 1
            continue
        if direction not in (LOWER, HIGHER):
            unmeasured.append({"metric": key,
                               "reason": f"неизвестное направление: {direction!r}"})
            continue
        if delta == 0:
            unchanged += 1
            continue
        got_worse = (delta > 0) if direction == LOWER else (delta < 0)
        if got_worse:
            row["verdict"] = "ОТКАТИТЬ"
            worse.append(row)
        else:
            better.append(row)

    compared = len(worse) + len(better) + unchanged
    if compared == 0:
        # Отдельное состояние, а не «не хуже». Ноль сравнимых метрик означает,
        # что вопрос «стало лучше или хуже» остался без ответа, — и выдавать
        # это за зелёный значит врать ровно там, где инструмент и нужен.
        raise CannotCompare(
            "ни одной сравнимой метрики: снимки не пересекаются по измеренному")

    trust_reasons = []
    if unmeasured:
        trust_reasons.append(f"метрик не сравнено: {len(unmeasured)}")
    for side, doc in (("до", b), ("после", a)):
        if doc.get("coverage_trustworthy") is False:
            trust_reasons.append(
                f"снимок «{side}» снят при неполном охвате правил — "
                "числа находок означают «сколько успели посчитать»")
        for item in (doc.get("unmeasured") or []):
            trust_reasons.append(
                f"снимок «{side}»: {item.get('metric', '?')} — {item.get('reason', '?')}")

    status = "worse" if worse else ("better" if better else "same")
    return {
        "tool": "baseline",
        "action": "diff",
        "schema": DIFF_SCHEMA,
        "status": status,
        "before": {"stamp": b.get("stamp"), "label": b.get("label", "")},
        "after": {"stamp": a.get("stamp"), "label": a.get("label", "")},
        "compared": compared,
        "unchanged": unchanged,
        "worse": worse,
        "better": better,
        "watch": watch,
        "unmeasured": unmeasured,
        "trustworthy": not trust_reasons,
        "trust_reasons": trust_reasons,
        "rollback_rule": ROLLBACK_RULE,
        "next": ("откатить правку и снять замер заново" if worse
                 else "можно продолжать: по измеренному хуже не стало"),
    }


# --------------------------------------------------------------------------
# человеческий текст
# --------------------------------------------------------------------------

HEAD = {"worse": "СТАЛО ХУЖЕ", "better": "СТАЛО ЛУЧШЕ", "same": "БЕЗ ИЗМЕНЕНИЙ"}


def _fmt(row: dict) -> str:
    sign = "+" if row["delta"] > 0 else ""
    return (f"{row['title']}: {row['before']} -> {row['after']} "
            f"({sign}{row['delta']} {row['unit']})")


def human_diff(d: dict) -> str:
    lines = [f"{HEAD.get(d['status'], d['status'])}  "
             f"[{d['before']['stamp']} -> {d['after']['stamp']}]"]
    for row in d["worse"]:
        lines.append(f"  x {_fmt(row)}  -> {row['verdict']}")
    for row in d["better"]:
        lines.append(f"  + {_fmt(row)}")
    for row in d["watch"]:
        lines.append(f"  · {_fmt(row)}  (наблюдение, не вердикт)")
    for item in d["unmeasured"]:
        lines.append(f"  ? НЕ СРАВНЕНО {item['metric']}: {item['reason']}")
    lines.append(f"  сравнено метрик: {d['compared']}, без изменений: {d['unchanged']}")
    if not d["trustworthy"]:
        lines.append("  ВНИМАНИЕ: замер неполон — это не «чисто», это «не измерено»:")
        for r in d["trust_reasons"]:
            lines.append(f"    - {r}")
    lines.append(f"  ПРАВИЛО ОТКАТА: {d['rollback_rule']}")
    lines.append(f"  дальше: {d['next']}")
    return "\n".join(lines)


def human_snapshot(s: dict, path: Path) -> str:
    lines = [f"ЗАМЕР СНЯТ: {path}", f"  метка: {s['stamp']}"]
    for spec in METRICS:
        row = s["metrics"].get(spec.key)
        if row is None:
            continue
        mark = "·" if row["direction"] == WATCH else "-"
        lines.append(f"  {mark} {row['title']}: {row['value']} {row['unit']}")
    for item in s["unmeasured"]:
        lines.append(f"  ? НЕ ИЗМЕРЕНО {item['metric']}: {item['reason']}")
    if s.get("coverage_trustworthy") is False:
        lines.append("  ВНИМАНИЕ: находки сняты при неполном охвате правил")
    lines.append(f"  ПРАВИЛО ОТКАТА: {s['rollback_rule']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# команды
# --------------------------------------------------------------------------

def default_stamp() -> str:
    """Метка по умолчанию. Часы читаются ЗДЕСЬ и только здесь — в снятии
    замера, где «сейчас» и есть содержание метки. В сравнение это не попадает
    ни при каких обстоятельствах: там время приходит из самих снимков."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def cmd_snapshot(a) -> int:
    if not a.facts and not a.findings:
        raise CallError("нечего измерять: задай --facts и/или --findings\n"
                        "  сначала: python3 probe/collect.py > facts.json")
    store = Path(a.dir).expanduser()
    stamp = a.stamp or default_stamp()
    target = snapshot_path(store, stamp)
    if target.exists():
        # Перезапись базовой линии молча уничтожила бы ту самую точку отсчёта,
        # ради которой всё делается. Отказ вслух, а не «ничего страшного».
        raise CallError(f"снимок с такой меткой уже есть: {target}\n"
                        "  задай другую метку — базовая линия не перезаписывается")

    facts = _read_json(Path(a.facts).expanduser(), "факты") if a.facts else None
    findings = _read_json(Path(a.findings).expanduser(), "находки") if a.findings else None

    snap = build_snapshot(stamp, facts, findings, a.label or "",
                          Path(a.facts).name if a.facts else None,
                          Path(a.findings).name if a.findings else None)
    write_atomic(target, snap)

    out = dict(snap)
    out["path"] = str(target)
    if not a.json:
        print(human_snapshot(snap, target), file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    # Снимок без единой метрики — не замер, а пустой файл с меткой. Он не
    # годится как база сравнения, и код возврата обязан это сказать.
    return 0 if snap["metrics"] else 2


def resolve_snapshot(arg: str, store) -> Path:
    """Аргумент — либо путь к файлу, либо метка внутри каталога хранения."""
    p = Path(arg).expanduser()
    if p.is_file():
        return p
    if store is not None:
        try:
            cand = snapshot_path(Path(store).expanduser(), arg)
        except CallError:
            cand = None          # аргумент не годится в имя файла — значит это не метка
        if cand is not None and cand.is_file():
            return cand
    raise CallError(f"нет такого снимка: {arg}"
                    + (f" (искал и в {store})" if store else ""))


def cmd_diff(a) -> int:
    before_path = resolve_snapshot(a.before, a.dir)
    after_path = resolve_snapshot(a.after, a.dir)
    try:
        before = _read_json(before_path, "снимок «до»")
        after = _read_json(after_path, "снимок «после»")
    except CallError as e:
        # Битый снимок — это «не смог сравнить», а не «неверный вызов»:
        # позвали правильно, сравнить не получилось.
        raise CannotCompare(str(e))
    d = compare(before, after)
    d["before"]["path"] = str(before_path)
    d["after"]["path"] = str(after_path)
    if not a.json:
        print(human_diff(d), file=sys.stderr)
    print(json.dumps(d, ensure_ascii=False, indent=1))
    return 1 if d["status"] == "worse" else 0


def halt_if_paused() -> None:
    """Тормоз соблюдается, а не только попадает в отчёт: человек нажал стоп —
    инструмент не работает. Проверяется ПЕРВЫМ действием, как у соседей."""
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    flag = Path.home() / ".claude" / "superstack" / "PAUSE"
    if flag.exists():
        try:
            since = flag.read_text(encoding="utf-8").strip()
        except OSError:
            since = "?"
        print(f"ОСТАНОВЛЕНО: система на паузе с {since}\n"
              f"  флаг: {flag}\n  снять: tools/pause.sh off", file=sys.stderr)
        raise SystemExit(10)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="baseline.py",
        description="базовая линия конфигурации: снять замер и сравнить замеры")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="снять замер в каталог хранения")
    s.add_argument("--dir", required=True, help="каталог хранения замеров")
    s.add_argument("--facts", help="facts.json от probe/collect.py")
    s.add_argument("--findings", help="findings.json от adjudicate.py")
    s.add_argument("--stamp", help="метка снимка; передаётся СНАРУЖИ, "
                                   "по умолчанию — время UTC")
    s.add_argument("--label", help="чем этот замер отличается: что за правка")
    s.add_argument("--json", action="store_true", help="без человеческого текста")
    s.set_defaults(fn=cmd_snapshot)

    d = sub.add_parser("diff", help="сравнить два замера")
    d.add_argument("before", help="путь к снимку или метка внутри --dir")
    d.add_argument("after", help="путь к снимку или метка внутри --dir")
    d.add_argument("--dir", help="каталог хранения замеров")
    d.add_argument("--json", action="store_true", help="без человеческого текста")
    d.set_defaults(fn=cmd_diff)
    return p


def main(argv=None) -> int:
    halt_if_paused()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse выходит с кодом 2, а два в этом инструменте означает
        # «не смог сравнить». Ошибка вызова — это три, и путать их нельзя:
        # вызывающий скрипт по коду 2 полез бы разбираться с метриками.
        # --help выходит нулём и остаётся нулём: справка не является отказом.
        return 0 if e.code == 0 else 3
    try:
        return args.fn(args)
    except CallError as e:
        print(f"ОТКАЗ ВЫЗОВА: {e}", file=sys.stderr)
        return 3
    except CannotCompare as e:
        print(f"НЕ СМОГ СРАВНИТЬ: {e}\n"
              "  это НЕ «не хуже» — это отсутствие ответа на вопрос",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
