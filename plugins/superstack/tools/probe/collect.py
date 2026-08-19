#!/usr/bin/env python3
"""SUPERSTACK — сборщик фактов.

Только читает. Ничего не меняет. Каждый факт помечен источником (probe),
чтобы любое утверждение системы можно было перепроверить руками.

Выход: JSON в stdout. Плоские ключи с точками — на них ссылаются правила.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"

# Бюджет листинга скиллов — доля контекстного окна, отдаваемая под
# name+description всех скиллов. Точное значение отдаёт /doctor;
# это консервативная оценка для предупреждения.
SKILL_LISTING_BUDGET_CHARS = 10_000

facts: dict[str, dict] = {}


# Класс достоверности факта. Заимствовано из graphify, где провенанс
# проставлен на каждом ребре графа: без него вывод эвристики неотличим
# от измерения, и человек не может понять, чему верить.
EXTRACTED = "EXTRACTED"    # прочитано из источника как есть
INFERRED = "INFERRED"      # выведено эвристикой из прочитанного
AMBIGUOUS = "AMBIGUOUS"    # источник противоречив или неполон


def put(key: str, value, probe: str, evidence: str | None = None,
        provenance: str = EXTRACTED) -> None:
    facts[key] = {
        "value": value,
        "probe": probe,
        "evidence": evidence,
        "provenance": provenance,
    }


def sh(cmd: list[str], timeout: int = 10) -> str | None:
    """Выполнить команду, вернуть stdout или None. Никогда не бросает."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# --------------------------------------------------------------------------
# скоупы: где Claude Code на самом деле ищет скиллы и хуки
# --------------------------------------------------------------------------
# Прежняя версия читала ОДИН каталог скиллов и ОДИН файл настроек. Из-за этого
# два утверждения системы были ложными по построению, причём в разные стороны:
#
#   «столько-то скиллов»  — занижение: скиллы включённых плагинов не считались;
#   «столько-то спящих»   — ложное срабатывание: хук, подключённый в другом
#                           файле настроек, объявлялся мёртвым.
#
# Это ровно тот класс дефекта, который система ищет у других: утверждение шире,
# чем измерение. Поэтому здесь измеряются ВСЕ достижимые скоупы, а те, что
# достичь не удалось, попадают в отчёт поимённо — «не прочитал» обязано
# отличаться от «там пусто».

def project_dir() -> Path:
    """Каталог, над которым работает человек.

    Скилл делает `cd` в каталог плагина перед запуском проб, поэтому
    Path.cwd() описывал сам плагин: его git, его язык, его tests/. Факты
    о проекте были систематически ложными — и настройки проектного скоупа
    читались тоже не из проекта.
    """
    return Path(os.environ.get("SUPERSTACK_PROJECT_DIR") or Path.cwd())


def _settings_files() -> list[tuple[str, Path]]:
    """Все файлы настроек, откуда Claude Code берёт хуки, в порядке приоритета."""
    cwd = project_dir()
    return [
        ("managed", Path("/Library/Application Support/ClaudeCode/managed-settings.json")),
        ("managed-linux", Path("/etc/claude-code/managed-settings.json")),
        ("user", CLAUDE / "settings.json"),
        ("user-local", CLAUDE / "settings.local.json"),
        ("project", cwd / ".claude" / "settings.json"),
        ("project-local", cwd / ".claude" / "settings.local.json"),
    ]


def read_settings_scoped() -> tuple[list[tuple[str, dict]], list[str], list[dict]]:
    """Прочитать все файлы настроек.

    Возвращает (пары скоуп-содержимое, охваченные скоупы, нечитаемые скоупы).

    Отсутствующий файл — это не проблема: скоуп просто не используется.
    А вот существующий и битый — это НЕПРОЧИТАННЫЙ скоуп, и молчать о нём
    нельзя: измерение по остальным скоупам в этом случае неполно.
    """
    loaded: list[tuple[str, dict]] = []
    covered: list[str] = []
    unreadable: list[dict] = []
    for label, path in _settings_files():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            unreadable.append({"scope": label, "path": str(path), "reason": str(e)})
            continue
        if isinstance(data, dict):
            loaded.append((label, data))
            covered.append(label)
        else:
            unreadable.append({"scope": label, "path": str(path),
                               "reason": "содержимое не является объектом"})
    return loaded, covered, unreadable


def enabled_plugins() -> tuple[list[dict], list[dict]]:
    """Включённые плагины и каталоги их установки.

    Включённость собирается по ВСЕМ файлам настроек: проектный файл включает
    плагин наравне с пользовательским. Путь берётся ТОЛЬКО из
    installed_plugins.json — это единственный авторитетный источник. Каталог
    plugins/marketplaces/ содержит весь каталог маркетплейса целиком, а не то,
    что установлено: счёт по нему завысил бы число скиллов в разы. Ошибка
    «померил не там» опаснее, чем «не померил» — она даёт число, которому верят.
    """
    enabled: dict[str, bool] = {}
    for _label, data in read_settings_scoped()[0]:
        ep = data.get("enabledPlugins")
        if isinstance(ep, dict):
            enabled.update(ep)

    installed = (read_json(CLAUDE / "plugins" / "installed_plugins.json") or {})
    table = installed.get("plugins", {}) if isinstance(installed, dict) else {}

    active: list[dict] = []
    problems: list[dict] = []
    for key, on in sorted(enabled.items()):
        if on is not True:
            continue
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
    roots: list[tuple[str, Path]] = [("user", CLAUDE / "skills")]
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
    roots: list[tuple[str, Path]] = [("user", CLAUDE / kind)]
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
        path = sh(["which", tool])
        # Наличие пути и работоспособность — разные вещи. Сломанный git
        # (битая установка, отсутствующие CLT) лежит по своему пути и
        # отвечает ошибкой на любую команду; факт «git есть» тогда врёт.
        works = bool(path) and sh([tool, "--version"]) is not None
        put(f"host.{tool}", works, "collect.probe_host",
            f"{path} (проверено вызовом --version)" if works else path)


