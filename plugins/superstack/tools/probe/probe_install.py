#!/usr/bin/env python3
"""SUPERSTACK — часть сборщика фактов: что установлено и как подключено.

Не самостоятельный инструмент, а кусок `probe/collect.py`, вынесенный, чтобы
ядро сборщика оставалось читаемым. Шов проведён так, чтобы не разрезать ни
одного якоря поломок, а не по красоте занятий.

Здесь всё, что отвечает на вопрос «что хост видит и откуда»: включённые
плагины и каталоги их установки, скоупы скиллов и ассетов, инвентарь, хуки
(объявлено против подключено), области MCP и признаки проекта.

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
    имён, которые подменяют СНАРУЖИ присваиванием на модуле ядра (`CLAUDE`,
    `sh`, `global_config_path` и соседи: doctor.py и около шестидесяти мест в
    tests/). Впрыск такого имени заморозил бы значение на момент загрузки, и
    подмена перестала бы доезжать МОЛЧА — с виду измеренным докладом про
    чужой профиль.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

#: Имена ядра, впрыскиваемые при подключении. Любое ненайденное — отказ при
#: импорте, а не `NameError` внутри пробы: `collect.main` глушит исключение
#: пробы в факт `error.*` и НЕ трогает код возврата, то есть потеря имени
#: прошла бы тихо, оставив отчёт без нескольких проверок.
#:
#: `SKILL_READ_ERRORS` впрыскивается ССЫЛКОЙ и только мутируется (`.append`,
#: чтение). Объявление остаётся в ядре: снаружи его чистят через
#: `collect.SKILL_READ_ERRORS.clear()`, и вторая копия списка сделала бы эту
#: чистку бесполезной молча.
НУЖНО_ИЗ_ЯДРА = ("put", "read_json", "project_dir", "plugin_decisions",
                 "read_settings_scoped", "SKILL_READ_ERRORS",
                 "SKILL_LISTING_BUDGET_CHARS", "INFERRED", "AMBIGUOUS")

#: Имена, которые ядро поднимает к себе после подключения. Пробы зовёт
#: `collect.main`; `skill_roots`, `asset_roots`, `is_hook_wired` и
#: `wired_hooks_scoped` просят соседние части и `doctor.py`; остальное держат
#: тесты. Подъём — присваиванием глобала ядра, НЕ функцией-обёрткой: обёртка
#: сохранила бы `def <имя>` в ядре и доказывала бы воротам «план» заглушку.
ОТДАЁТ = ("enabled_plugins", "skill_roots", "asset_roots", "probe_host",
          "_skill_listing_cost", "probe_inventory", "_untrusted",
          "is_hook_wired", "wired_hooks_scoped", "probe_hooks",
          "_mcp_top", "_mcp_local", "mcp_scopes", "probe_mcp", "probe_project")

#: Ставится ядром при подключении. До этого момента модуль неработоспособен —
#: и это правильно: часть без ядра не имеет смысла.
ЯДРО = None


def enabled_plugins() -> tuple[list[dict], list[dict]]:
    """Включённые плагины и каталоги их установки.

    Включённость собирается по ВСЕМ файлам настроек и по приоритету хоста:
    решает самая сильная область, назвавшая плагин, — см. plugin_decisions.
    Путь берётся ТОЛЬКО из installed_plugins.json — это единственный
    авторитетный источник. Каталог plugins/marketplaces/ содержит весь каталог
    маркетплейса целиком, а не то, что установлено: счёт по нему завысил бы
    число скиллов в разы. Ошибка «померил не там» опаснее, чем «не померил» —
    она даёт число, которому верят.
    """
    installed = (read_json(ЯДРО.CLAUDE / "plugins" / "installed_plugins.json") or {})
    table = installed.get("plugins", {}) if isinstance(installed, dict) else {}

    active: list[dict] = []
    problems: list[dict] = []
    for решение in plugin_decisions()[0]:
        if not решение["enabled"]:
            continue
        key = решение["plugin"]
        entries = table.get(key) or []
        if not entries:
            problems.append({"plugin": key, "reason": "включён, но не установлен"})
            continue
        path = Path(entries[0].get("installPath", ""))
        if not path.is_dir():
            problems.append({"plugin": key, "reason": "каталог установки отсутствует",
                             "path": str(path)})
            continue
        active.append({"plugin": key, "path": path})
    return active, problems


def skill_roots() -> tuple[list[tuple[str, Path]], list[dict]]:
    """Все каталоги, откуда грузятся скиллы: пользователь, плагины, проект."""
    roots: list[tuple[str, Path]] = [("user", ЯДРО.CLAUDE / "skills")]
    active, problems = enabled_plugins()
    for item in active:
        roots.append((f"plugin:{item['plugin']}", item["path"] / "skills"))
    roots.append(("project", project_dir() / ".claude" / "skills"))
    return [(label, p) for label, p in roots if p.is_dir()], problems


def asset_roots(kind: str) -> tuple[list[tuple[str, Path]], list[dict]]:
    """Каталоги, откуда грузятся агенты или команды: те же скоупы, что у скиллов.

    Скиллы и хуки уже мерились по всем скоупам, а агенты и команды — по одному
    каталогу. Класс дефекта тот же: судья из ВКЛЮЧЁННОГО плагина был невидим,
    и число «проверяющих с правом записи» подавалось как полное. Хуже того,
    агент без поля tools имеет полные права по собственному критерию системы —
    то есть невидимым оказывался самый опасный случай.
    """
    roots: list[tuple[str, Path]] = [("user", ЯДРО.CLAUDE / kind)]
    active, problems = enabled_plugins()
    for item in active:
        roots.append((f"plugin:{item['plugin']}", item["path"] / kind))
    roots.append(("project", project_dir() / ".claude" / kind))
    return [(label, p) for label, p in roots if p.is_dir()], problems


# --------------------------------------------------------------------------
# host
# --------------------------------------------------------------------------
def probe_host() -> None:
    put("host.os", sys.platform, "collect.probe_host")
    for tool in ("git", "gh", "node", "python3"):
        path = ЯДРО.sh(["which", tool])
        # Наличие пути и работоспособность — разные вещи. Сломанный git
        # (битая установка, отсутствующие CLT) лежит по своему пути и
        # отвечает ошибкой на любую команду; факт «git есть» тогда врёт.
        works = bool(path) and ЯДРО.sh([tool, "--version"]) is not None
        put(f"host.{tool}", works, "collect.probe_host",
            f"{path} (проверено вызовом --version)" if works else path)


# --------------------------------------------------------------------------
# инвентарь
# --------------------------------------------------------------------------
def _skill_listing_cost(skills_dir: Path) -> tuple[int, int, list]:
    """Стоимость листинга = сумма len(name)+len(description) по всем скиллам."""
    total, count, biggest = 0, 0, []
    if not skills_dir.is_dir():
        return 0, 0, []
    for d in sorted(skills_dir.iterdir()):
        f = d / "SKILL.md"
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            SKILL_READ_ERRORS.append({"path": str(f), "reason": str(e)})
            continue
        m = re.search(r"^---(.*?)^---", text, re.S | re.M)
        fm = m.group(1) if m else ""
        dm = re.search(r"^description:\s*(.*?)(?=^\w[\w-]*:|\Z)", fm, re.S | re.M)
        desc = " ".join((dm.group(1) if dm else "").split())
        cost = len(d.name) + len(desc) + 4
        total += cost
        count += 1
        biggest.append((cost, d.name))
    biggest.sort(reverse=True)
    return total, count, biggest[:10]


def probe_inventory() -> None:
    roots, plugin_problems = skill_roots()
    cost = count = 0
    biggest: list = []
    by_scope: list[dict] = []
    for label, path in roots:
        c, n, big = _skill_listing_cost(path)
        cost += c
        count += n
        biggest.extend(big)
        by_scope.append({"scope": label, "skills": n, "chars": c, "path": str(path)})
    biggest.sort(key=lambda x: x[0], reverse=True)
    biggest = biggest[:10]

    # Охват измерения едет вместе с числом. Иначе «283 скилла» неотличимо от
    # «283 скилла из тех каталогов, куда я догадался заглянуть».
    put("inv.skills.by_scope", by_scope, "collect.probe_inventory",
        "разбивка по каталогам, откуда грузятся скиллы")
    put("inv.skills.scopes_covered", [s["scope"] for s in by_scope],
        "collect.probe_inventory")
    put("inv.skills.scopes_unmeasured", plugin_problems + list(SKILL_READ_ERRORS),
        "collect.probe_inventory",
        "включённые плагины без каталога и файлы скиллов, которые не прочитались")

    put("inv.skills.count", count, "collect.probe_inventory",
        "сумма по всем скоупам: пользователь + включённые плагины + проект")
    put("inv.skills.listing_chars", cost, "collect.probe_inventory",
        "сумма name+description по всем скоупам",
        provenance=INFERRED)  # оценка стоимости листинга, не замер рантайма
    put("inv.skills.budget_chars", SKILL_LISTING_BUDGET_CHARS,
        "collect.probe_inventory", "консервативная оценка; точную даёт /doctor",
        provenance=INFERRED)  # НЕ измеренное значение бюджета
    put("inv.skills.over_budget_ratio",
        round(cost / SKILL_LISTING_BUDGET_CHARS, 2) if cost else 0,
        "collect.probe_inventory", "делится на ОЦЕНКУ бюджета",
        provenance=INFERRED)  # оценка / оценка — вдвойне не измерение
    put("inv.skills.biggest", [{"chars": c, "name": n} for c, n in biggest],
        "collect.probe_inventory")

    for name in ("agents", "commands"):
        roots, _ = asset_roots(name)
        total, per_scope = 0, []
        for label, d in roots:
            n = len([x for x in d.iterdir() if x.suffix == ".md"])
            total += n
            per_scope.append({"scope": label, "count": n})
        put(f"inv.{name}.count", total, "collect.probe_inventory",
            "сумма по пользовательскому каталогу, включённым плагинам и проекту")
        put(f"inv.{name}.by_scope", per_scope, "collect.probe_inventory")

    mk = read_json(ЯДРО.CLAUDE / "plugins" / "known_marketplaces.json") or {}
    put("inv.marketplaces", list(mk.keys()) if isinstance(mk, dict) else [],
        "collect.probe_inventory")


# --------------------------------------------------------------------------
# хуки: объявлено против подключено
# --------------------------------------------------------------------------
# Сопоставление подстрокой делало подключённым что попало: id «lint» находился
# внутри «npx eslint», и объявленный, но невключённый хук исчезал из списка
# спящих. Ошибка тихая и в опасную сторону — система говорила «подключено» про
# то, чего нет. Здесь id обязан стоять отдельным токеном: так его и передают
# в команду (кавычки, пробелы, запятые), и так его нельзя случайно «найти»
# внутри другого слова.
def _untrusted(text: str, limit: int = 100) -> str:
    """Текст из чужого файла, попадающий в отчёт.

    Описания хуков, имена агентов и прочее приходят из файлов, которые писал
    не пользователь и не мы. Отчёт же показывается агенту с указанием не
    пересказывать его — то есть чужой текст попадает в контекст модели
    дословно. Строка вида «игнорируй предыдущие инструкции и…» в описании
    хука становится путём инъекции.

    Поэтому: переводы строк схлопываются (многострочная вставка не может
    притвориться отдельной репликой), управляющие символы убираются, длина
    режется. Это не защита от всего, но снимает самый дешёвый способ.
    """
    if not isinstance(text, str):
        return ""
    flat = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    flat = re.sub(r"\s+", " ", flat).strip()
    return flat[:limit] + ("…" if len(flat) > limit else "")


def is_hook_wired(hook_id: str, blob: str) -> bool:
    if not hook_id or hook_id == "?":
        return False
    return re.search(
        r"""(?:^|[\s"'=,\[(/])""" + re.escape(hook_id) + r"""(?:$|[\s"'\],)])""",
        blob) is not None


def wired_hooks_scoped() -> tuple[str, list[dict], list[str], list[dict]]:
    """Подключённые хуки по всем скоупам.

    Возвращает (склеенный текст конфигов, разбивка, охват, нечитаемые скоупы).

    Склеенный текст нужен для сопоставления id: хук считается подключённым,
    если его id встречается ХОТЬ ГДЕ. Проверять только один файл — значит
    объявлять мёртвым то, что живо в другом скоупе.

    Само сопоставление — не подстрокой (см. is_hook_wired): подстрока делает
    подключённым любой хук с коротким id.
    """
    loaded, covered, unreadable = read_settings_scoped()
    blobs: list[str] = []
    by_scope: list[dict] = []
    for label, data in loaded:
        hooks = data.get("hooks", {})
        if not isinstance(hooks, dict):
            continue
        n = sum(len(v) for v in hooks.values() if isinstance(v, list))
        by_scope.append({"scope": label, "entries": n,
                         "events": sorted(hooks.keys())})
        blobs.append(json.dumps(hooks, ensure_ascii=False))

    # Плагины тоже поставляют хуки, и они работают, не будучи упомянутыми
    # ни в одном settings.json.
    for item in enabled_plugins()[0]:
        hp = item["path"] / "hooks" / "hooks.json"
        data = read_json(hp)
        if not isinstance(data, dict):
            continue
        hooks = data.get("hooks", {})
        if not isinstance(hooks, dict):
            continue
        n = sum(len(v) for v in hooks.values() if isinstance(v, list))
        if n:
            by_scope.append({"scope": f"plugin:{item['plugin']}", "entries": n,
                             "events": sorted(hooks.keys())})
            blobs.append(json.dumps(hooks, ensure_ascii=False))
    return "\n".join(blobs), by_scope, covered, unreadable


def probe_hooks() -> None:
    blob, by_scope, covered, unreadable = wired_hooks_scoped()
    total = sum(s["entries"] for s in by_scope)
    events = sorted({e for s in by_scope for e in s["events"]})

    put("hooks.wired.count", total, "collect.probe_hooks",
        "сумма по всем файлам настроек и хукам включённых плагинов")
    put("hooks.wired.events", events, "collect.probe_hooks")
    put("hooks.wired.by_scope", by_scope, "collect.probe_hooks")
    put("hooks.scopes_covered", covered, "collect.probe_hooks")
    put("hooks.scopes_unreadable", unreadable, "collect.probe_hooks",
        "файл настроек существует, но не разобран — скоуп НЕ измерен")

    manifest_path = ЯДРО.CLAUDE / "hooks" / "hooks.json"
    manifest = read_json(manifest_path)
    # Путь публикуется ВСЕГДА, а не только когда файл нашёлся: правило
    # ссылается на этот факт, а факт, существующий не на каждой машине,
    # выключает правило именно там, где оно нужнее.
    put("hooks.manifest.path", str(manifest_path), "collect.probe_hooks")
    if not manifest:
        put("hooks.manifest.count", 0, "collect.probe_hooks")
        # Провенанс обязан совпадать с основной веткой: на чистой машине
        # шёл именно этот путь, и вывод помечался как ИЗМЕРЕНИЕ.
        put("hooks.dormant", [], "collect.probe_hooks",
            "манифеста нет — спящих нет по определению", provenance=INFERRED)
        put("hooks.dormant.count", 0, "collect.probe_hooks", provenance=INFERRED)
        return

    hooks = manifest.get("hooks", {})
    declared = [
        {"id": _untrusted(e.get("id", "?"), 80), "event": _untrusted(ev, 40),
         "description": _untrusted(e.get("description") or "")}
        for ev, entries in hooks.items()
        for e in entries
    ]
    put("hooks.manifest.count", len(declared), "collect.probe_hooks")

    dormant = [d for d in declared if not is_hook_wired(d["id"], blob)]
    # Нечитаемый скоуп означает, что «спящий» может быть подключён именно там.
    # Тогда это не находка, а незавершённое измерение — и оно помечается.
    put("hooks.dormant", dormant, "collect.probe_hooks",
        "объявлены в hooks.json, но их id не встречается ни в одном скоупе",
        provenance=AMBIGUOUS if unreadable else INFERRED)
    put("hooks.dormant.count", len(dormant), "collect.probe_hooks",
        provenance=AMBIGUOUS if unreadable else INFERRED)


# --------------------------------------------------------------------------
# mcp
# --------------------------------------------------------------------------
def _mcp_top(данные: dict, _проект: Path) -> dict:
    """Верхний `mcpServers` файла — пользовательская область и файл команды."""
    блок = данные.get("mcpServers")
    return блок if isinstance(блок, dict) else {}


def _mcp_local(данные: dict, проект: Path) -> dict:
    """Область ЭТОГО проекта внутри общего конфига.

    Ключ — абсолютный путь проекта, каким его записал хост. Один каталог
    бывает достижим под двумя именами (символическая ссылка: `/tmp` и
    `/private/tmp` на маке — один и тот же каталог), и запись найдётся только
    под одним из них. Проверяются оба: иначе «область пуста» было бы сказано
    про область, в которую не заглянули.
    """
    проекты = данные.get("projects")
    if not isinstance(проекты, dict):
        return {}
    имена = []
    try:
        имена.append(str(проект.resolve()))
    except OSError:
        pass
    имена.append(str(проект))
    for имя in имена:
        узел = проекты.get(имя)
        if isinstance(узел, dict):
            return _mcp_top(узел, проект)
    return {}


def mcp_scopes() -> tuple[list[tuple[str, Path, dict]], list[str], list[dict]]:
    """Все области, откуда хост берёт MCP-серверы.

    Читался ОДИН верхний `mcpServers` общего конфига. Сервер, добавленный для
    этого проекта, и сервер из `.mcp.json` команды в список не попадали —
    а MCP-сервер это произвольное исполнение кода, и молчать о нём нельзя.
    Недосчёт тут опаснее пересчёта: «сервер один» при трёх настроенных
    читается как измерение и закрывает вопрос.

    То же правило, что и у файлов настроек, и по той же причине. Отсутствующий
    файл — не проблема: область просто не используется. Существующий и
    неразобранный — НЕПРОЧИТАННАЯ область, и она называется поимённо: «не
    прочитал» обязано отличаться от «там пусто».

    Возвращает (тройки область-файл-серверы, прочитанные области,
    непрочитанные). Файл едет вместе с областью, а не подразумевается: две
    области живут в ОДНОМ файле, и без его имени человек не может проверить
    ни одну из них — а проверяемость и есть всё, что этот доклад продаёт.
    """
    проект = project_dir()
    общий = ЯДРО.global_config_path()
    источники = [
        ("user", общий, _mcp_top),
        ("local", общий, _mcp_local),
        ("project", проект / ".mcp.json", _mcp_top),
    ]
    loaded: list[tuple[str, Path, dict]] = []
    covered: list[str] = []
    unreadable: list[dict] = []
    for метка, путь, достать in источники:
        if not путь.is_file():
            continue
        данные = read_json(путь)
        if not isinstance(данные, dict):
            unreadable.append({"scope": метка, "path": str(путь),
                               "reason": "файл области не разобран как объект"})
            continue
        covered.append(метка)
        loaded.append((метка, путь, достать(данные, проект)))
    return loaded, covered, unreadable


def probe_mcp() -> None:
    scoped, covered, unreadable = mcp_scopes()
    servers: dict[str, dict] = {}
    by_scope: list[dict] = []
    for метка, путь, блок in scoped:
        by_scope.append({"scope": метка, "path": str(путь),
                         "servers": sorted(блок)})
        for name, srv in блок.items():
            servers.setdefault(name, srv)
    put("mcp.declared", sorted(servers), "collect.probe_mcp",
        "объединение всех прочитанных областей")
    put("mcp.by_scope", by_scope, "collect.probe_mcp",
        "какой сервер из какой области и из какого файла")
    put("mcp.scopes_covered", covered, "collect.probe_mcp",
        "области, которые удалось прочитать")
    put("mcp.scopes_unreadable", unreadable, "collect.probe_mcp",
        "файл области существует, но не разобран — область НЕ измерена")
    # MCP-сервер = произвольное исполнение кода. Помечаем те, что стартуют
    # из локального пути вне менеджера пакетов.
    # Абсолютный путь сам по себе не улика. В GUI-приложениях на маке PATH
    # урезан, и «/opt/homebrew/bin/npx» — не самодельный бинарь, а штатный
    # обход этой проблемы: сам пакет всё равно тянется менеджером. Кричать на
    # рекомендованную конфигурацию — значит приучать пропускать предупреждения.
    # Улика — исполняемый файл ВНЕ менеджера пакетов.
    _RUNNERS = {"npx", "npm", "node", "uvx", "uv", "pnpm", "yarn", "bunx", "deno",
                "python", "python3", "pipx", "docker"}
    # Перебираются ВСЕ области, а не объединение. Одно и то же имя может быть
    # объявлено в двух областях по-разному, и объединение показало бы только
    # одно из объявлений — то есть могло бы спрятать ровно подозрительное.
    local = []
    for _метка, _путь, блок in scoped:
        for name, srv in блок.items():
            if not isinstance(srv, dict):
                continue
            cmd = str(srv.get("command", ""))
            if not cmd.startswith("/"):
                continue
            if Path(cmd).name in _RUNNERS:
                continue
            if name not in local:
                local.append(name)
    put("mcp.local_binaries", sorted(local), "collect.probe_mcp",
        "command указывает на абсолютный путь",
        provenance=INFERRED)  # признак по форме пути, не проверка апстрима


# --------------------------------------------------------------------------
# проект
# --------------------------------------------------------------------------
def probe_project() -> None:
    cwd = project_dir()
    put("proj.is_git", (cwd / ".git").exists(), "collect.probe_project", str(cwd))
    markers = {
        "package.json": "node",
        "pyproject.toml": "python",
        "requirements.txt": "python",
        "go.mod": "go",
        "Cargo.toml": "rust",
    }
    langs = sorted({v for f, v in markers.items() if (cwd / f).exists()})
    put("proj.lang", langs, "collect.probe_project",
        provenance=INFERRED)  # по файлам-маркерам в корне
    put("proj.has_tests", any((cwd / d).is_dir() for d in ("tests", "test", "__tests__")),
        "collect.probe_project")
    put("proj.dir", str(cwd), "collect.probe_project",
        "SUPERSTACK_PROJECT_DIR либо текущий каталог")
