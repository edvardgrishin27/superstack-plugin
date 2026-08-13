#!/usr/bin/env python3
"""SUPERSTACK — отчёт как страница, а не как текст в терминале.

Зачем отдельный рендер. Целевой пользователь в терминал не заходит: он
работает в десктопном приложении. ASCII-рамки, три отдельные команды для
трёх глубин и «напиши /more, чтобы увидеть подробности» — это интерфейс
терминала, и для него он не существует.

Здесь та же структура данных даёт страницу, где глубина раскрывается
нажатием: строка -> обоснование -> буквальные значения фактов. Аудитория
не определяется заранее — она выбирается тем, насколько глубоко человек
раскрыл. Ошибиться в сторону новичка стоит эксперту одного нажатия.

Три правила, унаследованные от текстового рендера и обязательные здесь:
  · блок охвата печатается ПЕРВЫМ, до находок;
  · вывод эвристики помечен и отличим от измерения;
  · «не нашёл» и «не смог проверить» — разные утверждения.

Страница самодостаточна: ни одного внешнего запроса, весь стиль внутри.

  python3 render_html.py findings.json > report.html
"""
from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path

BRAND = "FuturaAI"

MARK = {"critical": "критично", "high": "важно", "medium": "стоит знать",
        "low": "мелочь"}
ACTION = {
    "FIX": "починю сам", "ADD": "добавлю", "REPLACE": "заменю",
    "QUARANTINE": "уберу в карантин", "LEAVE": "оставлю как есть",
    "ASK": "спрошу тебя", "BLOCK": "остановлюсь до твоего решения",
}