# --------------------------------------------------------------------------
# claude code
# --------------------------------------------------------------------------
def probe_claude() -> None:
    """Факты о настройках — ВСЕГДА, даже когда настроек нет.

    Прежняя версия выходила по `return`, если ~/.claude/settings.json не
    читался. Последствие: на чистой машине — на той самой, ради которой всё
    строится, — четыре факта не появлялись, и 18 правил из 22 не вычислялись.
    Причём выпадали ровно те, что рассчитаны на отсутствие настроек. Продукт
    молчал именно там, где обязан был говорить громче всего.

    Поэтому отсутствие файла — это ЗНАЧЕНИЕ («настроек нет»), а не выход из
    пробы. Правило про пустую машину должно иметь на чём сработать.
    """
    settings_path = CLAUDE / "settings.json"
    exists = settings_path.is_file()
    settings = read_json(settings_path)
    put("cc.settings_present", exists, "collect.probe_claude", str(settings_path))
    put("cc.settings_valid_json", settings is not None,
        "collect.probe_claude", str(settings_path))

    if not isinstance(settings, dict):
        # Файла нет или он битый. Разница между этими случаями — в
        # cc.settings_present, и она видна правилам.
        settings = {}

    perms = settings.get("permissions", {})
    if not isinstance(perms, dict):
        perms = {}
    allow = perms.get("allow", [])
    put("cc.default_mode", perms.get("defaultMode"), "collect.probe_claude",
        "permissions.defaultMode")
    put("cc.allow_count", len(allow) if isinstance(allow, list) else 0,
        "collect.probe_claude")
    put("cc.statusline", bool(settings.get("statusLine")), "collect.probe_claude")

    # Конституция — то, что читается КАЖДОЙ сессией целиком. Её объём это не
    # вкус, а плата: чем она длиннее, тем меньше места остаётся задаче, и тем
    # вернее середина будет прочитана по диагонали. Потолок поэтому считается
    # числом, а не обсуждается. Берётся максимум из личной и проектной: платит
    # человек за обе сразу.
    строк = 0
    откуда = None
    for файл in (CLAUDE / "CLAUDE.md", Path.cwd() / "CLAUDE.md"):
        try:
            n = len(файл.read_text("utf-8", errors="replace").splitlines())
        except OSError:
            continue
        if n > строк:
            строк, откуда = n, str(файл)
    # Ноль без объяснения читается как «конституция пуста». Разница между
    # «файла нет» и «файл пуст» видна только в улике, поэтому она там и стоит.
    put("cc.constitution_lines", строк, "collect.probe_claude",
        откуда or "ни личной, ни проектной CLAUDE.md не найдено")

    # hooks.wired.* считается в probe_hooks по ВСЕМ файлам настроек: хук,
    # подключённый в проектном settings.json, здесь не виден.

    put("sec.secret_matches", scan_secrets_everywhere(), "collect.probe_claude",
        "сигнатуры совпадений; значения НИКОГДА не сохраняются",
        provenance=INFERRED)  # эвристика по форме ключа и значения
    put("sec.scan_unreadable", list(SECRET_SCAN_UNREADABLE), "collect.probe_claude",
        "конфиги, которые существуют, но не разобраны — там НЕ искали")
    put("sec.scan_files", [label for label, path in _settings_files() if path.is_file()],
        "collect.probe_claude", "какие файлы настроек реально просмотрены")


# --------------------------------------------------------------------------
# секреты
# --------------------------------------------------------------------------
# Ранняя версия смотрела ТОЛЬКО в permissions.allow. Это неверное место:
# ключи в конфигах Claude Code живут в блоках env{} и в mcpServers.*.env{},
# и туда сканер не заглядывал вообще. Проверено вживую: настоящие ключи
# в этих местах не находились, а отчёт при этом молчал.

# Ссылка на переменную окружения — это НЕ секрет, а указание, где он лежит.
# Ловить их — значит кричать на правильно сделанную конфигурацию.
_VAR_REF = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")

