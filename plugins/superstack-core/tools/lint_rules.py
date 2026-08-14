#!/usr/bin/env python3
"""SUPERSTACK — шлюз целостности правил.

Зачем. У авторства правил не было ни одного шлюза: опечатка в имени факта,
дублирующийся id, подстановка несуществующего значения и чужая схема
проходили молча. Молча — потому что неизвестное имя факта останавливает
вычисление ОДНОГО правила, оно уходит в skipped, и на выходе выглядит как
«нарушение не найдено». При двух десятках правил это заметно глазами;
при пяти сотнях это единственный реальный отказ роста.

Здесь правило проверяется ДО того, как его вердикт кому-то покажут:

  1. схема       — обязательные поля на месте, значения из допустимых наборов
  2. уникальность — id не повторяется ни внутри файла, ни между файлами
  3. грамматика  — «when» разбирается тем же интерпретатором, что и в бою
  4. имена фактов — каждое имя в «when», в evidence и в подстановках {…}
                    существует в каталоге фактов, который выдаёт collect.py
  5. тексты      — новичковый и экспертный слои непусты

Каталог фактов берётся ЖИВЫМ прогоном сборщика, а не списком в коде: список
в коде — это второй источник правды, и он расходится с первым молча.

  python3 lint_rules.py 'rules/*.json'          проверить
  python3 lint_rules.py 'rules/*.json' --facts f.json   на готовых фактах
"""
from __future__ import annotations

import ast
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

# --- поиск соседних пакетов -------------------------------------------------
# После разделения на семь плагинов соседние инструменты лежат в других
# каталогах. Пути берутся из МАНИФЕСТА этого плагина (поле dependencies), а не
# перечисляются здесь вторым списком: два списка расходятся молча, и расхождение
# всплывает импортом, упавшим у пользователя, а не у нас.
def _wire_siblings(here: Path) -> None:
    """Положить в sys.path каталоги tools соседних пакетов.

    Раньше соседи брались из поля `dependencies` собственного манифеста. Поле
    пришлось убрать: движок Claude Code 2.1.42 отвергает его как неизвестный
    ключ, и с ним НИ ОДИН пакет не проходил валидацию, то есть продукт не
    ставился вовсе. Как только поле исчезло, вместе с ним исчезла и проводка —
    инструменты перестали находить друг друга.

    Теперь соседи ищутся по раскладке, а не по манифесту. Два уровня, потому
    что установленный плагин лежит не так, как репозиторий: между корнем
    маркетплейса и корнем пакета появляется каталог версии
    (`cache/<маркетплейс>/<плагин>/<версия>/tools`).
    """
    plug_root = here.parent
    for base in (plug_root.parent, plug_root.parent.parent):
        if not base.is_dir():
            continue
        for pat in ("*/tools", "*/*/tools"):
            for d in sorted(base.glob(pat)):
                if d.is_dir() and d != here and str(d) not in sys.path:
                    sys.path.append(str(d))
_wire_siblings(Path(__file__).resolve().parent)


from adjudicate import RuleError, _walk, halt_if_paused  # noqa: E402

SEVERITY = {"critical", "high", "medium", "low"}
CLASS = {"AUTO", "GATE", "INFORM", "BLOCK"}
VERDICT = {"FIX", "ADD", "REPLACE", "QUARANTINE", "LEAVE", "ASK", "BLOCK"}
REQUIRED = ("id", "when", "severity", "class", "verdict")
# Подстановки, которые вычисляет рендер, а не берёт из фактов напрямую.
# Вычисляемые подстановки. Список обязан совпадать с тем, что реально умеет
# adjudicate.substitute: «indexes» лежал здесь и делал линтер слепым к
# отгруженной поломке — подстановка была в белом списке, а вычислять её
# было нечем.
COMPUTED_TOKENS = {"n", "ratio", "places"}

PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def fact_catalog(facts_file: str | None) -> set[str]:
    """Имена фактов, которые сборщик реально производит."""
    if facts_file:
        raw = json.loads(Path(facts_file).read_text(encoding="utf-8"))
    else:
        collect = Path(__file__).resolve().parent / "probe" / "collect.py"
        r = subprocess.run([sys.executable, str(collect)], capture_output=True,
                           text=True, timeout=180)
        if r.returncode != 0:
            raise SystemExit(f"сборщик фактов не отработал (код {r.returncode}):\n{r.stderr}")
        raw = json.loads(r.stdout)
    return set(raw.keys())


def names_in_expression(expr: str) -> set[str]:
    """Имена, встречающиеся в выражении правила.

    Разбирается тот же текст, что уйдёт в бой, но БЕЗ подмены точечных имён
    на токены: здесь нужны именно исходные имена, чтобы сверить их с каталогом.
    """
    out: set[str] = set()
    # Точечные имена не являются валидным Python, поэтому вынимаются регуляркой,
    # а синтаксис целиком проверяется отдельно, уже с подменой.
    for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_.]*", expr):
        token = m.group(0)
        if token in {"and", "or", "not", "None", "True", "False", "len", "in"}:
            continue
        out.add(token)
    return out


