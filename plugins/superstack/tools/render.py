#!/usr/bin/env python3
"""SUPERSTACK — рендер отчёта.

Одна структура данных (находка) — три глубины показа:

  beginner  строка: заголовок + одно предложение последствия
  expert    + машинная формулировка, значения фактов, оговорки
  why       + правило-источник и команда-проба, которую можно повторить руками

Новичковый вид показывается ВСЕМ по умолчанию. Опытный поднимает
детализацию одним словом. Ошибиться в сторону новичка стоит эксперту
одного нажатия; ошибиться в другую сторону стоит продукта.

  python3 render.py findings.json [beginner|expert|why] [rule-id]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MARK = {"critical": "!!", "high": "! ", "medium": "· ", "low": "  "}
ACTION = {
    "FIX": "починю", "ADD": "добавлю", "REPLACE": "заменю",
    "QUARANTINE": "уберу в карантин", "LEAVE": "оставлю как есть",
    "ASK": "спрошу тебя", "BLOCK": "остановлюсь до твоего решения",
}
BEGINNER_ROWS = 5


def line(width: int = 72) -> str:
    return "─" * width


def render_coverage(data: dict, depth: str) -> list[str]:
    """Сколько проверок НЕ выполнилось. Печатается ПЕРВЫМ, до находок.

    Без этого блока «я ничего не нашёл» и «я не смог проверить» выглядят
    одинаково — и вердикт становится уверенной ложью.
    """
    cov = data.get("coverage") or {}
    if cov.get("trustworthy", True):
        return []

    out = ["", "⚠ ЭТОТ ОТЧЁТ НЕПОЛНЫЙ — часть проверок не выполнилась:"]
    if cov.get("rules_skipped"):
        out.append(f"   не отработало правил: {cov['rules_skipped']} из {cov.get('rules_total','?')}")
    if cov.get("files_broken"):
        out.append(f"   битых файлов правил: {cov['files_broken']}")
    if cov.get("probe_errors"):
        out.append(f"   упавших проб: {cov['probe_errors']}")
    if cov.get("malformed_facts"):
        out.append(f"   фактов без значения: {cov['malformed_facts']}")
    if cov.get("scopes_unmeasured"):
        out.append(f"   мест, куда не удалось заглянуть: {cov['scopes_unmeasured']}")
    out.append("   Значит «ничего не нашёл» здесь НЕ означает «всё в порядке».")

    if depth != "beginner":
        for s in (data.get("skipped_rules") or [])[:10]:
            out.append(f"     · {s.get('rule')}: {str(s.get('reason'))[:80]}")
        for b in (data.get("broken_rule_files") or [])[:5]:
            out.append(f"     · файл {b.get('file')}: {str(b.get('reason'))[:70]}")
        for p in (data.get("probe_errors") or [])[:5]:
            out.append(f"     · проба {p.get('probe')}: {str(p.get('reason'))[:70]}")
        for u in (data.get("unmeasured_scopes") or [])[:8]:
            where = u.get("path") or u.get("plugin") or u.get("file") or u.get("scope")
            out.append(f"     · {u.get('measurement')}: не прочитано {where} "
                       f"({str(u.get('reason', 'причина не указана'))[:50]})")
    else:
        out.append("   Подробности: /more")
    out.append("")
    return out


def render_beginner(findings: list[dict], data: dict | None = None) -> str:
    out = [line(), "ЧТО Я НАШЁЛ НА ТВОЁМ КОМПЬЮТЕРЕ", line()]
    out += render_coverage(data or {}, "beginner")
    if not out[-1]:
        out = out[:-1]
    out.append("")
    if not findings:
        cov = (data or {}).get("coverage") or {}
        if cov.get("trustworthy", True):
            out += ["Ничего, что требовало бы вмешательства.",
                    f"Проверено правил: {cov.get('rules_evaluated', '?')}.", ""]
        else:
            out += ["Находок нет — но проверки выполнились не все (см. выше).",
                    "Это НЕ значит, что всё в порядке.", ""]
        return "\n".join(out)

    shown = findings[:BEGINNER_ROWS]
    for i, f in enumerate(shown, 1):
        out.append(f"{MARK.get(f['severity'], '  ')}{i}. {f['headline']}")
        if f["plain"]:
            out.append(f"      {f['plain']}")
        if f["why"]:
            out.append(f"      Зачем: {f['why']}")
        out.append(f"      Предлагаю: {ACTION.get(f['verdict'], f['verdict'])}")
        out.append("")

    hidden = len(findings) - len(shown)
    if hidden > 0:
        out.append(f"Ещё {hidden} — менее срочных. Показать все: /more")
        out.append("")
    out += ["Подробный разбор с доказательствами: /more", ""]
    return "\n".join(out)


def render_expert(findings: list[dict], data: dict | None = None) -> str:
    out = [line(), "РАЗБОР", line()]
    out += render_coverage(data or {}, "expert")
    out.append("")
    for f in findings:
        out.append(f"[{f['severity']}] [{f['class']}] {f['id']}")
        out.append(f"  {f['headline']}")
        if f["claim"]:
            out.append(f"  утверждение: {f['claim']}")
        if f["evidence"]:
            out.append("  факты:")
            for k, v in f["evidence"].items():
                text = json.dumps(v, ensure_ascii=False)
                if len(text) > 160:
                    text = text[:157] + "…"
                out.append(f"    {k} = {text}")
        if f["note"]:
            out.append(f"  оговорка: {f['note']}")
        out.append(f"  вердикт: {f['verdict']}   правило: {f['rule_file']}")
        out.append("")
    return "\n".join(out)


def render_why(findings: list[dict], rule_id: str, data: dict | None = None) -> str:
    """Самая глубокая проверка обязана быть и самой честной.

    Предупреждение о неполноте охвата стояло на новичковом и экспертном виде
    и отсутствовало здесь. То есть человек, который дошёл до разбора «почему»
    — именно тот, кто принимает решение, — единственный его не видел.
    """
    match = next((f for f in findings if f["id"] == rule_id), None)
    if not match:
        ids = ", ".join(f["id"] for f in findings)
        return f"Нет такой находки: {rule_id}\nЕсть: {ids}"
    out = [line(), f"ПОЧЕМУ: {rule_id}", line()]
    out += render_coverage(data or {}, "why")
    out += ["",
           f"Вывод:        {match['headline']}",
           f"Утверждение:  {match['claim']}",
           f"Правило:      {match['rule_file']}",
           f"Класс:        {match['class']}   вердикт: {match['verdict']}",
           "", "Буквальные значения фактов, на которых построен вывод:"]

    prov = match.get("provenance", {})
    inferred = []
    for k, v in match["evidence"].items():
        mark = prov.get(k, "EXTRACTED")
        tag = "" if mark == "EXTRACTED" else f"   [{mark}]"
        out.append(f"  {k} = {json.dumps(v, ensure_ascii=False)}{tag}")
        if mark != "EXTRACTED":
            inferred.append(k)

    if inferred:
        out += ["",
                "⚠ Часть этого — ВЫВОД эвристики, а не измерение:"]
        out += [f"  {k}" for k in inferred]
        out += ["  Значит вывод может быть неверным на нетипичной конфигурации.",
                "  Проверь сам, прежде чем действовать по нему."]
    if match["note"]:
        out += ["", f"Оговорка: {match['note']}"]
    out += ["", "Перепроверить руками:",
            "  python3 tools/probe/collect.py > facts.json",
            f"  python3 tools/adjudicate.py facts.json 'rules/*.json' | grep -A20 {rule_id}",
            ""]
    return "\n".join(out)


def halt_if_paused() -> None:
    """Тормоз обязан быть общим. Прежде его соблюдали collect, adjudicate,
    doctor и линтер — но НЕ этот инструмент, хотя докстринги трёх других
    утверждали «каждый инструмент проверяет флаг первым действием».
    Для learn.py это особенно тяжело: он единственный ПИШЕТ на диск."""
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
        import sys as _s
        print(f"ОСТАНОВЛЕНО: система на паузе с {since}\n"
              f"  флаг: {flag}\n  снять: tools/pause.sh off", file=_s.stderr)
        raise SystemExit(10)


def _fail(msg: str, code: int = 2) -> None:
    """Сообщение вместо стектрейса. Стектрейс — это отчёт разработчику;
    человеку он говорит «сломалось» и не говорит, что делать."""
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    halt_if_paused()
    if len(sys.argv) < 2:
        _fail("нужен файл с находками:\n"
              "  python3 render.py findings.json [beginner|expert|why] [rule-id]")
    src = Path(sys.argv[1])
    if not src.is_file():
        _fail(f"нет такого файла: {src}\n"
              "  сначала: python3 adjudicate.py facts.json 'rules/*.json' > findings.json")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        _fail(f"файл находок не разбирается как JSON: {e}\n  файл: {src}")
    if not isinstance(data, dict) or "findings" not in data:
        _fail(f"это не файл находок: в {src} нет поля findings")
    findings = data["findings"]
    depth = sys.argv[2] if len(sys.argv) > 2 else "beginner"

    if depth == "why":
        if len(sys.argv) < 4:
            print("нужен id находки: render.py findings.json why <rule-id>")
            return
        print(render_why(findings, sys.argv[3], data))
    elif depth == "expert":
        print(render_expert(findings, data))
    else:
        print(render_beginner(findings, data))


if __name__ == "__main__":
    main()