# Форма значения. Проверяется до имени ключа: токен опознаётся по префиксу,
# даже если поле называется безобидно.
_VALUE_SHAPE = [
    (re.compile(r"^ghp_[A-Za-z0-9]{20,}$"), "токен GitHub"),
    (re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$"), "токен GitHub (fine-grained)"),
    (re.compile(r"^sk-[A-Za-z0-9_\-]{20,}$"), "ключ вида sk-"),
    (re.compile(r"^xox[baprs]-[A-Za-z0-9\-]{10,}$"), "токен Slack"),
    (re.compile(r"^AKIA[0-9A-Z]{16}$"), "ключ доступа AWS"),
    (re.compile(r"^eyJ[A-Za-z0-9_\-]{10,}\."), "JWT"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "приватный ключ"),
]

# Имя ключа. Срабатывает, только если значение непохоже на ссылку и достаточно длинное.
_KEY_NAME = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|credential|auth)")

# Дата или отметка времени — не секрет, как бы ни называлось поле.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]|$)")

# Причина срабатывания по имени поля. Вынесена в константу, потому что по ней
# отличается уверенность находки: форма значения — улика, имя поля — догадка.
WHY_BY_NAME = "имя поля указывает на секрет, значение похоже на реальное"


# Образец из документации — НЕ утечка. Цена такой находки выше, чем кажется:
# «sk-your-api-key-goes-here» не просто шумит, он ВЫТЕСНЯЕТ настоящий токен,
# лежащий ниже в том же файле, из отчёта — человек уходит чинить то, чего нет,
# а живой ключ остаётся лежать. Ложное срабатывание здесь маскирует истинное.
_PLACEHOLDER = re.compile(r"""(?ix)
      (?:^|[^A-Za-z0-9])(?:your|our|my|the)[-_]?(?:api|key|token|secret|password|pass|value)
    | goes[-_]?here
    | change[-_]?me
    | replace[-_]?(?:me|this|with)
    | placeholder | dummy | redacted | example | sample
    | insert[-_]?(?:your|key|token)
    | \bTODO\b | \bFIXME\b
    | \.\.\.                     # многоточие — вырезанный кусок в документации
    | ^<.*>$ | ^\{\{.*\}\}$
""")


# Маска из повторяющегося символа — самый частый способ показать ключ в
# документации: «sk-xxxxxxxxxxxxxxxxxxxx», «ghp_XXXXXXXXXXXX», «token: 00000…».
# Словесные формы выше её не ловят, поэтому канонический образец получал
# critical/BLOCK «в настройках лежит пароль открытым текстом».
#
# Порог в 8 повторов подряд выбран так, чтобы не задеть живое: настоящий ключ —
# это результат генератора, и восемь одинаковых знаков подряд в нём означали бы
# энтропию, которой у ключей не бывает. Считаем ХВОСТ после префикса, а не всю
# строку: «sk-» в начале настоящий и совпал бы с настоящим ключом.
_MASK_RUN = re.compile(r"(.)\1{7,}")


def _is_placeholder(v: str) -> bool:
    """Похоже ли значение на образец из инструкции, а не на живой секрет."""
    if _PLACEHOLDER.search(v):
        return True
    body = re.sub(r"^(?:sk-ant-|sk-|ghp_|github_pat_|xox[baprs]-|AKIA)", "", v)
    return bool(_MASK_RUN.search(body))


# Приватный ключ. Заголовок «-----BEGIN … PRIVATE KEY-----» одинаков у ВСЕХ
# ключей мира, поэтому хешировать его нельзя: два РАЗНЫХ украденных ключа
# давали один отпечаток, отчёт показывал одну находку, человек менял один ключ
# и считал вопрос закрытым. Хешируется тело.
_PEM_HEADER = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----(.*?)-----END [A-Z ]*PRIVATE KEY-----", re.S)
PEM_MATERIAL_CHARS = 4000


def _pem_material(text: str) -> str:
    """Вещество приватного ключа, а не его заголовок.

    Если тела не видно (ключ обрезан, конец блока за пределами куска), берётся
    всё, что идёт после заголовка: это всё равно различает разные ключи. И
    только когда после заголовка вообще ничего нет, остаётся сам заголовок —
    отпечаток тогда вырожденный, но и различать в этом случае нечего.
    """
    m = _PEM_BLOCK.search(text)
    if m:
        body = re.sub(r"[^A-Za-z0-9+/=]", "", m.group(1))
        if body:
            return body
    h = _PEM_HEADER.search(text)
    tail = text[h.end():] if h else text
    body = re.sub(r"[^A-Za-z0-9+/=]", "", tail[:PEM_MATERIAL_CHARS])
    return body or (h.group(0) if h else text)