def check_grammar(expr: str, catalog: set[str]) -> str | None:
    """Разобрать выражение так же, как это сделает решатель."""
    safe = expr
    for i, key in enumerate(sorted(catalog, key=len, reverse=True)):
        if key in safe:
            safe = safe.replace(key, f"_f{i}")
    try:
        tree = ast.parse(safe, mode="eval")
    except SyntaxError as e:
        return f"не разбирается как выражение: {e}"
    env = {n.id: 0 for n in ast.walk(tree) if isinstance(n, ast.Name)}
    env.update({"None": None, "True": True, "False": False})
    try:
        _walk(tree, env)
    except RuleError as e:
        return f"запрещённая конструкция: {e}"
    except Exception:
        # Ошибка ТИПА на подставных нулях — не дефект правила: сравнение
        # числа со списком тут ничего не доказывает. Грамматика проверена.
        return None
    return None


def lint(rule_files: list[str], catalog: set[str]) -> list[dict]:
    problems: list[dict] = []
    seen: dict[str, str] = {}

    def bad(rule_id: str, file: str, what: str) -> None:
        problems.append({"rule": rule_id, "file": file, "problem": what})

    for rf in rule_files:
        try:
            doc = json.loads(Path(rf).read_text(encoding="utf-8-sig"))
        except Exception as e:
            bad("<файл>", rf, f"не разбирается как JSON: {e}")
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list):
            bad("<файл>", rf, "нет списка rules на верхнем уровне")
            continue

        for rule in doc["rules"]:
            rid = rule.get("id", "<без id>")
            for field in REQUIRED:
                if not rule.get(field):
                    bad(rid, rf, f"нет обязательного поля «{field}»")
            if rid in seen:
                bad(rid, rf, f"id уже занят в {seen[rid]}")
            else:
                seen[rid] = rf

            if rule.get("severity") not in SEVERITY:
                bad(rid, rf, f"severity вне набора: {rule.get('severity')!r}")
            if rule.get("class") not in CLASS:
                bad(rid, rf, f"class вне набора: {rule.get('class')!r}")
            if rule.get("verdict") not in VERDICT:
                bad(rid, rf, f"verdict вне набора: {rule.get('verdict')!r}")

            expr = rule.get("when") or ""
            if expr:
                err = check_grammar(expr, catalog)
                if err:
                    bad(rid, rf, f"«when»: {err}")
                for name in names_in_expression(expr) - catalog:
                    bad(rid, rf, f"«when» ссылается на несуществующий факт: {name}")

            for key in rule.get("expert", {}).get("evidence", []) or []:
                if key not in catalog:
                    bad(rid, rf, f"evidence ссылается на несуществующий факт: {key}")

            num = rule.get("number") or {}
            if num.get("of") and num["of"] not in catalog:
                bad(rid, rf, f"number.of ссылается на несуществующий факт: {num['of']}")

            for where in ("beginner", "expert"):
                block = rule.get(where) or {}
                for field, text in block.items():
                    if not isinstance(text, str):
                        continue
                    for token in PLACEHOLDER.findall(text):
                        if token in COMPUTED_TOKENS or token in catalog:
                            continue
                        bad(rid, rf,
                            f"{where}.{field}: подстановка {{{token}}} ни факт, ни вычисляемое")

            beg = rule.get("beginner") or {}
            if not beg.get("headline") or not beg.get("plain"):
                bad(rid, rf, "новичковый слой пуст — вердикт будет нечитаем")
            if not (rule.get("expert") or {}).get("claim"):
                bad(rid, rf, "нет машинной формулировки для экспертного слоя")

    return problems


def main() -> int:
    halt_if_paused()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    facts_file = None
    if "--facts" in sys.argv:
        facts_file = sys.argv[sys.argv.index("--facts") + 1]
        args = [a for a in args if a != facts_file]

    rule_files: list[str] = []
    for pattern in args or ["rules/*.json"]:
        rule_files.extend(sorted(glob.glob(pattern)))
    if not rule_files:
        print(f"ОТКАЗ: по маске {args} не найдено ни одного файла правил.",
              file=sys.stderr)
        return 3

    catalog = fact_catalog(facts_file)
    problems = lint(rule_files, catalog)

    if not problems:
        print(f"правила целы: файлов {len(rule_files)}, "
              f"фактов в каталоге {len(catalog)}")
        return 0

    print(f"НАЙДЕНО ПРОБЛЕМ В ПРАВИЛАХ: {len(problems)}", file=sys.stderr)
    for p in problems:
        print(f"  {p['rule']}  [{p['file']}]\n      {p['problem']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
