#!/usr/bin/env python3
"""Подставная машина для проб SUPERSTACK.

Зачем это существует. Тесты, которые запускали настоящий `probe/collect.py`,
читали НАСТОЯЩИЙ ~/.claude, звали `npm ls -g`, лезли в /Applications и брали
CLAUDE_CODE_ENTRYPOINT из окружения человека. Из-за этого «зелёный набор»
описывал не код, а машину: за одну сессию на неизменном коде число сабтестов
уезжало вслед за тем, что человек успел поставить и удалить. Утверждение
«тесты проходят» переставало быть утверждением о продукте.

Здесь машина СОБИРАЕТСЯ, а не наблюдается: известное дерево в temp-каталоге
плюс окружение, в котором ни одна проба не может дотянуться до хоста.

Что закрыто:
  HOME                     -> подставной корень (Path.home() читает именно его);
  SUPERSTACK_PROJECT_DIR   -> подставной проект, а не текущий каталог;
  PATH=""                  -> ни `which`, ни `npm` не находятся, значит
                              host.* и rt.versions_installed не с этой машины;
  CLAUDE_CODE_ENTRYPOINT   -> фиксировано, а не унаследовано;
  SUPERSTACK_IGNORE_PAUSE  -> флаг паузы на настоящей машине не роняет прогон.

Что НЕ закрыто и обязано быть названо (закрывается только правкой collect.py):
  /Library/Application Support/ClaudeCode/managed-settings.json,
  /etc/claude-code/managed-settings.json,
  /Applications/Claude.app/Contents/Resources/app.asar
— три абсолютных пути, зашитых в пробы. Они влияют на ЗНАЧЕНИЯ (набор
установленных версий, дополнительный скоуп настроек), но не на НАБОР КЛЮЧЕЙ
и не на то, какие правила срабатывают: rt.active_version остаётся None,
потому что entrypoint фиксирован как "cli", а native/npm недостижимы.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Синтетический токен собирается конкатенацией, а не лежит литералом: файл
# фикстуры не должен выглядеть как утечка ни для человека, ни для сканера
# секретов. Значение заведомо не является ничьим ключом.
FAKE_GITHUB_TOKEN = "ghp_" + "0123456789abcdefghij" * 2

#: Ровно те переменные, которые получит проба. Наследовать окружение целиком
#: нельзя: именно через него на пробу протекает состояние машины.
def env_for(root: Path, project: Path) -> dict:
    return {
        "HOME": str(root),
        "PATH": "",
        "SUPERSTACK_PROJECT_DIR": str(project),
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "SUPERSTACK_IGNORE_PAUSE": "1",
        # Кодировка вывода не должна зависеть от локали хоста: JSON с русскими
        # строками уезжает в stdout, и ASCII-локаль превратила бы это в UnicodeError.
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data) -> None:
    _write(path, json.dumps(data, ensure_ascii=False, indent=1))


def build_bare(root: Path) -> dict:
    """Пустая машина: HOME существует, ~/.claude нет вообще.

    Нужна как ВТОРАЯ точка сравнения. Проба, которая на чистой машине выходит
    по раннему return, отдаёт меньше фактов — и правила, рассчитанные ровно на
    отсутствие настроек, молча не вычисляются. Такой дефект виден только при
    сравнении двух разных машин, поэтому одной фикстуры мало.
    """
    project = root / "project"
    project.mkdir(parents=True, exist_ok=True)
    return env_for(root, project)


def build_populated(root: Path) -> dict:
    """Обжитая машина: настройки, хуки, скиллы, агенты, память, MCP, секрет.

    Содержимое подобрано так, чтобы сработали правила РАЗНОЙ тяжести и чтобы
    хотя бы одна находка стояла на выводе эвристики (rests_on_inference),
    иначе проверка провенанса гоняется по пустому циклу.
    """
    project = root / "project"
    claude = root / ".claude"

    # --- настройки пользователя -------------------------------------------
    # Хук ss-format ПОДКЛЮЧЁН здесь, ss-observe — нигде: так в фикстуре
    # появляется ровно один спящий хук, а не «сколько получится».
    _write_json(claude / "settings.json", {
        "permissions": {"defaultMode": "plan",
                        "allow": ["Bash(git status)", "Bash(ls)"]},
        "statusLine": {"type": "command", "command": "ss-status"},
        "enabledPlugins": {},
        "hooks": {"PostToolUse": [
            {"matcher": "Write|Edit",
             "hooks": [{"type": "command", "command": "ss-format --stdin"}]},
        ]},
    })

    # --- манифест хуков ----------------------------------------------------
    _write_json(claude / "hooks" / "hooks.json", {"hooks": {"PostToolUse": [
        {"id": "ss-format", "description": "форматирование после правки"},
        {"id": "ss-observe", "description": "наблюдение за сессией"},
    ]}})

    # --- скиллы ------------------------------------------------------------
    _write(claude / "skills" / "alpha" / "SKILL.md",
           "---\nname: alpha\ndescription: разбор логов сборки\n---\n\nтело\n")
    _write(claude / "skills" / "beta" / "SKILL.md",
           "---\nname: beta\ndescription: подготовка релизных заметок\n---\n\nтело\n")
    # Один скилл с тестами: ev.skills_with_tests обязан быть не нулём, иначе
    # соответствующее правило срабатывает и заслоняет остальные.
    (claude / "skills" / "beta" / "tests").mkdir(parents=True, exist_ok=True)

    # --- агенты ------------------------------------------------------------
    # Судья с правом записи: это не «плохая фикстура», а нужный случай —
    # находка на нём помечена INFERRED и держит проверку провенанса.
    _write(claude / "agents" / "code-reviewer.md",
           "---\nname: code-reviewer\nmodel: sonnet\ntools: Read, Grep, Write\n---\n\n"
           "Проверяет диффы.\n")
    _write(claude / "agents" / "builder.md",
           "---\nname: builder\nmodel: haiku\ntools: Read, Write\n---\n\nПишет код.\n")
    _write(claude / "commands" / "ship.md", "Выкатить релиз.\n")

    # --- плагины -----------------------------------------------------------
    # Пустая таблица, а не отсутствие файла: путь установки плагина был бы
    # абсолютным и машинно-зависимым, а фикстура обязана быть переносимой.
    _write_json(claude / "plugins" / "installed_plugins.json", {"plugins": {}})
    _write_json(claude / "plugins" / "known_marketplaces.json", {"demo-market": {}})

    # --- память и метрики --------------------------------------------------
    _write(claude / "projects" / "demo" / "memory" / "MEMORY.md", "# индекс\n")
    _write(claude / "projects" / "demo" / "memory" / "тема.md", "заметка\n")
    (claude / "metrics").mkdir(parents=True, exist_ok=True)
    (claude / "homunculus").mkdir(parents=True, exist_ok=True)

    # --- цикл самоулучшения ------------------------------------------------
    _write(claude / "superstack" / "promotion.yaml", "gate: two-runs\n")
    _write(claude / "superstack" / "budget.json", '{"max": 10}\n')
    _write(claude / "superstack" / "baseline" / "smoke.yaml", "cases: 3\n")
    _write_json(claude / "superstack" / "learned" / "0001.json", {"id": "first"})

    # --- MCP и секрет ------------------------------------------------------
    # local-server стартует из пути ВНЕ менеджера пакетов — это отдельная
    # находка тяжести high, она даёт набору находок разброс по тяжести.
    _write_json(root / ".claude.json", {"mcpServers": {
        "packaged": {"command": "/opt/homebrew/bin/npx", "args": ["-y", "demo-mcp"]},
        "local-server": {"command": str(root / "bin" / "local-server"),
                         "env": {"GITHUB_TOKEN": FAKE_GITHUB_TOKEN}},
    }})

    # --- проект ------------------------------------------------------------
    (project / ".git").mkdir(parents=True, exist_ok=True)
    (project / "tests").mkdir(parents=True, exist_ok=True)
    _write_json(project / "package.json", {"name": "demo"})
    _write_json(project / ".claude" / "settings.json", {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "ss-guard"}]},
    ]}})

    return env_for(root, project)


if __name__ == "__main__":  # ручная проверка: собрать дерево и показать корень
    import sys
    import tempfile
    tmp = tempfile.mkdtemp(prefix="superstack-fake-home-")
    env = build_populated(Path(tmp))
    print(tmp)
    print(json.dumps(env, ensure_ascii=False, indent=1))
    sys.exit(0 if os.path.isdir(tmp) else 1)