CSS = """
/* Дизайн-система Futura AI: чистый чёрный, монохром, стекло.
   Цветные акценты в системе ЗАПРЕЩЕНЫ явно — поэтому тяжесть находки
   передаётся весом, прозрачностью и плотностью границы, а не цветом.
   Тема одна: бренд коммитится в чёрный, а не поддерживает две. */
:root {
  --bg: #000000;
  --bg-alt: #070612;
  --surface: #0F0F14;
  --glass: rgba(255, 255, 255, 0.04);
  --glass-strong: rgba(255, 255, 255, 0.07);
  --primary: #FFFFFF;
  --secondary: rgba(255, 255, 255, 0.70);
  --muted: rgba(255, 255, 255, 0.40);
  --faint: rgba(255, 255, 255, 0.22);
  --border: rgba(255, 255, 255, 0.12);
  --border-subtle: rgba(255, 255, 255, 0.06);
  --shadow-subtle: 0 2px 10px rgba(0, 0, 0, 0.3);
  --shadow-elevated: 0 10px 30px rgba(0, 0, 0, 0.4);
  --glow: 0 0 30px rgba(255, 255, 255, 0.06);
  --radius: 1.5rem;
  --font-head: "Space Grotesk", ui-sans-serif, system-ui, -apple-system, sans-serif;
  --font-body: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  --step--1: clamp(.8rem, .78rem + .1vw, .875rem);
  --step-0: clamp(.95rem, .92rem + .2vw, 1.02rem);
  --step-2: clamp(1.5rem, 1.1rem + 1.6vw, 2.4rem);
  --step-hero: clamp(2.5rem, 1.2rem + 5vw, 5rem);
}
* { box-sizing: border-box; }
.glass-filter { position: absolute; width: 0; height: 0; }
html { color-scheme: dark; }
body {
  margin: 0; background: var(--bg); color: var(--primary);
  font: var(--step-0)/1.65 var(--font-body);
  -webkit-font-smoothing: antialiased;
  background-image:
    radial-gradient(60rem 40rem at 82% -12%, rgba(255,255,255,.055), transparent 62%),
    radial-gradient(44rem 34rem at 6% 8%, rgba(255,255,255,.03), transparent 58%);
  background-attachment: fixed;
}
.wrap { max-width: 56rem; margin: 0 auto; padding: clamp(2.5rem, 7vw, 6rem) 1.15rem 8rem; }

/* угловая метка бренда — на каждой странице */
.brand-mark {
  position: fixed; right: clamp(.8rem, 2vw, 1.6rem); bottom: clamp(.8rem, 2vw, 1.6rem);
  z-index: 40; display: inline-flex; align-items: center; gap: .5rem;
  padding: .5rem .85rem; border-radius: 999px;
  background: rgba(10, 10, 12, .5); border: none;
  backdrop-filter: blur(18px) saturate(1.5);
  -webkit-backdrop-filter: blur(18px) saturate(1.5);
  box-shadow:
    0 8px 32px rgba(0, 0, 0, .5),
    inset 0 1px 0 rgba(255, 255, 255, .30),
    inset 0 0 0 1px rgba(255, 255, 255, .10);
  font-family: var(--font-mono); font-size: .68rem; letter-spacing: .14em;
  text-transform: uppercase; color: var(--secondary);
  transition: border-color .4s ease, box-shadow .4s ease, color .4s ease;
}
.brand-mark:hover {
  color: var(--primary);
  box-shadow:
    0 10px 36px rgba(0, 0, 0, .6),
    inset 0 1px 0 rgba(255, 255, 255, .45),
    inset 0 0 0 1px rgba(255, 255, 255, .16),
    0 0 34px rgba(255, 255, 255, .07);
}
.brand-mark .dot {
  width: .42rem; height: .42rem; border-radius: 50%;
  background: var(--primary); box-shadow: 0 0 10px rgba(255,255,255,.7);
}

header { margin-bottom: clamp(2.5rem, 6vw, 4.5rem); }
.eyebrow {
  font-family: var(--font-mono); font-size: .7rem; letter-spacing: .22em;
  text-transform: uppercase; color: var(--muted); margin: 0 0 1.1rem;
}
h1 {
  font-family: var(--font-head); font-size: var(--step-hero); line-height: 1.02;
  letter-spacing: -.035em; margin: 0 0 .9rem; font-weight: 700;
  background: linear-gradient(144.5deg, #FFFFFF 28%, rgba(255,255,255,.35) 115%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.sub { color: var(--secondary); font-size: var(--step-0); margin: 0; max-width: 42rem; }
.tally { display: flex; flex-wrap: wrap; gap: .5rem; margin: 2rem 0 0; padding: 0; list-style: none; }
.tally li {
  font-family: var(--font-mono); font-size: .7rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--muted);
  padding: .55rem .95rem; border-radius: 999px;
  background: var(--glass); border: 1px solid var(--border-subtle);
}
.tally b {
  color: var(--primary); font-size: .95rem; margin-right: .35rem;
  font-variant-numeric: tabular-nums;
}

/* Единственная поверхность с настоящим преломлением. Их не должно быть много:
   каждый такой слой дорог. Блок неполноты — самое важное на странице, ему и
   отдаётся сигнатурный материал. Преломление живёт только в Blink; в Safari
   и Firefox остаётся тот же frost, потому что url() добавляется ОТДЕЛЬНЫМ
   правилом: положи его в одно значение с blur — и они выбросят всё, включая
   размытие. */
.warn {
  position: relative; overflow: hidden;
  background: rgba(10, 10, 12, .42);
  border: none; border-radius: var(--radius);
  padding: 1.6rem 1.7rem; margin: 2.5rem 0 0;
  backdrop-filter: blur(20px) saturate(1.5);
  -webkit-backdrop-filter: blur(20px) saturate(1.5);
  box-shadow:
    0 12px 48px rgba(0, 0, 0, .6),
    inset 0 1px 0 rgba(255, 255, 255, .34),
    inset 0 0 0 1px rgba(255, 255, 255, .10);
}
@supports (backdrop-filter: url(#glass-refraction)) {
  .warn { backdrop-filter: url(#glass-refraction) blur(18px) saturate(1.5); }
}
.warn::before {
  content: ""; position: absolute; inset: 0 0 auto; height: 50%;
  background: linear-gradient(180deg, rgba(255,255,255,.06), transparent);
  pointer-events: none;
}
.warn h2 {
  font-family: var(--font-head); font-size: 1.15rem; margin: 0 0 .7rem;
  font-weight: 600; letter-spacing: -.01em;
}
.warn ul { margin: .5rem 0 0; padding-left: 1.1rem; color: var(--secondary); font-size: var(--step--1); }
.warn p { margin: .9rem 0 0; font-weight: 500; color: var(--primary); }

h2.section {
  font-family: var(--font-head); font-size: var(--step-2); letter-spacing: -.025em;
  margin: clamp(3rem, 7vw, 5rem) 0 1.5rem; font-weight: 600;
}

/* Стекло по трём признакам, отличающим его от «размытой коробки»:
   1) свет на верхнем ребре — главный признак стекла, а не рамка по контуру;
   2) тонирует стекло, содержимое остаётся резким;
   3) настоящая тень — иначе панель читается как плоская картинка.
   Для ПОВТОРЯЮЩИХСЯ карточек берётся CSS-frost, а не SVG-преломление:
   каждый преломляющий слой дорог, и список из тринадцати начнёт дёргаться. */
.finding {
  position: relative;
  background: var(--glass);
  border-radius: var(--radius); margin: 0 0 .85rem; overflow: hidden;
  border: none;
  backdrop-filter: blur(14px) saturate(1.4);
  -webkit-backdrop-filter: blur(14px) saturate(1.4);
  box-shadow:
    0 8px 32px rgba(0, 0, 0, .45),
    inset 0 1px 0 rgba(255, 255, 255, .30),
    inset 0 0 0 1px rgba(255, 255, 255, .07);
  transition: box-shadow .5s cubic-bezier(.16,1,.3,1),
              background .5s cubic-bezier(.16,1,.3,1),
              transform .5s cubic-bezier(.16,1,.3,1);
}
/* блик: свет падает сверху и гаснет к середине панели */
.finding::before {
  content: ""; position: absolute; inset: 0 0 auto; height: 55%;
  background: linear-gradient(180deg, rgba(255,255,255,.07), transparent);
  pointer-events: none; z-index: 0;
}
.finding > * { position: relative; z-index: 1; }
.finding:hover, .finding[open] {
  background: var(--glass-strong);
  transform: translateY(-1px);
  box-shadow:
    0 14px 44px rgba(0, 0, 0, .55),
    inset 0 1px 0 rgba(255, 255, 255, .42),
    inset 0 0 0 1px rgba(255, 255, 255, .13),
    0 0 40px rgba(255, 255, 255, .05);
}
.finding[open] { transform: none; }
.finding > summary {
  cursor: pointer; padding: 1.35rem 1.5rem; display: grid;
  grid-template-columns: auto 1fr; gap: .5rem 1rem; align-items: baseline;
  list-style: none;
}
.finding > summary::-webkit-details-marker { display: none; }
.finding > summary:focus-visible { outline: 1px solid var(--primary); outline-offset: -3px; }
/* тяжесть — весом и прозрачностью, а не цветом: цветные акценты в системе запрещены */
.sev {
  font-family: var(--font-mono); font-size: .62rem; letter-spacing: .16em;
  text-transform: uppercase; padding: .3rem .6rem; border-radius: 6px;
  white-space: nowrap; border: 1px solid var(--border-subtle); color: var(--faint);
}
.sev.critical { background: var(--primary); color: #000; border-color: var(--primary); font-weight: 700; }
.sev.high { color: var(--primary); border-color: rgba(255,255,255,.45); font-weight: 600; }
.sev.medium { color: var(--secondary); border-color: var(--border); }
.headline {
  font-family: var(--font-head); font-weight: 500; font-size: 1.06rem;
  letter-spacing: -.015em; color: var(--primary);
}
.plain { grid-column: 2; color: var(--secondary); font-size: var(--step--1); }
.body { padding: 0 1.5rem 1.5rem; border-top: 1px solid var(--border-subtle); margin-top: .3rem; padding-top: 1.2rem; }
.body > * { margin: 0 0 .9rem; }
.why { border-left: 1px solid rgba(255,255,255,.3); padding-left: 1rem; color: var(--secondary); }
.why strong {
  display: block; font-family: var(--font-mono); font-size: .65rem;
  letter-spacing: .16em; text-transform: uppercase; color: var(--muted);
  font-weight: 500; margin-bottom: .3rem;
}
.verdict { font-size: var(--step--1); color: var(--muted); }
.verdict span { font-family: var(--font-mono); color: var(--primary); letter-spacing: .04em; }
.inferred {
  font-size: var(--step--1); color: var(--secondary); background: var(--glass-strong);
  padding: .75rem 1rem; border-radius: .9rem; border: 1px solid var(--border-subtle);
}
.claim { font-size: var(--step--1); color: var(--muted); }
details.deep > summary {
  cursor: pointer; font-family: var(--font-mono); font-size: .68rem;
  letter-spacing: .12em; text-transform: uppercase; color: var(--muted);
  padding: .4rem 0; transition: color .3s ease;
}
details.deep > summary:hover { color: var(--primary); }
details.deep pre {
  background: rgba(0,0,0,.5); border: 1px solid var(--border-subtle);
  border-radius: .9rem; padding: 1rem; overflow-x: auto; font-size: .74rem;
  line-height: 1.6; font-family: var(--font-mono); margin: .6rem 0 0;
  color: var(--secondary);
}
.empty {
  background: var(--glass); border: 1px solid var(--border-subtle);
  border-radius: var(--radius); padding: 3.5rem 1.5rem; text-align: center;
  color: var(--muted); font-family: var(--font-mono); font-size: var(--step--1);
  letter-spacing: .06em;
}
footer {
  margin-top: clamp(3rem, 8vw, 6rem); padding-top: 1.6rem;
  border-top: 1px solid var(--border-subtle);
  color: var(--muted); font-size: var(--step--1);
}
footer .rules { font-family: var(--font-mono); font-size: .68rem; color: var(--faint); }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }

/* Блок «что дальше». Материал — тот же frost, что у карточек находок, а не
   второе преломление: преломляющий слой на странице потрачен на блок
   неполноты, и это осознанно (каждый такой слой дорог). Отличается блок не
   материалом, а плотностью: это сводка на один взгляд, а не чтение. */
.next {
  position: relative; overflow: hidden;
  background: var(--glass);
  border: none; border-radius: var(--radius);
  padding: 1.6rem 1.7rem; margin: clamp(3rem, 7vw, 5rem) 0 0;
  backdrop-filter: blur(14px) saturate(1.4);
  -webkit-backdrop-filter: blur(14px) saturate(1.4);
  box-shadow:
    0 8px 32px rgba(0, 0, 0, .45),
    inset 0 1px 0 rgba(255, 255, 255, .30),
    inset 0 0 0 1px rgba(255, 255, 255, .07);
}
.next::before {
  content: ""; position: absolute; inset: 0 0 auto; height: 55%;
  background: linear-gradient(180deg, rgba(255,255,255,.06), transparent);
  pointer-events: none;
}
.next > * { position: relative; z-index: 1; }
.next-h {
  font-family: var(--font-head); font-size: 1.15rem; margin: 0 0 1.1rem;
  font-weight: 600; letter-spacing: -.01em;
}
.next-rows { list-style: none; margin: 0; padding: 0; }
.next-rows li {
  display: grid; grid-template-columns: 2.6rem 1fr; gap: .15rem .9rem;
  align-items: baseline; padding: .75rem 0;
  border-top: 1px solid var(--border-subtle);
}
.next-rows li:first-child { border-top: none; padding-top: 0; }
/* число крупнее подписи: сколько — это главный вопрос к этому блоку */
.next-rows b {
  grid-row: 1 / span 2; align-self: start;
  font-family: var(--font-mono); font-size: 1.35rem; font-weight: 500;
  font-variant-numeric: tabular-nums; color: var(--primary); line-height: 1.2;
}
.next-rows .k { font-family: var(--font-head); font-weight: 500; color: var(--primary); }
.next-rows .d { grid-column: 2; color: var(--muted); font-size: var(--step--1); }
.next-none { margin: 0; color: var(--secondary); font-size: var(--step--1); }
.say {
  margin: 1.4rem 0 0; padding-top: 1.15rem; color: var(--secondary);
  border-top: 1px solid var(--border-subtle);
}
.say-phrase {
  font-family: var(--font-mono); color: var(--primary);
  background: var(--glass-strong); border: 1px solid var(--border-subtle);
  border-radius: .6rem; padding: .28rem .65rem; letter-spacing: .02em;
  white-space: nowrap;
}
.safety { margin: .85rem 0 0; color: var(--faint); font-size: var(--step--1); }
"""


