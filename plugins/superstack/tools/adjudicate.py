#!/usr/bin/env python3
"""SUPERSTACK — решатель.

Факты + правила -> находки. Никакой модели: только вычисление выражений
над собранными фактами. Поэтому вывод воспроизводим и проверяем руками.

Выражения правил считает собственный интерпретатор по белому списку узлов.
Ни eval, ни compile, ни exec — правило физически не может выполнить код.

ЗАВИСИМОСТЕЙ НЕТ. Только стандартная библиотека — потому что на чистом маке
системный python3 (3.9, из Command Line Tools) не имеет PyYAML, и любая
внешняя зависимость означает ModuleNotFoundError на первом же шаге у того,
ради кого всё строится.

  python3 adjudicate.py facts.json 'rules/*.json' > findings.json
"""
from __future__ import annotations

import ast
import glob
import json
import operator
import sys
from pathlib import Path

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

#: потолок для умножения в выражениях правил — защита от исчерпания памяти
MAX_REPEAT = 10_000

_CMP = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
}


class RuleError(ValueError):
    """Правило содержит конструкцию вне разрешённой грамматики."""


def _walk(node, env: dict):
    """Мини-интерпретатор. Всё, что не описано здесь, — ошибка правила."""
    if isinstance(node, ast.Expression):
        return _walk(node.body, env)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        if node.id not in env:
            raise RuleError(f"неизвестный факт: {node.id}")
        return env[node.id]

    if isinstance(node, ast.Attribute):
        # Имя с точкой, не совпавшее ни с одним фактом, доходит сюда как
        # Attribute. Раньше диагностика обвиняла ГРАММАТИКУ правила, хотя
        # причина другая: проба не дала такой факт. Автор искал ошибку не там.
        parts, cur = [], node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            raise RuleError(f"неизвестный факт: {'.'.join(reversed(parts))}")
        raise RuleError("обращение к атрибуту в правилах запрещено")

    if isinstance(node, ast.BoolOp):
        vals = (_walk(v, env) for v in node.values)
        if isinstance(node.op, ast.And):
            return all(vals)
        if isinstance(node.op, ast.Or):
            return any(vals)
        raise RuleError("неподдерживаемый логический оператор")

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _walk(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -_walk(node.operand, env)
        raise RuleError("неподдерживаемый унарный оператор")

    if isinstance(node, ast.BinOp):
        fn = _BIN.get(type(node.op))
        if fn is None:
            raise RuleError("неподдерживаемая арифметика")
        значение = fn(_walk(node.left, env), _walk(node.right, env))
        # Статическая проверка ниже ловит ОДИН большой множитель. Цепочку из
        # нескольких допустимых она пропускает: 9999*9999 — сто мегабайт, и
        # каждый множитель в пределах. Предел ставится и на результат.
        if isinstance(значение, (str, bytes, list, tuple)) \
                and len(значение) > MAX_REPEAT:
            raise RuleError(f"результат длиной {len(значение)} превышает предел "
                            f"{MAX_REPEAT}: правило может исчерпать память")
        return значение

    if isinstance(node, ast.Compare):
        left = _walk(node.left, env)
        for op, right_node in zip(node.ops, node.comparators):
            fn = _CMP.get(type(op))
            if fn is None:
                raise RuleError("неподдерживаемое сравнение")
            right = _walk(right_node, env)
            if not fn(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id != "len":
            raise RuleError("в правилах разрешён только len()")
        if len(node.args) != 1 or node.keywords:
            raise RuleError("len() принимает ровно один аргумент")
        value = _walk(node.args[0], env)
        return len(value) if value is not None else 0

    raise RuleError(f"запрещённая конструкция: {type(node).__name__}")


def evaluate(expr: str, values: dict):
    """Вычислить выражение правила над плоскими фактами (ключи с точками)."""
    env = {"None": None, "True": True, "False": False}

    # Подстановка идёт ТОЛЬКО вне строковых литералов. Прежняя версия делала
    # replace по всему тексту выражения и переписывала содержимое кавычек:
    # правило `cc.default_mode == "cc.default_mode"` молча давало False.
    # Молча — потому что это не ошибка разбора, а подмена смысла.
    keys = [k for k in sorted(values, key=len, reverse=True) if k and k.strip()]
    token_of = {k: f"_f{i}" for i, k in enumerate(keys)}

    out, i, n = [], 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch in "\"'":
            # литерал переносится дословно, вместе с кавычками
            j = i + 1
            while j < n and expr[j] != ch:
                j += 2 if expr[j] == "\\" else 1
            out.append(expr[i:j + 1])
            i = j + 1
            continue
        for k in keys:
            if expr.startswith(k, i):
                out.append(token_of[k])
                env[token_of[k]] = values[k]
                i += len(k)
                break
        else:
            out.append(ch)
            i += 1
    safe = "".join(out)

    tree = ast.parse(safe, mode="eval")
    # M002: умножение строк не ограничено ничем, и правило приходит через git.
    # Кода оно не исполняет, но десяток гигабайт выделяет за долю секунды.
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and isinstance(side.value, int) \
                        and abs(side.value) > MAX_REPEAT:
                    raise RuleError(
                        f"множитель {side.value} превышает предел {MAX_REPEAT}: "
                        f"правило может исчерпать память")
    return _walk(tree, env)


def _human(val) -> str:
    """Значение факта в текст отчёта, а не repr питоновской структуры.

    Раньше в русскую фразу подставлялся str() от списка словарей —
    строка вида [{'agent': 'x', 'why': '...'}], которую человек читать
    не может, а в новичковом виде она занимала пол-экрана.
    """
    if isinstance(val, list):
        if not val:
            return "—"
        parts = []
        for item in val[:6]:
            if isinstance(item, dict):
                parts.append(str(item.get("agent") or item.get("id")
                                 or item.get("plugin") or item.get("scope")
                                 or item.get("location") or item))
            else:
                parts.append(str(item))
        tail = f" и ещё {len(val) - 6}" if len(val) > 6 else ""
        return ", ".join(parts) + tail
    if isinstance(val, dict):
        return ", ".join(f"{k}: {v}" for k, v in list(val.items())[:6])
    return str(val)


def substitute(text: str, values: dict, n: int | None) -> str:
    out = text.replace("{n}", str(n) if n is not None else "?")
    for key, val in values.items():
        out = out.replace("{" + key + "}", _human(val))
    if "{ratio}" in out:
        out = out.replace("{ratio}", str(values.get("inv.skills.over_budget_ratio", "?")))
    if "{places}" in out:
        # Раньше здесь подставлялся несуществующий ключ «index», и в отчёт
        # уезжало «в позиции [None]». Причём место всегда называлось
        # permissions.allow, где бы совпадение ни нашлось. Утверждение
        # называло файл и позицию, которых никто не мерил.
        hits = values.get("sec.secret_matches") or []
        places = [f"{h.get('file', '?')} -> {h.get('location', '?')}" for h in hits]
        out = out.replace("{places}", "; ".join(places) if places else "—")
    return out


def halt_if_paused() -> None:
    """Тормоз соблюдается, а не только попадает в отчёт.

    Раньше флаг паузы читался лишь как факт для отчёта: человек жал стоп,
    система записывала «paused: true» и продолжала работать. Теперь каждый
    инструмент проверяет флаг ПЕРВЫМ действием и выходит с кодом 10.
    """
    import os
    from pathlib import Path as _P
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    flag = _P.home() / ".claude" / "superstack" / "PAUSE"
    if flag.exists():
        try:
            since = flag.read_text(encoding="utf-8").strip()
        except Exception:
            since = "?"
        print(f"ОСТАНОВЛЕНО: система на паузе с {since}\n"
              f"  флаг: {flag}\n"
              f"  снять: tools/pause.sh off", file=__import__("sys").stderr)
        raise SystemExit(10)


def _build_finding(rule, rid, beg, exp, values, provenance, n, rf) -> dict:
    """Сборка находки. Вынесена, чтобы падение здесь не уносило весь прогон."""
    num = rule.get("number") or {}
    return {
            "id": rid,
            "severity": rule.get("severity", "low"),
            "class": rule.get("class", "INFORM"),
            "verdict": rule.get("verdict", "LEAVE"),
            "n": n,
            "unit": (num or {}).get("unit"),
            "headline": substitute(beg.get("headline", rid), values, n),
            "plain": substitute(beg.get("plain", ""), values, n),
            "why": substitute(beg.get("why", ""), values, n),
            "claim": substitute(exp.get("claim", ""), values, n),
            "evidence": {k: values.get(k) for k in exp.get("evidence", [])},
            "provenance": {k: provenance.get(k, "EXTRACTED")
                           for k in exp.get("evidence", [])},
            "rests_on_inference": any(
                provenance.get(k) == "INFERRED" for k in exp.get("evidence", [])),
            "note": exp.get("note", ""),
            "rule_file": rf,
    }


def _fail(msg: str, code: int = 2) -> None:
    """Сообщение вместо стектрейса. Стектрейс — это отчёт разработчику;
    человеку он говорит «сломалось» и не говорит, что делать."""
    print(msg, file=sys.stderr)
    raise SystemExit(code)



def _utf8_stdio() -> None:
    """Печать по-русски не должна зависеть от локали.

    В окружении без UTF-8 — минимальный контейнер, cron с урезанным env,
    `PYTHONCOERCECLOCALE=0` — кодировка вывода оказывается ascii, и первый же
    русский символ роняет инструмент целиком. Человек получает не «проверка не
    прошла», а трейсбек вместо любого ответа. На macOS по умолчанию это не
    воспроизводится: интерпретатор сам приводит локаль C к C.UTF-8.
    """
    for поток in (sys.stdout, sys.stderr):
        кодировка = (getattr(поток, "encoding", "") or "").lower().replace("-", "")
        if кодировка != "utf8" and hasattr(поток, "reconfigure"):
            поток.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _utf8_stdio()
    halt_if_paused()
    if len(sys.argv) < 3:
        _fail("нужен файл фактов и маска правил:\n"
              "  python3 adjudicate.py facts.json 'rules/*.json' > findings.json")
    src = Path(sys.argv[1])
    if not src.is_file():
        _fail(f"нет такого файла фактов: {src}\n"
              "  сначала: python3 probe/collect.py > facts.json")
    try:
        facts_raw = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        _fail(f"файл фактов не разбирается как JSON: {e}\n  файл: {src}")
    if not isinstance(facts_raw, dict):
        _fail(f"это не файл фактов: {src} — ожидался объект с ключами-фактами")
    # Факт без поля value роняет всё до первой находки. Кривой факт —
    # это дефект сборщика, но человек не обязан получать вместо отчёта
    # стектрейс: пропускаем и говорим, сколько пропустили.
    values, malformed = {}, []
    for k, v in facts_raw.items():
        # Пустой ключ превращает подстановку «{}» в замену пустой строки —
        # то есть в мусор по всему тексту правила, и все правила уходят в
        # skipped. Аудита нет, а баннер при этом честный.
        if not isinstance(k, str) or not k.strip():
            malformed.append(repr(k))
            continue
        if isinstance(v, dict) and "value" in v:
            values[k] = v["value"]
        else:
            malformed.append(k)
    # Класс достоверности каждого факта едет вместе с находкой: вывод
    # эвристики не должен выглядеть как измерение.
    provenance = {k: v.get("provenance", "EXTRACTED")
                  for k, v in facts_raw.items() if isinstance(v, dict)}

    # Дедупликация по РЕАЛЬНОМУ пути. Пересекающиеся маски (`rules/core.json`
    # и `rules/*.json`) читали один файл дважды: находки удваивались, а
    # rules_total врал в большую сторону — то есть «честность охвата»
    # искажалась тем же кодом, который её декларирует.
    seen_files: set = set()
    rule_files: list[str] = []
    for pattern in sys.argv[2:]:
        for f in sorted(glob.glob(pattern)):
            key = str(Path(f).resolve())
            if key in seen_files:
                continue
            seen_files.add(key)
            rule_files.append(f)

    # ГРОМКИЙ ОТКАЗ вместо тихого «всё чисто».
    # «Правил не найдено» и «нарушений не найдено» — разные утверждения,
    # а на выходе они выглядели одинаково: пустой список находок и код 0.
    # Именно так провал маскировался под успех.
    if not rule_files:
        print(f"ОТКАЗ: по маске {sys.argv[2:]} не найдено ни одного файла правил.\n"
              f"  текущий каталог: {Path.cwd()}\n"
              f"  Это НЕ «чистая машина» — это несработавшая проверка.\n"
              f"  Проверь путь запуска и маску (правила лежат в rules/*.json).",
              file=sys.stderr)
        sys.exit(3)

    findings, skipped, broken_files = [], [], []
    total_rules = 0
    for rf in rule_files:
        try:
            doc = json.loads(Path(rf).read_text(encoding="utf-8-sig"))
        except Exception as e:
            # Битый файл правил — тоже отказ, а не «правил нет».
            broken_files.append({"file": rf, "reason": str(e)})
            continue
        rules_here = doc.get("rules", [])
        total_rules += len(rules_here)
        for rule in rules_here:
            rid = rule.get("id", "?")
            try:
                fired = bool(evaluate(rule["when"], values))
            except Exception as e:
                skipped.append({"rule": rid, "reason": str(e)})
                continue
            if not fired:
                continue

            n = None
            num = rule.get("number")
            if num and "of" in num:
                target = values.get(num["of"])
                n = len(target) if isinstance(target, (list, dict)) else target

            beg, exp = rule.get("beginner", {}), rule.get("expert", {})
            try:
                finding = _build_finding(rule, rid, beg, exp, values, provenance, n, rf)
            except Exception as e:
                # Политика «правило упало — остальные продолжают» не действовала
                # на пути подстановки: одно кривое значение в фактах давало
                # ноль отчёта вместо частичного. Отчёт с дырой полезнее, чем
                # трейсбек вместо отчёта, — при условии, что дыра ВИДНА.
                skipped.append({"rule": rid, "reason": f"сборка находки: {e}"})
                continue
            findings.append(finding)

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["id"]))

    # Ошибки самих ПРОБ едут дальше вместе с находками. Иначе упавшая проба
    # неотличима от «всё чисто» — а это ровно то, что делает вердикт враньём.
    probe_errors = [{"probe": k, "reason": v["value"]}
                    for k, v in facts_raw.items() if k.startswith("error.")]

    # НЕПРОЧИТАННЫЕ СКОУПЫ. Проба отработала без ошибки, но не смогла заглянуть
    # в место, где искомое могло быть: битый файл настроек, включённый плагин
    # без каталога установки. Формально ошибки нет — фактически измерение
    # неполно, и «не нашёл» здесь не равно «там чисто». Поэтому это гасит
    # доверие к отчёту наравне с упавшей пробой.
    unmeasured: list[dict] = []
    for key, what in (("hooks.scopes_unreadable", "хуки"),
                      ("sec.scan_unreadable", "секреты"),
                      ("inv.skills.scopes_unmeasured", "скиллы"),
                      ("disc.agents_unreadable", "агенты")):
        for item in (values.get(key) or []):
            entry = dict(item) if isinstance(item, dict) else {"item": item}
            entry["measurement"] = what
            unmeasured.append(entry)

    # ЧЕСТНОСТЬ ОХВАТА. Читатель обязан видеть не только что найдено,
    # но и сколько проверок НЕ выполнилось.
    coverage = {
        "rules_total": total_rules,
        "rules_evaluated": total_rules - len(skipped),
        "rules_skipped": len(skipped),
        "files_broken": len(broken_files),
        "probe_errors": len(probe_errors),
        "scopes_unmeasured": len(unmeasured),
        "malformed_facts": len(malformed),
        "trustworthy": not (skipped or broken_files or probe_errors
                            or unmeasured or malformed),
    }

    json.dump({"findings": findings,
               "skipped_rules": skipped,
               "broken_rule_files": broken_files,
               "probe_errors": probe_errors,
               "unmeasured_scopes": unmeasured,
               "coverage": coverage,
               "rule_files": rule_files},
              sys.stdout, ensure_ascii=False, indent=2)

    if broken_files:
        print(f"ВНИМАНИЕ: битых файлов правил: {len(broken_files)} — "
              f"часть проверок не выполнена", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()