# Формы, где секрет — это ЧАСТЬ строки, и захватить надо именно её.
# Третий элемент — требовать ли от захваченного значения вида учётных данных.
# Он нужен там, где «слот пароля» существует и в документации: строку
# postgres://user:password@host пишут в каждом втором README, и без этой
# проверки сканер поднимал тревогу на пяти учебных примерах ради одной
# настоящей находки рядом.
_CAPTURE_SHAPE = [
    # Пароль прямо в команде. Кавычки обязаны быть ПАРНЫМИ: шаблон ['"]…['"]
    # разрешал открыть одинарной, а закрыть двойной, поэтому «pa'ss1XYZ»
    # обрезался до двух символов — и любые два разных пароля с внутренней
    # кавычкой сливались в один отпечаток длиной 2. Третья ветка — без кавычек:
    # «sshpass -p пароль» пишут постоянно, а прежний шаблон требовал кавычки
    # и не находил такой пароль ВООБЩЕ.
    # Вида учётных данных здесь НЕ требуется: явный флаг -p — улика сам по себе,
    # и короткий словарный пароль тут так же настоящий, как длинный.
    (re.compile(r"sshpass\s+-p\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s'\"]+))"),
     "пароль прямо в команде", False),
    # Пароль внутри строки подключения. _credential_shaped отбрасывает всё, где
    # есть «://», и насчёт ссылок он прав — но вместе с ними выбрасывал
    # postgres://admin:ПАРОЛЬ@host, то есть самый обыденный способ утечь.
    (re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:([^\s/@]+)@"),
     "пароль в строке подключения", True),
    # Секретный ключ AWS: 40 знаков base64, по одной лишь форме неотличимых от
    # мусора, поэтому имя поля — часть шаблона. Соседний AKIA… опознаётся сам,
    # но он ИДЕНТИФИКАТОР: без этих сорока знаков он бесполезен, а прежний
    # сканер находил ровно идентификатор и молчал о секрете.
    (re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})"),
     "секретный ключ AWS", False),
]


# Формы токенов, вкраплённых в строку. _VALUE_SHAPE заякорен ^…$ и годится
# только для значения целиком; токен внутри команды («Bash(curl -H
# 'Authorization: Bearer sk-…')») он не видит. А permissions.allow состоит
# ровно из таких строк — то есть сканер был слеп именно там, где сам claim
# правила называет местом жизни секретов.
_EMBEDDED_SHAPE = [
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "токен GitHub"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "токен GitHub (fine-grained)"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "ключ вида sk-"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "токен Slack"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "ключ AWS"),
    # Подпись — часть JWT. Прежний шаблон её отрезал, поэтому один и тот же
    # токен, попавший в словарь (там хешировалось значение целиком) и в строку
    # (там — обрезанное совпадение), давал ДВА отпечатка: одна утечка выглядела
    # как две, и «вторая» продолжала числиться после починки первой.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]+)?"),
     "JWT"),
]


# Присваивание вида KEY=VALUE или KEY: VALUE внутри произвольного текста.
# Нужно там, где нет структуры конфига: в задаче по расписанию, в теле скилла,
# в строке разрешения. Раньше эвристика по ИМЕНИ поля работала только на
# словарях, поэтому «ADMIN_PASSWORD=…» в инструкции не находилось вовсе.
_NAMED_ASSIGNMENT = re.compile(r"""(?x)
    (?:^|[\s,;({\[\"'])
    ([A-Za-z_][A-Za-z0-9_.\-]*)
    \s*[:=]\s*
    (?:\"([^\"]*)\"|'([^']*)'|([^\s,;)}\]]+))
""")

# Правая часть присваивания — ВЫРАЖЕНИЕ, а не литерал.
#
# Инструкции состоят из примеров кода: «password = User.objects.make_password(raw)»,
# «SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')», «token = jwt.sign(payload)».
# Имя поля там «секретное», длина подходящая, классы символов смешаны — то есть
# эвристика по имени срабатывает на каждой второй строке документации. Замер на
# машине автора: 34 находки, из них 33 — примеры кода. Тревога, звучащая тридцать
# три раза подряд впустую, обесценивает тридцать четвёртую, настоящую.
#
# Признаки выражения: вызов, индекс, дженерик, обращение к полю (.foo), путь
# пространства имён (::). Настоящий пароль так не выглядит; при этом фильтр НЕ
# трогает пароли со спецсимволами в начале («!QAZ2wsx#EDC») — они литералы.
_CODE_EXPRESSION = re.compile(r"[()\[\]{}<>]|^[:=,]|\.[A-Za-z_]|::")


def _secrets_in_text(text: str, tail: "str | None" = None) -> list:
    """Все секреты в куске текста: пары (причина, само значение).

    Возвращает СПИСОК, а не первое совпадение. Строка разрешения вида
    «AWS_SECRET_ACCESS_KEY=… AKIA…» несёт и идентификатор, и сам ключ; остановка
    на первом совпадении отчитывалась о менее опасном и умалчивала о более.

    tail — продолжение файла за этой строкой; нужно только приватному ключу,
    тело которого лежит на СЛЕДУЮЩИХ строках.
    """
    out: list = []
    seen: set = set()

    def add(why: str, value: str) -> None:
        # Дедупликация по значению: один и тот же токен, опознанный и по форме,
        # и по имени поля, — одна утечка, а не две.
        if not value or value in seen or _is_placeholder(value):
            return
        if _VAR_REF.match(value):
            return   # «sshpass -p $DEPLOY_PW» — правильная практика, не находка
        if re.fullmatch(r"[\W_]+", value):
            return   # «sshpass -p *» — маска в правиле разрешения, а не пароль
        seen.add(value)
        out.append((why, value))

    if _PEM_HEADER.search(text):
        add("приватный ключ", _pem_material(tail if tail is not None else text))
    for rx, why, strict in _CAPTURE_SHAPE:
        for m in rx.finditer(text):
            value = next((g for g in m.groups() if g), "")
            if strict and not _credential_shaped(value):
                continue       # «postgres://user:password@host» из README
            add(why, value)
    for rx, why in _EMBEDDED_SHAPE:
        for m in rx.finditer(text):
            # Токен, оборванный многоточием («sk-ant-api03-Xy7Kq2Lm9Rt4Vb8Nc1…»),
            # — образец из документации. Само совпадение многоточия не
            # содержит: шаблон останавливается на точке, поэтому проверяется
            # ХВОСТ за совпадением, а не значение.
            if text[m.end():m.end() + 3] == "...":
                continue
            add(why, m.group(0))
    for m in _NAMED_ASSIGNMENT.finditer(text):
        value = next((g for g in m.groups()[1:] if g), "")
        if _CODE_EXPRESSION.search(value):
            continue           # пример кода в инструкции, а не записанный секрет
        why = _looks_secret(m.group(1), value)
        if why:
            add(why, value)
    return out