#: Классы находок в порядке убывания того, насколько без человека не обойтись.
#: Порядок задан здесь руками, а не сортировкой: сначала то, где система сама
#: не сдвинется, и только потом то, что можно прочитать на досуге. Тексты —
#: обещание системы про каждый класс, поэтому они живут рядом, а не в разметке.
NEXT_CLASSES = (
    ("BLOCK", "Решаешь только ты", "система останавливается и спрашивает"),
    ("GATE", "Покажу в плане", "применю после твоего подтверждения"),
    ("AUTO", "Сделаю сам", "но назову вслух каждое действие"),
    ("INFORM", "Просто к сведению", "делать ничего не нужно"),
)
#: Пятая строка — не класс, а честность. Правило может отдать класс вне набора
#: (опечатка, новый класс, битый файл). Молча выбросить такие находки нельзя:
#: сумма строк перестанет сходиться с числом находок, и блок начнёт врать
#: спокойным тоном — худший вид вранья в отчёте.
UNCLASSED = ("Правило не сказало, что с этим делать",
             "поэтому сам не трону — покажу и спрошу")

#: Фраз ровно две на весь блок, и показывается всегда РОВНО ОДНА. Меню из
#: четырёх команд — это снова интерфейс терминала, от которого здесь уходят.
SAY_PLAN = "покажи план"
SAY_AGAIN = "проверь ещё раз"


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def coverage_block(data: dict) -> str:
    cov = data.get("coverage") or {}
    if cov.get("trustworthy", True):
        return ""
    rows = []
    if cov.get("rules_skipped"):
        rows.append(f"не отработало проверок: {cov['rules_skipped']} из "
                    f"{cov.get('rules_total', '?')}")
    if cov.get("files_broken"):
        rows.append(f"битых файлов с правилами: {cov['files_broken']}")
    if cov.get("probe_errors"):
        rows.append(f"упавших проб: {cov['probe_errors']}")
    if cov.get("malformed_facts"):
        rows.append(f"фактов без значения: {cov['malformed_facts']}")
    if cov.get("scopes_unmeasured"):
        rows.append(f"мест, куда не удалось заглянуть: {cov['scopes_unmeasured']}")
    items = "".join(f"<li>{esc(r)}</li>" for r in rows)
    return (
        '<section class="warn" aria-labelledby="cov">'
        '<h2 id="cov">Этот отчёт неполный</h2>'
        f"<ul>{items}</ul>"
        "<p>Значит «ничего не нашёл» здесь НЕ означает «всё в порядке».</p>"
        "</section>"
    )


