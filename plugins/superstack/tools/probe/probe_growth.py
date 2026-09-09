#!/usr/bin/env python3
"""SUPERSTACK — часть сборщика фактов: цикл улучшения и дисциплина.

Не самостоятельный инструмент, а кусок `probe/collect.py`, вынесенный, чтобы
ядро сборщика оставалось читаемым. Шов проведён так, чтобы не разрезать ни
одного якоря поломок, а не по красоте занятий.

Почему `probe_evolution` и `probe_discipline` лежат в одном файле: первая
зовёт `_has_content`, вторая — `_is_judge_role`, `_parse_tools`, `_model_tier`,
`_declares_tools`, `_declares_mcp`. Сегодня это держится на общей области
модуля; в одном файле связь остаётся внутренней и видимой глазом.

КАК ЭТО ГРУЗИТСЯ. Обычным `import` — никогда: ядро живёт в процессе
несколькими независимыми копиями (doctor.py, what.py, settings_copy.py,
checkpoint.py, семь загрузчиков в tests/), у каждой свой каталог настроек, и
ни одна не регистрируется в `sys.modules`. Общая запись склеила бы части
между копиями. Ядро грузит эту часть по пути тем же способом и поднимает её
имена к себе — см. `collect._подключить`.

ОТКУДА БЕРУТСЯ ИМЕНА ЯДРА. Два разных способа, и путать их нельзя:

  · `НУЖНО_ИЗ_ЯДРА` — впрыск при подключении. Годится для того, что снаружи
    не подменяют: функции, накопители, метки провенанса.
  · `ЯДРО.<имя>` — чтение глобала ядра В МОМЕНТ ОБРАЩЕНИЯ. Обязательно для
    имён, которые подменяют СНАРУЖИ присваиванием на модуле ядра (`CLAUDE` и
    соседи: doctor.py и около шестидесяти мест в tests/). Впрыск такого имени
    заморозил бы значение на момент загрузки, и подмена перестала бы доезжать
    МОЛЧА — с виду измеренным докладом про чужой профиль.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

#: Имена ядра, впрыскиваемые при подключении. Любое ненайденное — отказ при
#: импорте, а не `NameError` внутри пробы: `collect.main` глушит исключение
#: пробы в факт `error.*` и НЕ трогает код возврата, то есть потеря имени
#: прошла бы тихо, оставив отчёт без нескольких проверок.
НУЖНО_ИЗ_ЯДРА = ("put", "read_json", "skill_roots", "asset_roots",
                 "is_hook_wired", "wired_hooks_scoped",
                 "EXTRACTED", "INFERRED", "AMBIGUOUS")

#: Имена, которые ядро поднимает к себе после подключения. Здесь и пробы (их
#: зовёт `collect.main`), и внутренние разборщики: на них стоят тесты, а
#: `probe_discipline` и `probe_evolution` берут тесты и `doctor.py`.
ОТДАЁТ = ("probe_evolution", "probe_discipline", "probe_memory",
          "_is_judge_role", "_parse_tools", "_has_content", "_model_tier",
          "_declares_tools", "_declares_mcp")

#: Ставится ядром при подключении. До этого момента модуль неработоспособен —
#: и это правильно: часть без ядра не имеет смысла.
ЯДРО = None


# --------------------------------------------------------------------------
# самоулучшение
# --------------------------------------------------------------------------
def probe_evolution() -> None:
    """Замкнут ли цикл улучшения: наблюдение -> отбор -> потолок -> измерение."""
    manifest = read_json(ЯДРО.CLAUDE / "hooks" / "hooks.json") or {}
    wired_blob = wired_hooks_scoped()[0]  # все скоупы, а не один файл

    # 1. Наблюдение. Без него учиться не на чем.
    observe = [
        e.get("id", "")
        for entries in manifest.get("hooks", {}).values()
        for e in entries
        if "observe" in e.get("id", "") or "evaluate-session" in e.get("id", "")
    ]
    put("ev.observe_hooks_declared", observe, "collect.probe_evolution")
    put("ev.observe_hooks_wired",
        [h for h in observe if is_hook_wired(h, wired_blob)], "collect.probe_evolution")

    # 2. Отбор. Планка доказательности для записи в память.
    # Наличие ФАЙЛА не является наличием механизма. `touch promotion.yaml`
    # удовлетворял планку отбора, `touch budget.json` — потолок роста.
    # Это ровно тот дефект, который продукт называет у других «подсчёт файлов
    # доказывает наличие, а не работоспособность» — применённый к себе.
    put("ev.promotion_gate", _has_content(ЯДРО.CLAUDE / "superstack" / "promotion.yaml"),
        "collect.probe_evolution", "~/.claude/superstack/promotion.yaml (непустой)")

    # 3. Потолок. Без него улучшение превращается в накопление.
    put("ev.budget_ceiling", _has_content(ЯДРО.CLAUDE / "superstack" / "budget.json"),
        "collect.probe_evolution", "~/.claude/superstack/budget.json (непустой)")

    # 4. Измерение. Без него «стало лучше» — это ощущение.
    baseline = ЯДРО.CLAUDE / "superstack" / "baseline"
    put("ev.eval_baseline",
        len([f for f in baseline.glob("*.yaml") if _has_content(f)])
        if baseline.is_dir() else 0,
        "collect.probe_evolution", f"{baseline} (считаются только непустые)")

    # 5. Тесты самих скиллов. Считаются по тем же скоупам, что и сами скиллы:
    # иначе знаменатель и числитель меряются в разных местах, и доля врёт.
    tested = 0
    for _label, path in skill_roots()[0]:
        tested += sum(
            1 for d in path.iterdir()
            if d.is_dir() and ((d / "tests").is_dir() or (d / "test.yaml").is_file())
        )
    put("ev.skills_with_tests", tested, "collect.probe_evolution",
        provenance=INFERRED)  # по наличию папки tests или test.yaml




# --- разбор конфига агента: три места, где прежняя версия ошибалась ---------

# Роль — по ГРАНИЦЕ СЛОВА. «review» внутри «preview» больше не считается.
_JUDGE_WORD = re.compile(
    r"(?i)(?:^|[-_\s])(verifier|verify|reviewer|review|qa|checker|check|auditor|audit)(?:$|[-_\s])")


def _is_judge_role(name: str, frontmatter: str) -> bool:
    """Судья определяется по имени роли или явному полю, а не по подстроке."""
    if re.search(r"(?im)^role:\s*(verifier|reviewer|auditor|checker)\s*$", frontmatter):
        return True
    return bool(_JUDGE_WORD.search(name))


def _parse_tools(frontmatter: str) -> list[str]:
    """tools бывает строкой И YAML-списком. Прежняя версия видела только строку."""
    # [ \t]*, а НЕ \s* — \s включает перевод строки, и жадный квантор
    # съедал его, захватывая первый элемент списка как однострочное значение.
    m = re.search(r"^tools:[ \t]*(.*)$", frontmatter, re.M)
    if not m:
        return []
    inline = m.group(1).strip()
    if inline and not inline.startswith("#"):
        inline = inline.strip("[]")
        return [t.strip().strip("'\"") for t in inline.split(",") if t.strip()]
    # список: строки вида "  - Write" до следующего ключа
    tail = frontmatter[m.end():]
    out = []
    for ln in tail.splitlines():
        if re.match(r"^\s*-\s*", ln):
            out.append(re.sub(r"^\s*-\s*", "", ln).strip().strip("'\""))
        elif ln.strip() and not ln.startswith((" ", "\t")):
            break
    return out


def _has_content(path: Path, minimum: int = 8) -> bool:
    """Файл существует И что-то в нём есть. Пустой файл — не механизм."""
    try:
        return path.is_file() and path.stat().st_size >= minimum
    except Exception:
        return False


def _model_tier(frontmatter: str) -> str:
    """Тир по полю model, включая полные идентификаторы.

    `^model:\\s*(\\w+)` останавливался на первом дефисе: документированный
    способ записи «model: claude-opus-4-5-20250929» давал слово «claude»,
    которого нет среди тиров, и агент уходил в корзину «none». Раньше это
    просто занижало счёт. После того как «none» стали считать верхним тиром
    (агент без поля наследует модель сессии), ошибка стала опаснее:
    флот целиком на haiku с полными идентификаторами помечался бы как
    целиком на дорогой модели — то есть проверка врала в обе стороны.
    """
    m = re.search(r"^model:[ \t]*[\"\']?([A-Za-z0-9._-]+)", frontmatter, re.M)
    if not m:
        return "none"          # поля нет — наследуется модель сессии
    value = m.group(1).lower()
    for tier in ("opus", "sonnet", "haiku"):
        if tier in value:
            return tier
    if value in ("inherit", "inherited"):
        return "none"
    return "other"


def _declares_tools(frontmatter: str) -> bool:
    """Есть ли вообще поле tools.

    Разница принципиальная. Поле со списком — это ОГРАНИЧЕНИЕ: агенту дано
    только перечисленное. Отсутствие поля — это НАСЛЕДОВАНИЕ ВСЕГО, включая
    запись. Прежняя версия обе ситуации сводила к пустому списку и делала
    вывод «прав на запись нет». Получалось наоборот: самая разрешительная
    конфигурация судьи оценивалась как самая безопасная. Проверка,
    инвертирующая собственный принцип, вреднее отсутствующей — она выдаёт
    успокоительный вердикт.
    """
    return re.search(r"^tools:", frontmatter, re.M) is not None


def _declares_mcp(frontmatter: str, body: str) -> bool:
    """Заявление об использовании MCP — не любое упоминание слова.

    «Never use MCP servers here» — это запрет. Считать его заявлением
    значит обвинять агента в обратном тому, что он делает.
    """
    for line in (frontmatter + "\n" + body).splitlines():
        if not re.search(r"\bMCP\b|mcp__", line):
            continue
        if re.search(r"(?i)\b(never|not|no|without|avoid|don'?t|запрещ|не использ)\b", line):
            continue
        if re.search(r"(?i)\b(use|uses|using|via|through|call|invoke|через|использ)\b", line):
            return True
    return False


# --------------------------------------------------------------------------
# дисциплина: механизмы, которые можно проверить машиной
# --------------------------------------------------------------------------
def probe_discipline() -> None:
    """Проверки из индекса механизмов, поддающиеся автоматизации.

    Всё, что здесь есть, проверяется фактом на диске. Остальные механизмы
    дисциплины живут в references/discipline.md как проза — и это честно
    помечено: проза не принуждает.
    """
    # Verifier Theater. Роль верификатора ищется в КОНФИГЕ, а не в прозе.
    # «У меня есть qa-engineer» и «у меня есть проверка» — разные утверждения.
    #
    # Прежняя версия ошибалась В ОБЕ СТОРОНЫ одновременно:
    #   · «review» как подстрока «preview» помечала генератор судьёй;
    #   · настоящий судья с tools СПИСКОМ проходил чистым, потому что
    #     регулярка читала только однострочное значение;
    #   · фраза «Never use MCP servers here» — ЗАПРЕТ — засчитывалась как
    #     заявление, потому что MCP искался по всему тексту файла.
    agent_roots, plugin_problems = asset_roots("agents")
    verifiers, fake_verifiers = [], []
    unread: list[dict] = list(plugin_problems)
    for scope, adir in agent_roots:
        for f in sorted(adir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                # Молчаливый пропуск здесь означал бы «судей с правом записи
                # нет» там, где судью просто не смогли прочитать. Отчёт
                # сказал бы «охват полный» — то есть провал выглядел бы
                # как чистый результат.
                unread.append({"scope": scope, "path": str(f), "reason": str(e)})
                continue
            m = re.search(r"^---(.*?)^---", text, re.S | re.M)
            if not m:
                continue
            fm, body = m.group(1), text[m.end():]
            name = re.search(r"^name:\s*(\S+)", fm, re.M)
            name = name.group(1) if name else f.stem
            if not _is_judge_role(name, fm):
                continue
            verifiers.append(name)
            tools = _parse_tools(fm)
            reasons = []
            if not _declares_tools(fm):
                reasons.append("грант не задан — судья наследует ВСЕ инструменты, "
                               "включая запись")
            elif any(t in tools for t in ("Write", "Edit", "MultiEdit", "NotebookEdit")):
                reasons.append("судья с правом записи")
            if _declares_mcp(fm, body) and not any(t.startswith("mcp__") for t in tools):
                reasons.append("обязан использовать MCP, но его нет в гранте")
            if any("*" in t for t in tools if t.startswith("mcp__")):
                reasons.append("MCP объявлен маской — движок может её не поддержать")
            if reasons:
                fake_verifiers.append({"agent": name, "why": "; ".join(reasons)})
    put("disc.verifiers", verifiers, "collect.probe_discipline")
    put("disc.verifier_theater", fake_verifiers, "collect.probe_discipline",
        "роль и права читаются из frontmatter, а не из прозы",
        provenance=INFERRED)  # эвристика по имени роли и полю tools
    put("disc.agent_scopes_covered", [s for s, _ in agent_roots],
        "collect.probe_discipline")
    put("disc.agents_unreadable", unread, "collect.probe_discipline",
        "файлы агентов, которые не удалось прочитать — там НЕ проверяли")

    # Тиринг моделей. Все агенты на дорогой модели — тихая регрессия по цене.
    models = {"opus": 0, "sonnet": 0, "haiku": 0, "none": 0, "other": 0}
    for _scope, adir in agent_roots:
        for f in adir.glob("*.md"):
            try:
                head = f.read_text(encoding="utf-8", errors="ignore")[:600]
            except Exception:
                continue
            models[_model_tier(head)] += 1
    put("disc.model_tiers", models, "collect.probe_discipline")
    # Агент БЕЗ поля model наследует модель сессии — то есть верхний тир.
    # Прежняя версия складывала его в корзину «none» и исключала из счёта:
    # флот целиком без model давал total_tiered = 0, и проверка молчала
    # в самом частом и самом дорогом случае. Более дорогая конфигурация
    # оценивалась как более безопасная.
    total_tiered = models["opus"] + models["sonnet"] + models["haiku"] + models["none"]
    put("disc.all_on_top_tier",
        total_tiered > 2 and models["sonnet"] == 0 and models["haiku"] == 0,
        "collect.probe_discipline", "ни одного агента на дешёвом тире",
        provenance=INFERRED)

    # Kill-switch. Флаг глобальной паузы, который ставит человек одной командой
    # и который не требует, чтобы агент был жив.
    # Наличие тормоза определяется наличием САМОГО ИНСТРУМЕНТА, а не папки.
    # Раньше проверялась родительская директория — а её создаёт журнал находок,
    # то есть одна записанная находка навсегда гасила предупреждение.
    ks = ЯДРО.CLAUDE / "superstack" / "PAUSE"
    tool = Path(__file__).resolve().parent.parent / "pause.sh"
    put("disc.kill_switch_present", tool.is_file() and os.access(tool, os.X_OK),
        "collect.probe_discipline", str(tool))
    put("disc.paused", ks.exists(), "collect.probe_discipline", str(ks))

    # Видимое компаундирование: журнал находок существует и наполняется.
    learned = ЯДРО.CLAUDE / "superstack" / "learned"
    n = len(list(learned.glob("*.json"))) if learned.is_dir() else 0
    put("disc.learned_entries", n, "collect.probe_discipline", str(learned))


# --------------------------------------------------------------------------
# память
# --------------------------------------------------------------------------
def probe_memory() -> None:
    root = ЯДРО.CLAUDE / "projects"
    stores = []
    unreadable = 0
    if root.is_dir():
        for proj in root.iterdir():
            mem = proj / "memory"
            if not mem.is_dir():
                continue
            index = mem / "MEMORY.md"
            try:
                файлы = [f for f in mem.iterdir() if f.suffix == ".md"]
                размер = index.stat().st_size if index.is_file() else 0
            except OSError:
                # Одна нечитаемая память не имеет права уносить ВСЕ остальные:
                # тогда исчезают все `mem.*`, и «не нашёл проблем с памятью»
                # становится неотличимо от «не смотрел».
                unreadable += 1
                continue
            stores.append({
                "path": str(mem),
                "index_bytes": размер,
                # Индекс темой не считается, отсюда «-1». Без нижней границы
                # память без единого файла даёт «тем: -1» — число, которого не
                # бывает, напечатанное человеку с уверенным видом.
                "topic_files": max(0, len(файлы) - 1),
            })
    stores.sort(key=lambda s: -s["index_bytes"])
    put("mem.stores", stores, "collect.probe_memory",
        provenance=AMBIGUOUS if unreadable else EXTRACTED)
    put("mem.store_count", len(stores), "collect.probe_memory",
        provenance=AMBIGUOUS if unreadable else EXTRACTED)
    biggest = stores[0]["index_bytes"] if stores else 0
    put("mem.largest_index_bytes", biggest, "collect.probe_memory")
    # Индекс грузится с ограничением ~25 КБ / 200 строк.
    put("mem.index_over_limit", biggest > 25_600, "collect.probe_memory",
        "порог ~25 КБ на загрузку MEMORY.md",
        provenance=INFERRED)  # порог приблизительный
    put("mem.instincts_active", (ЯДРО.CLAUDE / "homunculus").is_dir(),
        "collect.probe_memory", "~/.claude/homunculus/")
    put("mem.cost_metrics", (ЯДРО.CLAUDE / "metrics").is_dir(),
        "collect.probe_memory", "~/.claude/metrics/")