def _secret_material(value) -> str:
    """Что именно хешируется: САМ секрет, а не строка вокруг него.

    Три отдельных дефекта сходились здесь в один симптом «одна утечка выглядит
    как несколько». Значение с хвостовым переводом строки давало лишний
    отпечаток: совпадение искалось по strip(), а хеш брался от исходной строки.
    Словарная ветка хешировала значение целиком, строковая — только совпадение.
    А JWT вдобавок обрезался по подписи. Теперь обе ветки приводят находку к
    одному и тому же веществу.
    """
    v = str(value).strip()
    hits = _secrets_in_text(v)
    return hits[0][1] if hits else v


def _credential_shaped(v: str) -> bool:
    """Похоже ли значение на сам секрет, а не на указание, где он лежит.

    Отсекается то, что человек кладёт в поля с «секретными» именами штатно:
    пути к файлу с ключом, ссылки, имена провайдеров и хелперов, коды
    регионов, идентификаторы моделей. Общее у них — читаемая структура:
    разделители пути, схема URL, дефисные слова, точки. Секрет так не
    выглядит: это плотная строка без пробелов и словарной структуры.
    """
    if " " in v or "\t" in v:
        return False
    if v.startswith(("/", "~", ".", "$")) or "://" in v:
        return False
    if "/" in v or "\\" in v:
        return False           # путь, а не ключ
    if re.fullmatch(r"[A-Za-z]+(-[A-Za-z0-9]+)+", v):
        return False           # kebab-имя: google-oauth2-pkce, osxkeychain-helper
    if re.fullmatch(r"[a-z]{2}-[a-z]+-\d[a-z]?", v):
        return False           # код региона: eu-central-1a
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", v) and "_" in v and v.islower():
        return False           # snake-имя: cl100k_base_v2_x
    if re.fullmatch(r"[A-Za-z-]+", v):
        return False           # одно слово буквами: X-Custom-Authorization
    # Осталось плотное значение со смесью классов символов — так выглядит ключ.
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    return classes >= 2


def _confidence(key, value) -> str:
    """high — сработала форма значения; low — только имя поля."""
    v = str(value).strip()
    for rx, _ in _VALUE_SHAPE:
        if rx.search(v):
            return "high"
    return "low"