def next_block(data: dict) -> str:
    """Блок «что дальше»: чем отчёт заканчивается вместо тупика.

    Зачем это отдельный блок. Отчёт заканчивался диагнозом: человек дочитывал
    находки и упирался — идти некуда, спросить нечего. Данные для продолжения
    лежали в каждой находке (поле class) и просто не показывались.

    Здесь ничего не придумывается: каждое число — это count находок с таким
    классом, и других чисел в блоке нет. Класс вне набора не выбрасывается, а
    считается отдельной строкой — иначе сумма строк разошлась бы с числом
    находок молча.

    Пустой прогон не показывает четыре нуля: нули читаются как интерфейс,
    который четыре раза говорит «пусто». Вместо них — одна фраза, и она
    РАЗНАЯ в зависимости от того, всё ли удалось проверить: «не нашёл» и
    «не смог проверить» — разные утверждения и здесь тоже.
    """
    findings = data.get("findings") or []
    known = {key for key, _, _ in NEXT_CLASSES}
    counts: dict = {}
    for f in findings:
        cls = f.get("class")
        counts[cls if cls in known else None] = counts.get(
            cls if cls in known else None, 0) + 1

    rows = []
    for key, title, note in NEXT_CLASSES:
        n = counts.get(key, 0)
        if not n:
            continue
        rows.append(f"<li><b>{n}</b><span class=\"k\">{esc(title)}</span>"
                    f"<span class=\"d\">{esc(note)}</span></li>")
    if counts.get(None):
        rows.append(f"<li><b>{counts[None]}</b>"
                    f"<span class=\"k\">{esc(UNCLASSED[0])}</span>"
                    f"<span class=\"d\">{esc(UNCLASSED[1])}</span></li>")

    # Звать план имеет смысл, только когда в плане что-то будет. Иначе
    # следующий шаг честнее назвать повторной проверкой.
    actionable = sum(counts.get(k, 0) for k in ("BLOCK", "GATE", "AUTO"))
    actionable += counts.get(None, 0)

    if rows:
        body = f'<ul class="next-rows">{"".join(rows)}</ul>'
    elif (data.get("coverage") or {}).get("trustworthy", True):
        body = ('<p class="next-none">Применять нечего: ни одна находка не '
                'просит ни решения, ни правки.</p>')
    else:
        body = ('<p class="next-none">Применять нечего из того, что '
                'проверилось, — а проверилось не всё, об этом выше.</p>')

    phrase = SAY_PLAN if actionable else SAY_AGAIN
    return (
        '<section class="next" aria-labelledby="next-h">'
        '<h2 class="next-h" id="next-h">Что дальше</h2>'
        f"{body}"
        f'<p class="say">Чтобы продолжить, скажи: '
        f'<span class="say-phrase">{esc(phrase)}</span></p>'
        '<p class="safety">Копия настроек делается до первой правки. Ничего не '
        'удаляется: всё, что убирается, уезжает в карантин и возвращается '
        'одной командой.</p>'
        "</section>")


