#!/usr/bin/env python3
"""SUPERSTACK — часть сборщика фактов: похоже ли это на секрет.

Не самостоятельный инструмент, а кусок `probe/collect.py`, вынесенный, чтобы
ядро сборщика оставалось читаемым. Шов проведён так, чтобы не разрезать ни
одного якоря поломок, а не по красоте занятий.

Здесь весь распознаватель: формы значений и токенов, имена полей, отсев
образцов из документации, отпечаток вместо значения, обход конфига по
структуре и обход текстовых инструкций (скиллы, задачи по расписанию).

ЧЕГО ЗДЕСЬ НЕТ. `scan_secrets_everywhere` — обход МЕСТ — остался в
`collect.py`. Причина названа там же, рядом с ним; коротко: на нём стоит
поломка `probe.secret-scan-swallows-denied`, чей якорь сшивает `ЗАКРЫТО`,
`SECRET_SCAN_UNREADABLE` и `НЕ_ПУСТИЛИ` — три имени, объявленные в ядре.
Раздел «секреты» физически разорван пополам, и это сказано в обоих файлах:
человек, пришедший чинить `sec.scan_unreadable`, откроет этот файл и обхода
здесь не найдёт.

КАК ЭТО ГРУЗИТСЯ. Обычным `import` — никогда: ядро живёт в процессе
несколькими независимыми копиями (doctor.py, what.py, settings_copy.py,
checkpoint.py, семь загрузчиков в tests/), у каждой свой каталог настроек, и
ни одна не регистрируется в `sys.modules`. Общая запись склеила бы части
между копиями. Ядро грузит эту часть по пути тем же способом и поднимает её
имена к себе — см. `collect._подключить`.

ОТКУДА БЕРУТСЯ ИМЕНА ЯДРА. Два разных способа, и путать их нельзя:

  · `НУЖНО_ИЗ_ЯДРА` — впрыск при подключении. Годится для того, что снаружи
    не подменяют: функции, накопители, потолки.
  · `ЯДРО.<имя>` — чтение глобала ядра В МОМЕНТ ОБРАЩЕНИЯ. Обязательно для
    имён, которые подменяют СНАРУЖИ присваиванием на модуле ядра: здесь это
    `CLAUDE` и `TEXT_ASSET_LIMIT` (`tests/test_probes.py` ставит лимит в 2,
    чтобы проверить обрезку). Впрыск заморозил бы значение на момент
    загрузки, и подмена перестала бы доезжать МОЛЧА.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

#: Имена ядра, впрыскиваемые при подключении. Любое ненайденное — отказ при
#: импорте, а не `NameError` внутри пробы: `collect.main` глушит исключение
#: пробы в факт `error.*` и НЕ трогает код возврата, то есть потеря имени
#: прошла бы тихо, оставив отчёт без нескольких проверок.
#:
#: `SECRET_SCAN_UNREADABLE` впрыскивается ССЫЛКОЙ и только мутируется
#: (`.append`). Объявление остаётся в ядре: туда же пишет
#: `scan_secrets_everywhere`, оттуда же читает `probe_claude`, а снаружи
#: список чистят через `collect.SECRET_SCAN_UNREADABLE.clear()`. Вторая копия
#: списка развалила бы все три связи молча.
НУЖНО_ИЗ_ЯДРА = ("skill_roots", "SECRET_SCAN_UNREADABLE",
                 "TEXT_ASSET_BYTES", "TEXT_ASSET_MAX_FINDINGS")

#: Имена, которые ядро поднимает к себе после подключения. `_walk_env`,
#: `_text_assets` и `_scan_text_asset` зовёт `scan_secrets_everywhere` из
#: ядра; `_walk_env`, `_PEM_HEADER` и `_secrets_in_text` берут напрямую
#: `settings_copy.py` и `checkpoint.py`, причём `AttributeError` там не
#: ловится; остальное держат тесты. Подъём — присваиванием глобала ядра, НЕ
#: функцией-обёрткой: обёртка сохранила бы `def <имя>` в ядре и доказывала бы
#: воротам «план» заглушку.
ОТДАЁТ = ("_is_placeholder", "_pem_material", "_secrets_in_text",
          "_secret_material", "_credential_shaped", "_confidence",
          "_looks_secret", "_walk_env", "_text_assets", "_scan_text_asset",
          "_PEM_HEADER", "_PEM_BLOCK", "PEM_MATERIAL_CHARS", "WHY_BY_NAME")

#: Ставится ядром при подключении. До этого момента модуль неработоспособен —
#: и это правильно: часть без ядра не имеет смысла.
ЯДРО = None


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
    tasks = ЯДРО.CLAUDE / "scheduled-tasks"
    if tasks.is_dir():
        for p in sorted(tasks.rglob("*.md")):
            found.append((p, f"scheduled-tasks/{p.relative_to(tasks)}"))
    roots, _ = skill_roots()
    for label, root in roots:
        for p in sorted(root.glob("*/SKILL.md")):
            found.append((p, f"{label}:{p.parent.name}/SKILL.md"))
    if len(found) > ЯДРО.TEXT_ASSET_LIMIT:
        skipped = [{"file": lbl, "path": str(p), "why": "сверх лимита обхода"}
                   for p, lbl in found[ЯДРО.TEXT_ASSET_LIMIT:]]
        found = found[:ЯДРО.TEXT_ASSET_LIMIT]
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