def _looks_secret(key: str, value) -> str | None:
    """Вернуть ПРИЧИНУ срабатывания или None. Значение наружу не отдаётся."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    if _VAR_REF.match(v):
        return None  # ссылка на переменную — правильная практика, не находка
    if _is_placeholder(v):
        return None  # образец из документации, а не живой ключ
    for rx, why in _VALUE_SHAPE:
        if rx.search(v):
            return why
    if _KEY_NAME.search(str(key)) and len(v) >= 12:
        # Отсев ложных: «Token» внутри «FirstTokenDate» — это не токен.
        # Имя поля само по себе — слабая улика. Прежняя версия срабатывала
        # на ЛЮБОЕ длинное значение при подходящем имени и помечала
        # критическим BLOCK-ом пути к ключам, названия провайдеров, регионы
        # и имена хелперов. Проверка, регулярно поднимающая тревогу на
        # правильной конфигурации, приучает пропускать тревоги вообще —
        # и обесценивает настоящую находку рядом.
        if re.search(r"(?i)(date|_at$|time|expiry|expires|updated|created)", str(key)):
            return None
        if _ISO_DATE.match(v):
            return None
        if v.lower() in ("true", "false", "null", "none"):
            return None
        if not _credential_shaped(v):
            return None
        return WHY_BY_NAME
    return None


def _walk_env(obj, path: str, out: list) -> None:
    """Рекурсивно обойти конфиг, помечая места, а не значения."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            # Строковое значение и без того доходит до строковой ветки обхода
            # ниже, где ищутся вкраплённые формы. Отдельная проверка здесь
            # была избыточной: её удаление не меняло поведения, то есть она
            # ничего не защищала — только создавала видимость.
            why = _looks_secret(k, v)
            if why:
                # Длина и отпечаток — от САМОГО секрета. Раньше здесь брался
                # str(v) целиком: токен с хвостовым «\n» превращался в третий
                # отпечаток того же токена, а JWT, лежащий в словаре, не
                # сходился с тем же JWT, найденным внутри строки.
                material = _secret_material(v)
                out.append({"location": here, "why": why,
                            "value_len": len(material),
                            "confidence": _confidence(k, v),
                            "fingerprint": hashlib.sha256(
                                material.encode("utf-8")).hexdigest()[:12]})
            else:
                _walk_env(v, here, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_env(v, f"{path}[{i}]", out)
    elif isinstance(obj, str):
        # Голая строка в списке проверялась ТОЛЬКО на «пароль в команде».
        # Формы токенов к ней не применялись вообще — а это ровно те места,
        # где ключи и живут: mcpServers.*.args и permissions.allow. Сканер
        # был слеп именно там, куда обещал смотреть.
        #
        # Отпечаток считается от САМОГО секрета, а не от команды вокруг него.
        # Раньше хешировалась вся строка разрешения — и один и тот же пароль,
        # лежащий в settings.json и settings.local.json, давал два разных
        # отпечатка. Отчёт показывал две несвязанные находки: человек менял
        # пароль в одном месте, видел, что находка ушла, и оставлял вторую
        # копию жить. Ровно то, ради чего отпечаток и заводился.
        for why, value in _secrets_in_text(obj):
            out.append({"location": path, "why": why, "value_len": len(value),
                        "confidence": "low" if why == WHY_BY_NAME else "high",
                        "fingerprint": hashlib.sha256(
                            value.encode("utf-8")).hexdigest()[:12]})


SECRET_SCAN_UNREADABLE: list = []
SKILL_READ_ERRORS: list = []

# Потолки на текстовый обход: проба обязана оставаться дешёвой. Файлы сверх
# лимита не «пропускаются молча» — они попадают в SECRET_SCAN_UNREADABLE, то
# есть в тот же список, что и нечитаемое, и гасят доверие к отчёту.
TEXT_ASSET_LIMIT = 600
TEXT_ASSET_BYTES = 256 * 1024

# Потолок РАЗЛИЧНЫХ отпечатков на один файл. Не единица: правило «одно
# совпадение на файл» превращало любое ложное срабатывание в глушилку — образец
# «sk-your-api-key-goes-here» в шапке инструкции вытеснял настоящий ghp_ ниже по
# тексту, и тот не попадал в отчёт ВООБЩЕ. Не бесконечность: сорок строк одного
# скилла — это шум, а не сорок находок.
TEXT_ASSET_MAX_FINDINGS = 3


def _text_assets() -> tuple[list, list]:
    """Инструкции, которые агент читает и ИСПОЛНЯЕТ: скиллы и задачи по расписанию.

    Сканер смотрел только в JSON-конфиги. Но пароль, лежащий в SKILL.md, ничем
    не защищённее пароля в settings.json: этот файл так же читается каждой
    подходящей сессией, так же уезжает в бэкапы и так же виден любому, кто
    получил доступ к каталогу. Найдено на машине автора — два пароля в
    settings, третий, ДРУГОЙ, в теле задачи по расписанию, и проба о нём
    молчала.
    """
    found: list = []
    skipped: list = []
    tasks = CLAUDE / "scheduled-tasks"
    if tasks.is_dir():
        for p in sorted(tasks.rglob("*.md")):
            found.append((p, f"scheduled-tasks/{p.relative_to(tasks)}"))
    roots, _ = skill_roots()
    for label, root in roots:
        for p in sorted(root.glob("*/SKILL.md")):
            found.append((p, f"{label}:{p.parent.name}/SKILL.md"))
    if len(found) > TEXT_ASSET_LIMIT:
        skipped = [{"file": lbl, "path": str(p), "why": "сверх лимита обхода"}
                   for p, lbl in found[TEXT_ASSET_LIMIT:]]
        found = found[:TEXT_ASSET_LIMIT]
    return found, skipped


def _scan_text_asset(path: Path, label: str, out: list) -> None:
    """До TEXT_ASSET_MAX_FINDINGS РАЗНЫХ секретов на файл.

    Место называется точно, значение не сохраняется. Обрезка потолком — это не
    «не нашёл», а «не смотрел дальше», поэтому она называется вслух в том же
    списке, что и нечитаемые файлы: иначе отчёт молча выдал бы неполноту за
    полноту.
    """
    try:
        if path.stat().st_size > TEXT_ASSET_BYTES:
            SECRET_SCAN_UNREADABLE.append(
                {"file": label, "path": str(path), "why": "файл слишком велик"})
            return
        text = path.read_text("utf-8", errors="replace")
    except OSError as e:
        SECRET_SCAN_UNREADABLE.append(
            {"file": label, "path": str(path), "why": str(e)})
        return
    seen: set = set()
    truncated = False
    pos = 0
    for num, line in enumerate(text.split("\n"), 1):
        # Хвост файла нужен только приватному ключу: его заголовок стоит на
        # этой строке, а тело — на следующих. Резать его на каждой строке
        # дорого, поэтому берём только при попадании заголовка.
        tail = text[pos:] if _PEM_HEADER.search(line) else None
        pos += len(line) + 1
        for why, value in _secrets_in_text(line, tail=tail):
            fp = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
            if fp in seen:
                continue
            if len(seen) >= TEXT_ASSET_MAX_FINDINGS:
                truncated = True
                break
            seen.add(fp)
            out.append({"location": f"строка {num}", "file": label, "why": why,
                        "value_len": len(value),
                        "confidence": "low" if why == WHY_BY_NAME else "high",
                        "fingerprint": fp})
        if truncated:
            break
    if truncated:
        SECRET_SCAN_UNREADABLE.append(
            {"file": label, "path": str(path),
             "why": f"находок больше потолка — показаны первые "
                    f"{TEXT_ASSET_MAX_FINDINGS}"})


def scan_secrets_everywhere() -> list:
    """Все места, где в конфигах Claude Code реально живут ключи.

    Отпечаток вместо значения: позволяет узнать один и тот же секрет в двух
    местах, не сохраняя его. Сам секрет не попадает ни в факты, ни в находки,
    ни в отчёт, ни в бэкап.
    """
    out: list = []
    # Список мест берётся из общего реестра скоупов, а не заводится здесь
    # заново: два независимых списка расходятся, и расходятся молча.
    targets = [(path, label) for label, path in _settings_files()]
    targets += [
        (HOME / ".claude.json", "~/.claude.json"),
        (project_dir() / ".mcp.json", "проект/.mcp.json"),
    ]
    for path, label in targets:
        if not path.is_file():
            continue
        data = read_json(path)
        if data is None:
            # Файл есть, но не разобран. Молча пропустить нельзя: секрет мог
            # быть именно там, а отчёт сказал бы «не нашёл».
            SECRET_SCAN_UNREADABLE.append({"file": label, "path": str(path)})
            continue
        found: list = []
        _walk_env(data, "", found)
        for f in found:
            f["file"] = label
            out.append(f)

    # Второй проход: не конфиги, а инструкции. Секрет одинаково открыт в обоих.
    assets, skipped = _text_assets()
    SECRET_SCAN_UNREADABLE.extend(skipped)
    for path, label in assets:
        _scan_text_asset(path, label, out)
    return out


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

    mk = read_json(CLAUDE / "plugins" / "known_marketplaces.json") or {}
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

    manifest_path = CLAUDE / "hooks" / "hooks.json"
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
# память
# --------------------------------------------------------------------------
def probe_memory() -> None:
    root = CLAUDE / "projects"
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
    put("mem.instincts_active", (CLAUDE / "homunculus").is_dir(),
        "collect.probe_memory", "~/.claude/homunculus/")
    put("mem.cost_metrics", (CLAUDE / "metrics").is_dir(),
        "collect.probe_memory", "~/.claude/metrics/")


# --------------------------------------------------------------------------
# mcp
# --------------------------------------------------------------------------
def probe_mcp() -> None:
    cfg = read_json(HOME / ".claude.json") or {}
    servers = cfg.get("mcpServers", {})
    put("mcp.declared", list(servers.keys()), "collect.probe_mcp")
    # MCP-сервер = произвольное исполнение кода. Помечаем те, что стартуют
    # из локального пути вне менеджера пакетов.
    # Абсолютный путь сам по себе не улика. В GUI-приложениях на маке PATH
    # урезан, и «/opt/homebrew/bin/npx» — не самодельный бинарь, а штатный
    # обход этой проблемы: сам пакет всё равно тянется менеджером. Кричать на
    # рекомендованную конфигурацию — значит приучать пропускать предупреждения.
    # Улика — исполняемый файл ВНЕ менеджера пакетов.
    _RUNNERS = {"npx", "npm", "node", "uvx", "uv", "pnpm", "yarn", "bunx", "deno",
                "python", "python3", "pipx", "docker"}
    local = []
    for name, srv in servers.items():
        if not isinstance(srv, dict):
            continue
        cmd = str(srv.get("command", ""))
        if not cmd.startswith("/"):
            continue
        if Path(cmd).name in _RUNNERS:
            continue
        local.append(name)
    put("mcp.local_binaries", local, "collect.probe_mcp",
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


# --------------------------------------------------------------------------
# самоулучшение
# --------------------------------------------------------------------------
def probe_evolution() -> None:
    """Замкнут ли цикл улучшения: наблюдение -> отбор -> потолок -> измерение."""
    manifest = read_json(CLAUDE / "hooks" / "hooks.json") or {}
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
    put("ev.promotion_gate", _has_content(CLAUDE / "superstack" / "promotion.yaml"),
        "collect.probe_evolution", "~/.claude/superstack/promotion.yaml (непустой)")

    # 3. Потолок. Без него улучшение превращается в накопление.
    put("ev.budget_ceiling", _has_content(CLAUDE / "superstack" / "budget.json"),
        "collect.probe_evolution", "~/.claude/superstack/budget.json (непустой)")

    # 4. Измерение. Без него «стало лучше» — это ощущение.
    baseline = CLAUDE / "superstack" / "baseline"
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
    ks = CLAUDE / "superstack" / "PAUSE"
    tool = Path(__file__).resolve().parent.parent / "pause.sh"
    put("disc.kill_switch_present", tool.is_file() and os.access(tool, os.X_OK),
        "collect.probe_discipline", str(tool))
    put("disc.paused", ks.exists(), "collect.probe_discipline", str(ks))

    # Видимое компаундирование: журнал находок существует и наполняется.
    learned = CLAUDE / "superstack" / "learned"
    n = len(list(learned.glob("*.json"))) if learned.is_dir() else 0
    put("disc.learned_entries", n, "collect.probe_discipline", str(learned))


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



# --------------------------------------------------------------------------
# версии рантайма
# --------------------------------------------------------------------------
MODEL_ROUTING_FLOOR = (2, 1, 170)

# Путь к движку десктопа — константа модуля, а не литерал внутри пробы: иначе
# выбор активной версии нельзя проверить тестом, не имея на машине настоящего
# /Applications/Claude.app, то есть результат теста зависел бы от компьютера.
DESKTOP_ASAR = Path("/Applications/Claude.app/Contents/Resources/app.asar")


def _ver(t: tuple) -> str:
    return ".".join(str(x) for x in t)


def _newest_version(versions) -> "str | None":
    """Самая новая версия ПО ЧИСЛАМ, а не по строкам.

    sorted(...)[-1] сравнивает посимвольно и ставит «2.1.99» выше «2.1.170»,
    потому что «9» > «1». Это ровно то сравнение, против которого ниже написан
    _at_least — и оно давало ложную активную версию десктопа, а следом ложный
    вердикт rt.subagent_model_routing: движок, где маршрутизация субагентов
    работает, объявлялся слишком старым.
    """
    best = None
    for v in versions:
        parts = tuple(int(x) for x in re.findall(r"\d+", v))
        if not parts:
            continue
        if best is None or parts > best[0]:
            best = (parts, v)
    return best[1] if best else None


def _at_least(version: "str | None", floor: tuple) -> "bool | None":
    """Сравнение версий по числам, а не по строкам.

    Возвращает None, когда версия неизвестна: «не смог проверить» — это не
    «не дотягивает». Правило, сработавшее на None, обвинило бы человека в
    том, чего никто не измерял.
    """
    if not version:
        return None
    parts = re.findall(r"\d+", version)
    if not parts:
        return None
    got = tuple(int(p) for p in parts[:len(floor)])
    got += (0,) * (len(floor) - len(got))
    return got >= floor


def probe_runtime() -> None:
    """Какая версия Claude Code РАБОТАЕТ, а не какая просто установлена.

    На одной машине их бывает несколько сразу: нативный установщик, npm-пакет
    и собственный движок внутри десктоп-приложения. Они расходятся на десятки
    минорных версий. Совет «удали, это уже есть в ядре» без знания активной
    версии — это совет вслепую: у человека на старом движке нативной замены
    ещё нет.
    """
    found = {}

    # 1. Нативный установщик: симлинк на каталог с номером версии.
    link = HOME / ".local" / "bin" / "claude"
    try:
        if link.exists():
            target = os.path.realpath(link)
            mv = re.search(r"(\d+\.\d+\.\d+)", os.path.basename(target))
            if mv:
                found["native"] = mv.group(1)
    except Exception:
        pass

    # 2. npm глобально.
    out = sh(["npm", "ls", "-g", "--depth=0"], timeout=20)
    if out:
        mv = re.search(r"@anthropic-ai/claude-code@(\d+\.\d+\.\d+)", out)
        if mv:
            found["npm"] = mv.group(1)

    # 3. Десктоп-приложение несёт собственный движок.
    if DESKTOP_ASAR.is_file():
        out = sh(["/usr/bin/strings", str(DESKTOP_ASAR)], timeout=60)
        if out:
            newest = _newest_version(set(re.findall(r'"(2\.\d+\.\d{2,3})"', out)))
            if newest:
                found["desktop"] = newest

    put("rt.versions_installed", found, "collect.probe_runtime",
        "могут расходиться между собой")

    # Какой движок исполняет ЭТУ сессию.
    entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    put("rt.entrypoint", entry or None, "collect.probe_runtime",
        "CLAUDE_CODE_ENTRYPOINT")
    active = None
    if "desktop" in entry and "desktop" in found:
        active = found["desktop"]
    elif found:
        active = found.get("native") or found.get("npm")
    put("rt.active_version", active, "collect.probe_runtime",
        "версия движка, исполняющего эту сессию",
        provenance=INFERRED if active else AMBIGUOUS)

    put("rt.version_conflict", len(set(found.values())) > 1,
        "collect.probe_runtime",
        "на машине несколько разных версий",
        provenance=INFERRED)

    # Порог, ниже которого `model:` во frontmatter субагента не действует:
    # агент молча исполняется той же моделью, что и сессия. Для обычного
    # агента это мелочь, для ревьюера — подмена сути: «второе мнение»,
    # выданное той же моделью, что писала код, это самоаттестация.
    # Отдельный факт нужен потому, что грамматика правил не умеет сравнивать
    # версии, а строковое сравнение «2.1.42 < 2.1.170» даёт неверный ответ.
    put("rt.subagent_model_routing", _at_least(active, MODEL_ROUTING_FLOOR),
        "collect.probe_runtime",
        f"маршрутизация субагентов по модели требует {_ver(MODEL_ROUTING_FLOOR)}"
        f"; активна {active or 'неизвестно'}",
        provenance=INFERRED if active else AMBIGUOUS)



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
    for probe in (probe_host, probe_claude, probe_inventory, probe_hooks,
                  probe_memory, probe_mcp, probe_project, probe_evolution, probe_discipline, probe_runtime):
        try:
            probe()
        except Exception as e:  # проба падает — остальные продолжают
            put(f"error.{probe.__name__}", str(e), "collect.main")
    json.dump(facts, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