def finding_block(f: dict) -> str:
    sev = f.get("severity", "low")
    parts = [
        f'<details class="finding"><summary>'
        f'<span class="sev {esc(sev)}">{esc(MARK.get(sev, sev))}</span>'
        f'<span class="headline">{esc(f.get("headline", f.get("id", "")))}</span>'
        f'<span class="plain">{esc(f.get("plain", ""))}</span>'
        f"</summary><div class=\"body\">"
    ]
    if f.get("why"):
        parts.append(f'<p class="why"><strong>почему это важно</strong>{esc(f["why"])}</p>')
    verdict = ACTION.get(f.get("verdict", ""), f.get("verdict", ""))
    parts.append(f'<p class="verdict">Что я предлагаю: <span>{esc(verdict)}</span></p>')
    if f.get("rests_on_inference"):
        parts.append('<p class="inferred">Часть этого — вывод эвристики, '
                     'а не измерение. Ниже видно, какие именно факты.</p>')
    if f.get("claim"):
        parts.append(f'<p class="claim">{esc(f["claim"])}</p>')

    ev, prov = f.get("evidence") or {}, f.get("provenance") or {}
    if ev:
        lines = []
        for k, v in ev.items():
            tag = prov.get(k, "EXTRACTED")
            suffix = "   ← вывод эвристики" if tag == "INFERRED" else (
                "   ← источник противоречив" if tag == "AMBIGUOUS" else "")
            lines.append(f"{k} = {json.dumps(v, ensure_ascii=False)}{suffix}")
        parts.append(
            '<details class="deep"><summary>Буквальные значения, на которых построен вывод</summary>'
            f"<pre>{esc(chr(10).join(lines))}</pre>"
            f'<pre>Перепроверить руками:\npython3 tools/probe/collect.py &gt; facts.json\n'
            f'python3 tools/adjudicate.py facts.json \'rules/*.json\' | grep -A20 {esc(f.get("id", ""))}</pre>'
            "</details>")
    parts.append("</div></details>")
    return "".join(parts)


def build(data: dict, title: str = "Что я нашёл на твоём компьютере") -> str:
    findings = data.get("findings", [])
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.get("severity", "low")] = counts.get(f.get("severity", "low"), 0) + 1
    tally = "".join(
        f'<li><b>{counts[s]}</b> {esc(MARK.get(s, s))}</li>'
        for s in ("critical", "high", "medium", "low") if counts.get(s))

    if findings:
        body = "".join(finding_block(f) for f in findings)
    else:
        cov = data.get("coverage") or {}
        body = ('<p class="empty">Ничего, что требовало бы вмешательства.</p>'
                if cov.get("trustworthy", True) else
                '<p class="empty">Находок нет — но проверки выполнились не все. '
                'Это НЕ значит, что всё в порядке.</p>')

    rules = ", ".join(esc(r) for r in (data.get("rule_files") or []))
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{esc(title)} — {esc(BRAND)}</title>
<style>{CSS}</style></head>
<body>
<svg class="glass-filter" aria-hidden="true" focusable="false"><defs>
  <filter id="glass-refraction" x="-20%" y="-20%" width="140%" height="140%">
    <feTurbulence type="fractalNoise" baseFrequency="0.008 0.012"
                  numOctaves="2" seed="7" result="noise"/>
    <feGaussianBlur in="noise" stdDeviation="6" result="soft"/>
    <feDisplacementMap in="SourceGraphic" in2="soft" scale="26"
                       xChannelSelector="R" yChannelSelector="G"/>
  </filter>
</defs></svg>
<div class="wrap">
<header>
  <p class="eyebrow">{esc(BRAND)} · Superstack</p>
  <h1>{esc(title)}</h1>
  <p class="sub">Нажми на строку, чтобы увидеть обоснование. Ещё раз — чтобы
  увидеть буквальные значения, на которых оно построено.</p>
  <ul class="tally">{tally}</ul>
</header>
{coverage_block(data)}
<main>
  <h2 class="section">Находки</h2>
  {body}
</main>
{next_block(data)}
<footer>
  <p>Каждый вывод получен детерминированной пробой и версионируемым правилом,
  а не мнением модели.</p>
  <p class="rules">Правила: {rules or "—"}</p>
</footer>
</div>
<aside class="brand-mark" aria-label="{esc(BRAND)}"><span class="dot"></span>{esc(BRAND)}</aside>
</body></html>"""


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    flag = Path.home() / ".claude" / "superstack" / "PAUSE"
    if flag.exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    if len(sys.argv) < 2:
        print("нужен файл с находками:\n"
              "  python3 render_html.py findings.json > report.html", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    if not src.is_file():
        print(f"нет такого файла: {src}", file=sys.stderr)
        return 2
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"файл находок не разбирается как JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(data, dict) or "findings" not in data:
        print(f"это не файл находок: в {src} нет поля findings", file=sys.stderr)
        return 2
    sys.stdout.write(build(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
