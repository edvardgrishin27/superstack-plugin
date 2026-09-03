#!/usr/bin/env python3
"""SUPERSTACK — живая панель хода: где мы сейчас и на ком ход.

Зачем отдельно от отчёта.

Отчёт отвечает на вопрос «что получилось» и собирается один раз. Панель
отвечает на другой — «где я и чего от меня ждут», — и нужна ПОСТОЯННО. Половина
ожиданий в прогоне это не «долго считает», а «каждый ждёт другого»: человек
ждёт систему, система ждёт человека, и оба молчат. На панели это видно сразу:
ход стоит на человеке сорок минут.

Как устроено обновление, и почему именно так.

Страница читает `state.json` сама, каждые несколько секунд. Значит обновление
состояния стоит ОДНОЙ ЗАПИСИ JSON — тем же `progress.py`, который его и ведёт;
HTML не перегенерируется и в контекст модели не попадает. Перерисовывать
страницу на каждое изменение значило бы платить за неё токенами по десять раз
за фазу — и её перестали бы обновлять, то есть она врала бы про «сейчас».

Разметка строится методами DOM, а не склейкой строк. Данные тут свои, но
правило дешевле соблюдать всегда, чем помнить, где можно нарушить: в состояние
попадают имена задач и деталей, которые пишет модель, и однажды туда приедет
угловая скобка.

Почему в странице лежит ещё и снимок состояния.

Чтение соседнего файла с диска браузер запрещает, и панель без сервера
показывала бы пустые полосы — то есть «ничего не происходит» вместо «нечего
показывать». Поэтому каждая запись состояния вписывает СНИМОК прямо в
страницу, между границами. Открытый двойным кликом файл показывает снимок
сразу и через месяц, при мёртвом сервере и на другой машине; живой сервер
кладёт свежее состояние поверх. Сервер остаётся ради обновления цифр, а не
ради того, чтобы страница вообще что-то показала.

  python3 live_panel.py <каталог .superstack>     -> пишет panel.html
  python3 live_panel.py <каталог> --serve [порт]  -> и поднимает сервер
  python3 live_panel.py <каталог> --check [порт]  -> отвечает ли уже панель
  python3 live_panel.py <каталог> --open          -> открыть файл в браузере

  `--serve` в прогоне всегда запускают отвязанным (`nohup … &`), и весь его
  вывод летит в /dev/null — если порт занят, узнать об этом изнутри того же
  процесса неоткуда. `--check` — отдельная синхронная команда: она сама
  опрашивает порт и сверяет ответ с panel.html на диске, и её код возврата
  виден снаружи, в отличие от вывода отвязанного процесса.

  код 0 — готово, 2 — нечего показывать, 3 — ошибка вызова,
  4 — порт занят или панель не ответила вовремя,
  5 — файл не открылся: ссылку надо сказать словами, прогон не блокируется
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# Дизайн-система берётся из ОДНОГО места — того же, что рисует отчёт и план.
# Своя палитра в панели уже случилась: фиолетовый акцент, зелёная полоса,
# жёлтое предупреждение — при том что в системе цветных акцентов НЕТ ВОВСЕ, и
# это записано в ней явно. Панель выглядела чужой страницей того же продукта.
_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
from render_html import CSS as SYSTEM_CSS, BRAND  # noqa: E402
import plain_ru  # noqa: E402
import derive_phase  # noqa: E402
import autonomy  # noqa: E402
# Числа считает ход стройки, а не разметка. Ввоз односторонний: `progress.py`
# зовёт панель только изнутри функции записи, поэтому кольца при загрузке нет.
import progress  # noqa: E402

PANEL = "panel.html"
DEFAULT_PORT = 8787
HOST = "127.0.0.1"

#: Границы снимка состояния внутри страницы. Между ними — и только между ними —
#: живёт то, что переписывается на каждой записи состояния; всё остальное в
#: файле неизменно, и панель можно переписывать по кусочку, не пересобирая.
#: Границы намеренно на латинице: их ищет и код, и человек в редакторе.
SNAPSHOT_BEGIN = "<!--SUPERSTACK-SNAPSHOT-BEGIN-->"
SNAPSHOT_END = "<!--SUPERSTACK-SNAPSHOT-END-->"

#: Код возврата, когда файл не удалось открыть. Отдельный от «3 — ошибка
#: вызова» и от «4 — порт занят»: неоткрывшееся окно НЕ ломает прогон, ссылку
#: просто называют словами. Но и молчать об этом нельзя — «открыл» без
#: проверки это подтверждение раньше результата.
OPEN_FAILED = 5

#: Чем открывать файл. Обычно решает система (`open`, `xdg-open`,
#: `os.startfile`); переменная нужна там, где системного открывателя нет вовсе,
#: и в проверке отказа — иначе честность кода возврата нечем показать.
OPENER_ENV = "SUPERSTACK_OPENER"

#: Сколько ждать открывателя. Он только передаёт файл браузеру и выходит;
#: зависший вызов не имеет права держать фазу.
OPEN_TIMEOUT_SECONDS = 10

#: Код возврата, когда порт занят или панель не подтвердила себя за отведённое
#: время. Отдельный от «3 — ошибка вызова»: это не опечатка в команде, а факт
#: о сети, и агенту важно отличить «почини вызов» от «порт занят, разберись».
PORT_BUSY = 4

#: Сколько всего ждать ответа при --check и с каким шагом опрашивать. Сервер
#: подняли только что и отвязанно (`nohup … &`) — секунда-другая на старт
#: Python и импорт http.server это норма, а не повод сразу объявлять порт
#: занятым.
CHECK_BUDGET_SECONDS = 3.0
CHECK_POLL_SECONDS = 0.25
CHECK_TIMEOUT_SECONDS = 1.0

#: Фазы прогона в порядке, в котором они идут. Панель показывает ВСЕ, а не
#: только текущую: человек, видящий одну строку «идёт фаза 3», не знает ни
#: сколько позади, ни сколько впереди, и любая пауза читается как поломка.
#: Фазы прогона в порядке, в котором они идут — НАЗВАННЫЕ ТАК, КАК ИХ ВИДИТ
#: ЧЕЛОВЕК. Первая редакция звалась «завести · понять · спека · направление»:
#: это слова конструкции, а не работы, и человеку они не говорят ничего.
#: «Направление» вдобавок врало — на этой фазе делают дизайн-систему и дизайн,
#: а слово обещало выбор из списка.
#:
#: Панель показывает ВСЕ фазы, а не только текущую: человек, видящий одну
#: строку, не знает ни сколько позади, ни сколько впереди, и любая пауза
#: читается как поломка.
PHASES = (
    "Записали просьбу",
    "Разобрали задачу",
    "Описали, что строим",
    "Дизайн-система",
    "Дизайн экранов",
    "План работ",
    "Пишем код",
    "Проверяем",
    "Приёмка",
    "Отчёт",
)

#: После скольких минут ожидание на человеке перестаёт быть «идёт» и начинает
#: быть «забыли». Двадцать — не измерение, а выбор: меньше даёт ложную тревогу
#: на обычной паузе, больше не спасает от вечера, потерянного на взаимном
#: ожидании.
STALE_MINUTES = 20

#: Тот же вопрос для держателей «система» и «исполнитель». Раньше тревога
#: умела срабатывать только на ожидании человека: упавшая сессия или уснувший
#: ноутбук на этапе «Пишем код» оставляет ход на исполнителе навсегда, а
#: панель бесконечно показывала «пишет помощник» как ни в чём не бывало.
#: Порог НАРОЧНО больше человеческого: их собственная работа в норме идёт
#: часами без единой новой записи в state.json (один таск, много помощников,
#: полный прогон проверок) — человеческий порог в двадцать минут звонил бы на
#: каждой обычной фазе, а после пары ложных тревог панель перестают читать.
#: Час не спасает от ноутбука, закрытого на всю ночь, но ловит обрыв в
#: пределах одного рабочего захода, а не только назавтра.
STALE_MINUTES_RUN = 60

#: Этап, на котором пишут код. Названо константой, потому что на него ссылается
#: и `progress.py`: переход задачи в работу обязан сам двигать этап сюда.
#: Совпадение двух написаний проверяется тестом — разошлись бы они молча.
BUILD_PHASE = "Пишем код"

_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Ход работы — __BRAND__</title>
<style>__CSS__
/* --- панель хода -------------------------------------------------------- */
/* Состояние передаётся ВЕСОМ и ПРОЗРАЧНОСТЬЮ, как во всей системе: цветных
   акцентов в ней нет вовсе. Здесь это помогает — монохром не даёт покрасить
   «заявлено» и «доказано» одинаково зелёным. */
/* Имя своё, не .wrap: общий словарь стилей отчёта уже занимает
   .wrap под страницу отчёта — max-width: 56rem и свои поля. Оба правила на
   одном классе не перекрывают друг друга целиком, а СКЛАДЫВАЮТСЯ: колонки
   доставались панели, а тесная ширина отчёта — вместе с ними, и весь экран
   читался сжатым в две трети своей ширины. */
.panel-shell { display: grid; grid-template-columns: 17rem 1fr; min-height: 100vh; }
@media (max-width: 68.75rem) { .panel-shell { grid-template-columns: 1fr; } }
/* Рельс: десять станций слева, текущая раскрыта. Человек, видящий одну строку
   «идёт этап 7», не знает ни сколько позади, ни сколько впереди, и любая
   пауза читается как поломка. */
/* min-width: 0 — без него содержимое рельса (горизонтальная лента станций на
   узком экране) тянет саму колонку сетки шире экрана вместо того, чтобы
   переноситься или прокручиваться внутри себя. max-width: 100% — тот же
   отказ на случай, когда широкий контент передаёт свою ширину не через
   min-content колонки, а через явный width/flex-basis потомка; переносное
   возвращение первого 375px-снимка показало, что одного min-width мало на
   живом Chrome (по системным шрифтам ширина текста иная, чем в headless-
   профиле, которым это проверялось до того). */
.rail { border-right: 1px solid var(--border-subtle); padding: 2.1rem 1.7rem;
        min-width: 0; max-width: 100%; }
.main { padding: 2.1rem 3rem 3.5rem; min-width: 0; max-width: 100%; }
.proj { color: var(--secondary); font-size: var(--step-0); margin: .2rem 0 2rem;
        line-height: 1.35; }
/* Раньше здесь стояла плашка-капсула («ПИШЕТ ПОМОЩНИК») с тремя строками
   пояснений под ней — на экране это читалось отдельной карточкой поверх
   рельса. Теперь это две строки обычным текстом: кто держит ход, и почему —
   как в рельсе станций слева, без рамки вокруг них. */
.who-line { margin: 1.6rem 0 0; padding-top: 1.1rem;
            border-top: 1px solid var(--border-subtle); }
.who { font-family: var(--font-head); font-size: 1rem; font-weight: 600;
       color: var(--secondary); }
/* Ход на человеке — единственное состояние, которое переворачивает строку:
   белым по чёрному нельзя не заметить, и это дешевле любого цвета. */
.who[data-owner="человек"] { color: var(--primary); }
/* Плашки режима и размера: как работаем, насколько глубоко, сколько частей.
   Договорённость, которой не видно, к третьей части забывают обе стороны —
   человек уверен, что его спросят, система уверена, что уже не надо. Одна
   строка чипов, а не карточка: они идут ПОСЛЕ того, кто держит ход, а не
   перед ним. */
.badges { display: flex; flex-wrap: wrap; gap: .4rem; margin: .6rem 0 0; }
/* `display: flex` перебивает служебное «спрятано», и панель без записанного
   режима показывала две пустые плашки. Видно это стало на странице, открытой
   без сервера: там режима в снимке может не быть вовсе. */
.badges[hidden] { display: none; }
.badges + .badges { margin-top: .35rem; }
.badge { display: inline-block; padding: .18rem .7rem; border-radius: 999px;
         font-family: var(--font-mono); font-size: .62rem; letter-spacing: .1em;
         text-transform: uppercase; border: 1px solid var(--border-subtle);
         color: var(--muted); }
.badge:empty { display: none; }
/* Подстрочник — «что это значит», человеческими словами. Без него название
   этапа сообщает ровно столько, сколько человек уже знает: «Пишем код» и
   «Приёмка» одинаково непрозрачны тому, кто не пишет код. */
.means { color: var(--secondary); font-size: var(--step--1); margin: .1rem 0 .5rem; }
.detail { color: var(--muted); font-size: .82rem; margin: .3rem 0 0; line-height: 1.4; }
.since { font-family: var(--font-mono); font-size: .68rem; letter-spacing: .08em;
         color: var(--muted); margin-top: .35rem; display: block; }
.since.stale { color: var(--primary); }
.since.stale::after { content: "__WAIT_LONG__"; color: var(--muted); }
/* Держатели «система»/«исполнитель»: тот же цвет через общий .stale, но свой
   текст. Правило идёт ПОСЛЕ .since.stale::after с той же специфичностью —
   у псевдоэлемента одна content, и здесь она обязана победить последней. */
.since.stale-run::after { content: "__STALE_RUN_SUFFIX__"; color: var(--muted); }
ol.phases { list-style: none; margin: 0; padding: 0 0 0 1.35rem;
            position: relative; }
ol.phases::before { content: ""; position: absolute; left: .3rem; top: .6rem;
                    bottom: .6rem; width: 1px; background: var(--border); }
ol.phases li { padding: .42rem 0; color: var(--faint); font-size: .95rem;
               position: relative; }
ol.phases li::before { content: ""; position: absolute; left: -1.35rem;
                       top: .72rem; width: .62rem; height: .62rem;
                       border-radius: 50%; border: 1px solid var(--faint);
                       background: var(--bg); }
ol.phases li[data-state="done"] { color: var(--secondary); }
ol.phases li[data-state="done"]::before { background: var(--secondary);
                                          border-color: var(--secondary); }
ol.phases li[data-state="now"] { color: var(--primary); font-weight: 600;
                                 padding: .6rem 0; }
ol.phases li[data-state="now"]::before { background: var(--primary);
                                         border-color: var(--primary);
                                         box-shadow: 0 0 1.2rem rgba(255,255,255,.45); }
ol.phases li .t { display: block; font-size: .72rem; color: var(--muted);
                  font-weight: 400; margin-top: .1rem; }
.topbar { display: flex; justify-content: space-between; align-items: center;
          font-family: var(--font-mono); font-size: .62rem; letter-spacing: .16em;
          text-transform: uppercase; color: var(--muted); margin-bottom: 2.6rem; }
/* Первая строка страницы — фраза первым лицом: что делаю и сколько осталось.
   Человек приходит с одним вопросом, и ответ на него не должен собираться
   глазами из четырёх плиток. Имя своё, не .say: общий словарь стилей отчёта
   уже занимает .say под другую строку отчёта, со своими полями и
   верхней чертой — без переименования обе примешивались бы к этому заголовку
   молча. Ширина растёт до 51.25rem (820px): на 46rem фраза, которая раньше
   умещалась в две строки, переносилась на три. */
/* overflow-wrap: anywhere — обычного переноса по пробелам не хватает на узком
   экране: часть работы в {what} иногда приезжает одним длинным словом
   (составное название, путь), и на 375px оно одно шире колонки. */
.sayline { font-family: var(--font-head); font-size: clamp(24px, 4vw, 40px);
       font-weight: 600; line-height: 1.15; letter-spacing: -.01em;
       max-width: 51.25rem; margin: 0 0 .6rem; overflow-wrap: anywhere; }
.saysub { color: var(--secondary); font-size: 1.25rem; margin: 0 0 .4rem;
          max-width: 51.25rem; overflow-wrap: anywhere; }
/* Четыре числа в ряд, без рамок: рамка вокруг каждого числа делает их
   одинаково важными, а важно из них одно. */
.prog { display: grid; grid-template-columns: repeat(4, 1fr); max-width: 65rem;
        margin: 2rem 0 .5rem; border-top: 1px solid var(--border);
        border-bottom: 1px solid var(--border); }
.pg { padding: 1.2rem 1.2rem 1.1rem 0; border-right: 1px solid var(--border-subtle); }
.pg:last-child { border-right: 0; }
.pg + .pg { padding-left: 1.2rem; }
.pg .k { font-family: var(--font-mono); font-size: .62rem; letter-spacing: .18em;
         text-transform: uppercase; color: var(--muted); margin-bottom: .6rem; }
.pg .v { font-family: var(--font-head); font-size: 2.2rem; font-weight: 600;
         line-height: 1; }
/* Единица — мелко и рядом, а не тем же кеглем: «1 ч 36» и «мин» одного
   размера сталкивают строку в перенос там, где хватило бы одной строки. */
.pg .v small { font-size: 1rem; font-weight: 400; color: var(--secondary);
               margin-left: .3rem; }
.pg .n { font-size: .74rem; color: var(--muted); margin-top: .5rem;
         line-height: 1.45; }
/* Шкала из десяти засечек: сколько этапов позади, видно не читая. */
.ticks { display: flex; gap: .35rem; margin: 1.6rem 0 3rem; }
.ticks i { width: 2.1rem; height: 3px; border-radius: 2px; background: var(--faint); }
.ticks i.d { background: var(--primary); }
.ticks i.n { background: var(--primary); box-shadow: 0 0 1.2rem rgba(255,255,255,.45); }
/* Две линейки, которые НИКОГДА не складываются: доказанное машиной и
   названное готовым. Одна полоса на двоих — и «сказал» становится
   достижением. */
.rulers { max-width: 51.25rem; margin-bottom: 3rem; }
.rl { display: grid; grid-template-columns: 9rem 1fr 4.5rem; align-items: center;
      gap: 1.1rem; padding: .5rem 0; }
.rl .k { font-family: var(--font-mono); font-size: .75rem; letter-spacing: .16em;
         text-transform: uppercase; color: var(--muted); line-height: 1.3; }
.rl .b { height: .9rem; border-radius: .25rem; background: rgba(255,255,255,.04);
         border: 1px solid var(--border-subtle); position: relative;
         overflow: hidden; }
.rl .b i { position: absolute; inset: 0 auto 0 0; width: 0; border-radius: .25rem; }
.rl.proved .b i { background: var(--primary); }
/* Заявленное — штрихом, а не заливкой: другая форма, а не другой цвет. */
.rl.said .b i { background: repeating-linear-gradient(135deg,
                rgba(255,255,255,.5) 0 5px, transparent 5px 10px); }
.rl .n { font-family: var(--font-head); font-size: 1.375rem; font-weight: 600;
         text-align: right; }
.rl .n span { font-size: .72rem; color: var(--muted); font-weight: 400;
              margin-left: .25rem; }
.gap { color: var(--secondary); font-size: var(--step--1); margin: .5rem 0 0;
       padding-left: 10.1rem; }
.rub { font-family: var(--font-mono); font-size: .62rem; letter-spacing: .18em;
       text-transform: uppercase; color: var(--muted); margin: 0 0 .8rem; }
h2 { font-family: var(--font-mono); font-size: .62rem; letter-spacing: .18em;
     text-transform: uppercase; color: var(--muted); margin: 0 0 .9rem;
     font-weight: 600; }
/* Ведомость двойной записи: слева обещание словами человека, справа —
   подтверждение машиной. Одна колонка без другой и есть тот самый отчёт,
   в котором обещание подтверждает само себя. */
.ved { max-width: 65rem; margin-bottom: 3rem; border-top: 1px solid var(--border); }
.vh, .vr { display: grid; grid-template-columns: 1fr 1fr; }
.vh { font-family: var(--font-mono); font-size: .62rem; letter-spacing: .14em;
      text-transform: uppercase; color: var(--muted); padding: .7rem 0;
      border-bottom: 1px solid var(--border); }
.vr { border-bottom: 1px solid var(--border-subtle); }
.vr > div { padding: 1rem 1.1rem 1rem 0; }
.vr .r { border-left: 1px solid var(--border-subtle); padding-left: 1.3rem; }
.vr .l { font-size: var(--step-0); line-height: 1.35; }
.vr .l em, .vr .r em { display: block; font-style: normal;
                       font-family: var(--font-mono); font-size: .68rem;
                       color: var(--muted); margin-top: .3rem; }
.vr .l.inf { color: var(--secondary); }
.vr .r.proven { color: var(--primary); font-family: var(--font-mono);
                font-size: .88rem; }
/* «Со слов» — курсивом и БЕЗ ЕДИНОГО ЧИСЛА: цифра рядом со словом читается
   как измерение, даже когда меряли только обещание. */
.vr .r.claimed { color: var(--secondary); font-style: italic; }
.vr .r.running { color: var(--secondary); }
.vr .r.waiting, .vr .r.dropped, .vr .r.deferred { color: var(--faint); }
.vr.dim .l { color: var(--faint); }
.vt { display: grid; grid-template-columns: 9rem 1fr; padding: .9rem 0;
      font-size: var(--step--1); color: var(--secondary);
      border-bottom: 1px solid var(--border); }
.vt .k { font-family: var(--font-mono); font-size: .62rem; letter-spacing: .18em;
         text-transform: uppercase; color: var(--muted); }
.more { font-size: var(--step--1); color: var(--muted); margin: .7rem 0 0; }
/* Лента «что произошло» — только события, у которых есть отметка времени в
   файле. Пересказ по памяти сюда попасть не может. */
.feed { max-width: 51.25rem; margin-bottom: 3rem; }
.ev { display: grid; grid-template-columns: 3.6rem 1fr 9rem; gap: 1rem;
      padding: .6rem 0; border-bottom: 1px solid var(--border-subtle);
      font-size: .92rem; color: var(--secondary); }
.ev .t { font-family: var(--font-mono); color: var(--muted); font-size: .78rem; }
.ev .r { text-align: right; color: var(--muted); font-size: .72rem; }
.ev.proven .r { color: var(--primary); }
/* Полоса «от тебя» — единственный светящийся элемент страницы. Светится
   ровно то, что ждёт человека: если светится всё, не светится ничего. */
.you { max-width: 65rem; border: 1px solid var(--border); border-radius: 1rem;
       padding: 1.1rem 1.3rem; margin-bottom: 2.4rem; }
/* Светится ТОЛЬКО тогда, когда от человека действительно что-то нужно. Если
   светится всегда — не светится ничего, и полосу перестают замечать ровно к
   тому моменту, когда в ней появляется дело. */
.you.on { border-color: var(--primary);
          box-shadow: 0 0 1.5rem rgba(255,255,255,.28); }
.you .k { font-family: var(--font-mono); font-size: .62rem; letter-spacing: .18em;
          text-transform: uppercase; color: var(--muted); margin-bottom: .35rem; }
.you .v { font-size: var(--step-0); }
.you .v:empty, .you .k:empty { display: none; }
.foot { color: var(--muted); font-size: var(--step--1); margin-top: .8rem; }
.eta { color: var(--secondary); font-size: .95rem; margin: .5rem 0 0; }
/* Расхождение панели с работой. Показывается словами, а не прячется: панель,
   молча показывающая устаревший этап, хуже отсутствующей — ей верят. Имя
   своё, не .warn: общий словарь стилей отчёта уже занимает .warn под
   тяжёлую стеклянную карточку отчёта (фон, размытие, тень) — оба правила на
   одном классе складывались бы, и однострочный флаг панели стал бы такой же
   карточкой без единой причины. */
.panelwarn { border: 1px solid var(--border); border-left-width: 3px;
        border-radius: var(--radius); padding: .7rem .9rem; margin: 1rem 0 0;
        color: var(--secondary); font-size: var(--step--1); max-width: 51.25rem; }
.fine { max-width: 65rem; color: var(--muted); font-size: .74rem; line-height: 1.7;
        border-top: 1px solid var(--border-subtle); padding-top: 1rem; }
.fine p { margin: .2rem 0; }
.fine b { color: var(--secondary); font-weight: 500; }
/* Язык — переключателем на самой странице, а не выбором при сборке. Панель
   уезжает человеку одним файлом, и кто его откроет — заранее не известно;
   пересобирать её ради второго языка означало бы, что второго не будет. */
.lang { display: flex; gap: .3rem; }
.lang button { font-family: var(--font-mono); font-size: .62rem;
               letter-spacing: .16em; padding: .18rem .55rem;
               border-radius: 999px; border: 1px solid var(--border-subtle);
               background: transparent; color: var(--faint); cursor: pointer; }
.lang button[aria-pressed="true"] { color: var(--primary);
                                    border-color: var(--border); }
.lang button:hover { color: var(--secondary); }
.lang button:focus-visible { outline: 1px solid var(--primary);
                             outline-offset: 2px; }
/* Четыре подписи дублировали цифры и линейки рядом с собой словами — человек,
   уже увидевший «33%» и «2 из 6», читал тот же счёт ещё раз абзацем ниже.
   Текст остаётся в словаре целиком (его читает /what и снимок состояния),
   просто не печатается вторым слоем на самой панели. */
#tasksrub, #eta, #etanote, #barnote, #tasksline { display: none; }
/* Адаптивность: три точки, а не одна. На узком экране рельс станций уходит
   наверх горизонтальной лентой вместо второй колонки, четыре числа сжимаются
   в 2×2 и затем в столбик, а ведомость из двух колонок становится строкой
   обещания над строкой подтверждения — иначе меньший экран сминает то, что
   было рассчитано на 1440px, и ничего не остаётся читаемым. */
@media (max-width: 62.5rem) { .prog { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 37.5rem) {
  .main { padding: 1.4rem 1.1rem 2.4rem; }
  .rail { padding: 1.4rem 1.1rem; }
  .prog { grid-template-columns: 1fr; }
  .pg { border-right: 0; border-bottom: 1px solid var(--border-subtle);
        padding: 1rem 0; }
  .pg:last-child { border-bottom: 0; }
  .pg + .pg { padding-left: 0; }
  .rl { grid-template-columns: 6rem 1fr 3.5rem; gap: .6rem; }
  .gap { padding-left: 0; }
  .ev { grid-template-columns: 1fr; gap: .15rem; }
  .ev .r { text-align: left; }
}
@media (max-width: 68.75rem) {
  .rail { border-right: 0; border-bottom: 1px solid var(--border-subtle); }
  /* Десять станций вертикальным рельсом не помещаются рядом с контентом —
     здесь они переносятся в несколько строк горизонтальной лентой, а не
     разрывают страницу на вечно длинную первую колонку. Перенос (flex-wrap),
     а не прокрутка: скролл внутри узкого контейнера в статичном снимке
     неотличим от обрезанного текста — «Оп…» на срезе виден, стрелки или
     полосы прокрутки нет, а сама прокрутка требует жеста, которого снимок
     не покажет. */
  ol.phases { display: flex; flex-wrap: wrap; gap: .5rem 1.4rem; width: 100%;
              max-width: 100%; padding: .3rem 0 .7rem 1.35rem; }
  ol.phases::before { display: none; }
  ol.phases li { flex: 0 1 auto; min-width: 0; max-width: 100%; }
}
@media (max-width: 45rem) {
  .vh, .vr { grid-template-columns: 1fr; }
  .vr .r { border-left: 0; padding-left: 0; margin-top: .6rem;
           padding-top: .6rem; border-top: 1px solid var(--border-subtle); }
}
</style></head>
<body>
<div class="panel-shell">
  <aside class="rail">
    <p class="eyebrow" id="eyebrow"></p>
    <p class="proj" id="proj"></p>
    <ol class="phases" id="phases"></ol>
    <p class="who-line"><span class="who" id="who"></span></p>
    <div class="detail" id="detail"></div>
    <div class="detail" id="why"></div>
    <div class="since" id="since"></div>
    <div class="badges" id="badges" hidden>
      <span class="badge" id="modebadge"></span>
      <span class="badge" id="depthbadge"></span>
    </div>
    <div class="badges"><span class="badge" id="sizebadge"></span></div>
  </aside>
  <main class="main">
    <div class="topbar">
      <span id="updated"></span>
      <span class="lang" id="lang">
        <button type="button" data-lang="ru" aria-pressed="true">__LANG_RU__</button>
        <button type="button" data-lang="en" aria-pressed="false">__LANG_EN__</button>
      </span>
    </div>
    <h1 class="sayline"><span id="say"></span> <span id="sayleft"></span></h1>
    <p class="saysub" id="saysub"></p>
    <p class="means" id="means"></p>
    <p class="panelwarn" id="mismatch" hidden></p>
    <div class="prog">
      <div class="pg"><div class="k" id="donerub"></div>
        <div class="v" id="donev"></div><div class="n" id="donenote"></div></div>
      <div class="pg"><div class="k" id="runrub"></div>
        <div class="v" id="runv"></div><div class="n" id="runnote"></div></div>
      <div class="pg"><div class="k" id="etarub"></div>
        <div class="v" id="etav"></div><div class="n" id="etahow"></div></div>
      <div class="pg"><div class="k" id="stagerub"></div>
        <div class="v" id="stagev"></div><div class="n" id="stagenote"></div></div>
    </div>
    <p class="foot" id="eta"></p>
    <p class="foot" id="etanote"></p>
    <div class="ticks" id="ticks"></div>
    <div class="rulers">
      <p class="rub" id="tasksrub"></p>
      <div class="rl proved"><span class="k" id="provedrub"></span>
        <div class="b"><i id="bar"></i></div><span class="n" id="provednum"></span></div>
      <div class="rl said"><span class="k" id="saidrub"></span>
        <div class="b"><i id="saidbar"></i></div><span class="n" id="saidnum"></span></div>
      <p class="gap" id="gap"></p>
      <p class="foot" id="tasksline"></p>
      <p class="foot" id="barnote"></p>
    </div>
    <h2 id="ledgerrub"></h2>
    <div class="ved">
      <div class="vh"><span id="ledgerleft"></span><span id="ledgerright"></span></div>
      <div id="ledger"></div>
      <div class="vt"><span class="k" id="ledgertotalrub"></span>
        <span id="ledgertotal"></span></div>
      <p class="more" id="ledgermore"></p>
      <p class="foot" id="covrub"></p>
      <p class="eta" id="cov"></p>
      <p class="foot" id="covnote"></p>
    </div>
    <h2 id="feedrub"></h2>
    <div class="feed" id="feed"></div>
    <!-- Один заголовок, одна фраза: раньше «ход у тебя»/«ничего не нужно» и
         «что нужно от тебя»/«ничего» стояли двумя строками и в частом случае
         (не твой ход, долга нет) повторяли одно и то же дважды разными
         словами. drawYou() теперь решает сама, что важнее — ход, долг или
         честное «ничего», — и пишет ровно одну фразу. -->
    <div class="you" id="you">
      <div class="k" id="yourub"></div>
      <div class="v" id="youv"></div>
    </div>
    <div class="fine">
      <p><b id="finerub"></b> <span id="finev"></span></p>
      <p id="checkline"></p>
      <p id="snapsline"></p>
      <p id="finenote"></p>
      <p id="where"></p>
    </div>
  </main>
</div>
__SNAPSHOT__
<script>
const BRAND = __BRAND_JSON__;
const PHASES = __PHASES__;
const STALE_MS = __STALE__ * 60000;
const STALE_RUN_MS = __STALE_RUN__ * 60000;
// Слова для человека приезжают из общего словаря, а не пишутся здесь. Иначе
// правка формулировки становится правкой кода, и человеку приходится просить
// о ней отдельно — а он и так уже попросил дважды.
const WORDS = __WORDS__;
const BUILD_PHASE = __BUILD_PHASE__;
// Прочерк вместо числа. Пустое место с прочерком — это «нечего показать»;
// ноль на его месте был бы утверждением, которого никто не измерял.
const DASH = '—';
// ОБЯЗАНА стоять раньше первого вызова firstLang(): та зовёт snapshot(), а
// snapshot() читает $('snapshot') немедленно, при первой отрисовке. $ ниже
// firstLang() — это `let LANG = firstLang()` в мёртвой зоне константы: на
// чистом профиле (пустой localStorage) страница падает раньше первой цифры,
// и панель молча остаётся пустой — та же поломка, что и «ничего не
// показывать» вместо «нечего показывать», только без единого слова о ней.
const $ = (id) => document.getElementById(id);
// Языков два, и ОБА вшиты в файл. Догружать второй словарь панели неоткуда:
// она обязана работать открытой с диска, где соседние файлы читать нельзя.
let LANG = firstLang();
let W = WORDS[LANG];
// Последнее нарисованное — чтобы переключение языка перерисовало то же самое,
// а не ждало следующего опроса и не показывало полминуты смесь двух языков.
let SEEN = null;

function ago(iso) {
  if (!iso) return '';
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  // «меньше минуты», а не «только что»: строка склеивается и с «идёт уже», и с
  // «обновилось … назад», и «обновилось только что назад» читается как брак.
  if (m < 1) return W.copy.less_than_a_minute;
  if (m < 60) return m + ' ' + W.copy.unit_min;
  return Math.floor(m / 60) + ' ' + W.copy.unit_hour + ' ' +
         (m % 60) + ' ' + W.copy.unit_min;
}

function minutes(ms) {
  const m = Math.round(ms / 60000);
  if (m < 60) return m + ' ' + W.copy.unit_min;
  return Math.floor(m / 60) + ' ' + W.copy.unit_hour +
         (m % 60 ? ' ' + (m % 60) + ' ' + W.copy.unit_min : '');
}

// То же время, что minutes(), но с минутами отдельно от числа: в ряду из
// четырёх цифр «1 ч 36 мин» одним кеглем переносится на три строки, а
// цифра с мелкой единицей рядом — нет.
function minutesParts(ms) {
  const m = Math.round(ms / 60000);
  if (m < 60) return { big: String(m), small: W.copy.unit_min };
  const h = Math.floor(m / 60), r = m % 60;
  // На ровном часе «мин» рядом со «2 ч» без остатка минут значило бы число,
  // которого нет — единица едет мелко, только когда есть что ей подписывать.
  return { big: h + ' ' + W.copy.unit_hour + (r ? ' ' + r : ''),
          small: r ? W.copy.unit_min : '' };
}

// Крупная цифра + мелкая подпись рядом, тем же приёмом, что и minutesParts:
// сама единица не обязана тянуть кегль всей цифры на себе.
function bigSmall(id, big, small) {
  const el = $(id);
  el.replaceChildren(document.createTextNode(big));
  if (small) {
    const s = document.createElement('small');
    s.textContent = small;
    el.append(s);
  }
}

// Час и минуты отметки времени. Показываются только там, где отметку поставил
// код: у доказанного — конец проверки, у снимка — момент снимка.
function hhmm(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return String(d.getHours()).padStart(2, '0') + ':' +
         String(d.getMinutes()).padStart(2, '0');
}

function fill(key, values) {
  let s = W.copy[key] || '';
  for (const k in values) s = s.split('{' + k + '}').join(values[k]);
  return s;
}

// Название этапа на языке читателя. Ключ везде остаётся русским — он же ключ
// состояния; перевод живёт только на выходе, иначе состояние, записанное при
// одном языке, перестало бы читаться при другом.
function label(name) {
  return (W.names && W.names[name]) || name || '';
}

// «Сколько ещё» считается ТОЛЬКО по замеренному: сколько заняли части, которые
// уже закончились. Середина ряда, а не среднее — одна застрявшая часть не
// должна растягивать оценку на всё остальное. Замеров нет — оценки нет, и об
// этом говорится прямо: выдуманный срок хуже честного «не знаю», потому что
// ему верят.
function drawEta(tasks) {
  $('etarub').textContent = W.copy.eta_rubric;
  $('etanote').textContent = '';
  $('etahow').textContent = '';
  $('etav').textContent = DASH;
  // Пустой список — это НЕ «всё сделано». Это вся первая половина прогона
  // (просьба, спека, дизайн, план — часы), пока работу ещё не разбили на
  // части: у этого случая тот же tasks.length === 0, что и у «все части
  // кончились» ниже. Старый код не различал их и показывал «Все части
  // закончены» в первую же минуту разговора, не сделав ровно ничего.
  if (!tasks.length) { $('eta').textContent = W.copy.no_tasks; return null; }
  const spans = tasks
    .filter(t => t.started && t.finished)
    .map(t => new Date(t.finished) - new Date(t.started))
    .filter(ms => ms > 0)
    .sort((a, b) => a - b);
  const left = tasks.filter(t => t.status === 'running' ||
                                 t.status === 'waiting').length;
  if (!left) { $('eta').textContent = W.copy.eta_done; return null; }
  if (!spans.length) { $('eta').textContent = W.copy.eta_none; return null; }
  const typical = spans[Math.floor(spans.length / 2)];
  const rest = minutes(typical * left);
  const restParts = minutesParts(typical * left);
  bigSmall('etav', '≈' + restParts.big, restParts.small);
  $('etahow').textContent = fill('n_left_note', { typical: minutes(typical) });
  $('eta').textContent = W.copy.eta_known
    .replace('{typical}', minutes(typical))
    .replace('{left}', left)
    .replace('{rest}', rest);
  $('etanote').textContent = W.copy.eta_note;
  return rest;
}

// Покрытие просьбы — ДРУГОЕ число, чем полоса частей работы, и показывать одно
// без другого опасно: части можно закрыть все и при этом упустить требование.
// Ровно это и случилось сегодня — страница вышла точно по системе, а цена и
// «что входит» пропали. Покрытие брифа заслуживает отдельной карточки: у нас
// оно считается давно, а панель об этом молчала.
//
// Числа берутся из файла требований (facts.requirements), а не из поля,
// которое заполняли отдельной командой: пропущенная команда молча оставляла
// вчерашние числа, и панель показывала их как сегодняшние.
function drawCoverage(s) {
  $('covrub').textContent = W.copy.coverage_rubric;
  const f = (s.facts || {}).requirements;
  const R = (f && f.total) ? { total: f.total, covered: f.done,
                               dropped: f.dropped } : (s.requirements || {});
  if (R.total === null || R.total === undefined ||
      R.covered === null || R.covered === undefined) {
    $('cov').textContent = W.copy.coverage_none;
    $('covnote').textContent = '';
    return;
  }
  const live = Math.max(0, (R.total || 0) - (R.dropped || 0));
  $('cov').textContent = W.copy.coverage_known
    .replace('{done}', R.covered).replace('{live}', live);
  $('covnote').textContent = W.copy.coverage_note;
}

// Долг — что нужно ОТ МЕНЯ, посчитанный без вывода на экран: заглушки и
// переменные окружения копятся молча, а всплывают в тот день, когда человек
// пытается этим пользоваться. Саму фразу собирает drawYou() — единственный
// блок «от тебя» решает сам, что важнее показать: ход, долг или честное
// «ничего», — а не печатает две строки, повторяющие одно и то же.
function debtTotal(s) {
  const d = s.debt || {};
  const n = (k) => (d[k] || []).length;
  const total = n('stub') + n('assumption') + n('env');
  return { total, phrase: total ? W.copy.debt_line
    .replace('{stub}', n('stub'))
    .replace('{assumption}', n('assumption'))
    .replace('{env}', n('env')) : '' };
}

function drawPhases(s, current) {
  const list = $('phases');
  list.replaceChildren();
  const at = PHASES.indexOf(current || '');
  const stages = {};
  for (const st of (s.stages || [])) stages[st.name] = st.detail || '';
  const ph = s.phase || {};
  PHASES.forEach((name, i) => {
    const li = document.createElement('li');
    li.dataset.state = at < 0 ? '' : (i < at ? 'done' : (i === at ? 'now' : ''));
    li.append(document.createTextNode(label(name)));
    // Одна строка факта под этапом: что на нём случилось. Берётся из записи
    // этапа, а у текущего — из подписи и времени, которые уже ведутся.
    const facts = [];
    if (stages[name]) facts.push(stages[name]);
    // «идёт N мин» перед подписью части: сначала сколько уже длится, потом
    // что именно длится — так читает эту строку глаз, сверху вниз по важности.
    if (i === at && ph.since) facts.push(W.copy.here_for + ' ' + ago(ph.since));
    if (i === at && ph.detail && !stages[name]) facts.push(ph.detail);
    if (facts.length) {
      const t = document.createElement('span');
      t.className = 't';
      t.textContent = facts.join(' · ');
      li.append(t);
    }
    list.append(li);
  });
  // Шкала из десяти засечек: пройденное видно, не читая ни одного слова.
  const ticks = $('ticks');
  ticks.replaceChildren();
  PHASES.forEach((name, i) => {
    const b = document.createElement('i');
    if (at >= 0 && i < at) b.className = 'd';
    if (at >= 0 && i === at) b.className = 'n';
    ticks.append(b);
  });
}

// Первая строка страницы: что делаю и сколько осталось — фразой, а не
// таблицей. Всё в ней взято из состояния: имя части в работе, оценка по
// замерам, счёт частей.
function drawSay(s, tasks, rest) {
  const running = tasks.filter(t => t.status === 'running')
                       .map(t => t.name).filter(Boolean);
  const p = (s.facts || {}).parts || {};
  const ph = s.phase || {};
  // «Пишу {what}.» без двоеточия читает часть как продолжение фразы, а не
  // как подпись после надписи — и первая буква тогда идёт строчной, как у
  // любого продолжения предложения.
  const what = running.join(', ');
  $('say').textContent = running.length
    ? fill('say_building', { what: what.charAt(0).toLowerCase() + what.slice(1) })
    : fill('say_phase', { what: label(ph.name) || W.copy.not_started });
  $('sayleft').textContent = rest ? fill('say_left', { rest: rest })
                                  : (tasks.length ? W.copy.say_left_unknown : '');
  const at = tasks.findIndex(t => t.status === 'running');
  $('saysub').textContent = tasks.length
    ? fill('say_sub', { n: (at < 0 ? p.proven || 0 : at + 1),
                        total: p.total || tasks.length,
                        proven: p.proven || 0, claimed: p.claimed || 0 })
    : W.copy.say_sub_no_parts;
}

// Четыре числа в ряд. Каждое — пересчёт того, что лежит в файлах; ни одно не
// вписано словами. Единица — мелкой подписью рядом с цифрой (bigSmall), а не
// тем же кеглем: «1 ч 36» и «мин» одного размера не умещаются в узкую ячейку.
function drawNumbers(s) {
  const f = s.facts || {}, p = f.parts || {}, st = f.stages || {};
  $('donerub').textContent = W.copy.n_done;
  if (p.percent_proven === null || p.percent_proven === undefined) {
    bigSmall('donev', DASH, '');
  } else {
    bigSmall('donev', String(p.percent_proven), W.copy.unit_percent);
  }
  $('donenote').textContent = p.total
    ? fill('n_done_note', { proven: p.proven, total: p.total })
    : W.copy.n_done_none;
  $('runrub').textContent = W.copy.n_running;
  if (f.started) {
    const parts = minutesParts(Date.now() - new Date(f.started));
    bigSmall('runv', parts.big, parts.small);
  } else {
    bigSmall('runv', DASH, '');
  }
  $('runnote').textContent = f.started ? W.copy.n_running_note : '';
  $('stagerub').textContent = W.copy.n_stage;
  bigSmall('stagev', st.now ? String(st.now) : DASH,
           st.now && st.total ? fill('n_stage_of', { total: st.total }) : '');
  const name = label((s.phase || {}).name);
  $('stagenote').textContent = name || '';
}

// Две линейки, которые не складываются. Полоса «доказано» растёт ТОЛЬКО от
// проверенного машиной; названное готовым идёт своей, штриховой, и зазор между
// ними назван словами — иначе он читается как одна общая готовность.
function drawRulers(s, tasks) {
  const p = (s.facts || {}).parts || {};
  const proven = p.proven || 0, claimed = p.claimed || 0;
  $('tasksrub').textContent = W.copy.tasks_rubric;
  $('barnote').textContent = W.copy.bar_note;
  $('provedrub').textContent = W.copy.ruler_proved;
  $('saidrub').textContent = W.copy.ruler_said;
  $('tasksline').textContent = tasks.length
    ? W.copy.counts.replace('{proven}', proven)
        .replace('{claimed}', claimed).replace('{total}', tasks.length)
    : W.copy.no_tasks;
  $('bar').style.width =
    tasks.length ? Math.round(100 * proven / tasks.length) + '%' : '0';
  $('saidbar').style.width = tasks.length
    ? Math.round(100 * (p.said || 0) / tasks.length) + '%' : '0';
  number('provednum', proven, tasks.length);
  number('saidnum', p.said || 0, tasks.length);
  $('gap').textContent = tasks.length
    ? (p.gap ? fill('gap_line', { gap: p.gap }) : W.copy.gap_none) : '';
}

function number(id, value, of) {
  const el = $(id);
  el.replaceChildren();
  el.append(document.createTextNode(of ? String(value) : DASH));
  if (of) {
    const s = document.createElement('span');
    s.textContent = fill('ruler_of', { total: of });
    el.append(s);
  }
}

// Правая колонка ведомости. У доказанного — код возврата, время и снимок;
// у названного готовым — курсив без единого числа: цифра рядом со словом
// читается как измерение, даже когда меряли только обещание.
function confirmation(r) {
  const box = document.createElement('div');
  box.className = 'r ' + r.state;
  if (r.state === 'proven') {
    box.append(document.createTextNode(
      fill('row_proven', { code: r.code }) +
      (r.at ? ' · ' + hhmm(r.at) : '')));
    if (r.sha) {
      const em = document.createElement('em');
      em.textContent = fill('row_snapshot', { sha: r.sha });
      box.append(em);
    }
    return box;
  }
  if (r.state === 'claimed') {
    box.append(document.createTextNode(W.copy.row_said));
    const em = document.createElement('em');
    em.textContent = W.copy.row_said_note;
    box.append(em);
    return box;
  }
  const say = { running: 'row_running', waiting: 'row_waiting',
                dropped: 'row_dropped', deferred: 'row_dropped' };
  box.append(document.createTextNode(W.copy[say[r.state]] || ''));
  return box;
}

function ledgerRow(r) {
  const row = document.createElement('div');
  row.className = 'vr' +
    (r.state === 'dropped' || r.state === 'deferred' ? ' dim' : '');
  const l = document.createElement('div');
  l.className = 'l' + (r.inferred ? ' inf' : '');
  // Слева — ДОСЛОВНО то, что сказал человек. Выведенное системой стоит рядом,
  // но помечено: чужая догадка, поданная как просьба человека, — самая тихая
  // подмена, какая тут возможна.
  l.append(document.createTextNode(
    r.inferred ? (r.basis || r.quote) : (r.quote ? '«' + r.quote + '»'
                                                 : r.basis)));
  const em = document.createElement('em');
  em.textContent = [r.id, r.inferred ? W.copy.inferred_prefix : '',
                    r.parent ? fill('serves', { parent: r.parent }) : '']
                   .filter(Boolean).join(' · ');
  l.append(em);
  row.append(l, confirmation(r));
  return row;
}

function drawLedger(s) {
  $('ledgerrub').textContent = W.copy.ledger_rubric;
  $('ledgerleft').textContent = W.copy.ledger_left;
  $('ledgerright').textContent = W.copy.ledger_right;
  $('ledgertotalrub').textContent = W.copy.ledger_total_rubric;
  const f = s.facts || {}, rows = f.ledger || [], t = f.totals || {};
  const box = $('ledger');
  box.replaceChildren();
  for (const r of rows) box.append(ledgerRow(r));
  if (!rows.length) {
    const p = document.createElement('p');
    p.className = 'more';
    p.textContent = W.copy.ledger_empty;
    box.append(p);
  }
  $('ledgertotal').textContent = rows.length ? fill('ledger_total', {
    proven: t.proven || 0, said: t.claimed || 0, running: t.running || 0,
    waiting: t.waiting || 0,
    dropped: (t.dropped || 0) + (t.deferred || 0) }) : '';
  $('ledgermore').textContent =
    (f.ledger_total && f.ledger_total > rows.length)
      ? fill('ledger_more', { shown: rows.length, total: f.ledger_total }) : '';
}

// Лента «что произошло»: только события, у которых отметку времени поставил
// код. Пересказ по памяти сюда попасть не может — ему неоткуда взять время.
function drawFeed(s) {
  $('feedrub').textContent = W.copy.feed_rubric;
  const box = $('feed');
  box.replaceChildren();
  const events = (s.facts || {}).events || [];
  if (!events.length) {
    const p = document.createElement('p');
    p.className = 'more';
    p.textContent = W.copy.feed_empty;
    box.append(p);
    return;
  }
  for (const e of events) {
    const row = document.createElement('div');
    row.className = 'ev' + (e.key === 'proven' ? ' proven' : '');
    const t = document.createElement('span');
    t.className = 't';
    t.textContent = hhmm(e.at);
    const what = document.createElement('span');
    what.textContent = fill('event_' + e.key, { name: e.name, code: e.code });
    const mark = document.createElement('span');
    mark.className = 'r';
    mark.textContent = e.key === 'proven' ? W.copy.mark_proved
      : (e.key === 'claimed' ? W.copy.mark_unproved
      : (e.key === 'snapshot' ? fill('mark_snapshot', { sha: e.sha }) : ''));
    row.append(t, what, mark);
    box.append(row);
  }
}

// Полоса «от тебя» — единственный светящийся элемент и единственная фраза:
// ход, долг и «ничего не нужно» — три ответа на один и тот же вопрос «что от
// меня», а не три отдельные строки. Раньше «ничего не нужно» и «долга нет»
// стояли рядом и повторяли одно и то же разными словами — здесь решает
// приоритет: ход важнее долга, долг важнее честного «ничего».
function drawYou(s) {
  $('yourub').textContent = W.copy.you_rubric;
  const ph = s.phase || {};
  const debt = debtTotal(s);
  const mine = ph.owner === 'человек';
  let phrase;
  if (mine) {
    // Ход у человека — самое срочное; незакрытый долг дописывается следом,
    // а не отдельной строкой, если он вообще есть.
    phrase = W.copy.you_turn + (ph.detail ? ': ' + ph.detail : '') +
             (debt.total ? ' · ' + debt.phrase : '');
  } else if (debt.total) {
    phrase = debt.phrase;
  } else {
    // Ничего не нужно говорится, только когда действительно ничего: ни хода,
    // ни незакрытого долга.
    phrase = W.copy.you_nothing;
  }
  $('youv').textContent = phrase;
  $('you').className = 'you' + (mine || debt.total ? ' on' : '');
}

// Мелкий шрифт: оговорки поимённо, последняя проверка, снимки и напоминание,
// откуда взялись числа. Это не украшение — здесь живёт всё, что человек
// узнаёт слишком поздно, если об этом не написать.
function drawFine(s) {
  const f = s.facts || {}, d = s.debt || {};
  const items = [...(d.stub || []), ...(d.env || []), ...(d.assumption || [])];
  $('finerub').textContent = fill('fine_rubric', { n: items.length });
  $('finev').textContent = items.length ? items.join(' · ') : W.copy.fine_none;
  const v = f.verify;
  $('checkline').textContent = v
    ? fill('check_last', { code: v.exit_code }) +
      (v.tests === null || v.tests === undefined
        ? '' : fill('check_tests', { tests: v.tests }))
    : W.copy.check_none;
  const c = f.checkpoints;
  $('snapsline').textContent = (c && c.count && c.last)
    ? fill('snaps_line', { count: c.count, sha: c.last['sha'],
                           name: c.last['этап'] || '' })
    : W.copy.snaps_none;
  $('finenote').textContent = W.copy.fine_note;
  $('where').textContent = W.copy.where_files;
}

// Отрисовка одного состояния. Откуда оно взялось — снимок из файла или ответ
// живого сервера — сказано вторым доводом и попадает на экран словами: снимок,
// показанный как свежее, врал бы молча.
function draw(s, live) {
  SEEN = { s: s, live: live };

  const ph = s.phase || {};
  $('proj').textContent = s.project || W.copy.project_unnamed;
  $('who').textContent = ph.owner ? (W.roles[ph.owner] || ph.owner)
                                  : W.copy.turn_unknown;
  $('who').dataset.owner = ph.owner || '';
  $('means').textContent = W.phases[ph.name] || '';
  $('detail').textContent = ph.detail || '';
  // Основание вывода. Панель, объявляющая этап без причины, — оракул: угадала
  // или нет, проверить нечем. С причиной человек видит, из чего это следует.
  $('why').textContent = ph.why ? W.copy.why_prefix + ' ' + ph.why : '';
  $('since').textContent = ph.since ? W.copy.here_for + ' ' + ago(ph.since) : '';
  // Долгое ожидание на человеке — не «идёт», а «забыли». В монохроме это
  // делается яркостью: строка становится белой вместо приглушённой.
  $('since').className = 'since' + (ph.owner === 'человек' && ph.since &&
    (Date.now() - new Date(ph.since)) > STALE_MS ? ' stale' : '');
  // Держатели «система» и «исполнитель»: тот же обрыв — упавшая сессия,
  // уснувший ноутбук — раньше тревогу не поднимал вовсе, она умела
  // срабатывать только на ожидании человека. Свой порог (STALE_RUN_MS): их
  // работа в норме идёт часами без единой записи, и порог человека звонил бы
  // на каждой обычной фазе. Свой текст (stale_run), а не «ждём давно»: там
  // ждут человека, здесь — нет, и слово «ждём» было бы не про то. Утверждать
  // «умер» текст всё равно не имеет права — система не отличает обрыв от
  // «долго думает», и словарь называет только то, что известно: давно не
  // менялось.
  if ((ph.owner === 'система' || ph.owner === 'исполнитель') && ph.since &&
      (Date.now() - new Date(ph.since)) > STALE_RUN_MS) {
    $('since').className += ' stale stale-run';
  }

  // Режим участия — теми же словами, какими человек его назвал. Номер
  // ступени тут не показывается намеренно: он ничего не сообщает тому, кто
  // выбирал словами, а не по таблице.
  const md = s.mode || {};
  const mw = W.modes[md.mode], dw = W.depths[md.depth];
  $('badges').hidden = !(mw && dw);
  $('modebadge').textContent = mw ? W.copy.mode_rubric + ': ' + mw : '';
  $('depthbadge').textContent = dw ? W.copy.depth_rubric + ': ' + dw : '';

  drawPhases(s, ph.name);

  const tasks = Object.values(s.waves || {}).flat();
  const running = tasks.filter(t => t.status === 'running').length;

  // Панель обязана сама заметить, что разошлась с работой: код пишут, а этап
  // показан другой. Ровно это и случилось 17.08 — этап остался на дизайне,
  // пока помощник уже писал страницу, и панель уверенно показывала неправду.
  const off = running > 0 && ph.name && ph.name !== BUILD_PHASE;
  $('mismatch').hidden = !off;
  $('mismatch').textContent = off ? W.copy.mismatch : '';

  // Размер работы нашей шкалой: человеку нужен масштаб, а не единица
  // измерения. Пусто, пока частей нет, — «полка» на нуле частей была бы
  // утверждением о работе, которой ещё не существует.
  const p = (s.facts || {}).parts || {};
  $('sizebadge').textContent = p.size
    ? fill('size_chip', { size: W.copy[p.size], n: p.total }) : '';

  drawNumbers(s);
  drawRulers(s, tasks);
  const rest = drawEta(tasks);
  drawSay(s, tasks, rest);
  drawLedger(s);
  drawCoverage(s);
  drawFeed(s);
  drawYou(s);
  drawFine(s);
  $('updated').textContent =
    (s.updated ? W.copy.updated.replace('{ago}', ago(s.updated)) : '') +
    (live ? '' : W.copy.from_snapshot);
}

// Снимок состояния, вписанный в саму страницу. Ради него панель и переживает
// смерть сервера: файл, открытый двойным кликом через месяц, показывает то,
// что было в последнюю запись, а не пустые полосы.
function snapshot() {
  const el = $('snapshot');
  if (!el) return null;
  try { return JSON.parse(el.textContent); } catch (e) { return null; }
}

async function tick() {
  // Живое состояние ложится ПОВЕРХ снимка, а не вместо него. Сервер жив —
  // цифры свежие; сервер мёртв или страницу открыли с диска — остаётся снимок,
  // и это сказано, а не показано молча как «сейчас».
  let fresh = null;
  try { fresh = await (await fetch('state.json?' + Date.now())).json(); }
  catch (e) { fresh = null; }
  if (fresh) { draw(fresh, true); return; }
  const kept = snapshot();
  if (kept) { draw(kept, false); return; }
  $('updated').textContent = W.copy.unreadable;
}

function firstLang() {
  // Выбор человека помнится. Помнить его негде, когда файл открыт с диска:
  // браузер там запрещает хранилище и бросает прямо на чтении. Отказ ловится
  // и ничего не значит — язык просто вернётся к умолчанию.
  try {
    const kept = localStorage.getItem('superstack-lang');
    if (kept === 'ru' || kept === 'en') return kept;
  } catch (e) { /* хранилище недоступно — не поломка */ }
  // Язык браузера учитывается только если английские надписи в файле ЕСТЬ:
  // пустой словарь дал бы страницу без единой подписи вместо русской.
  const en = WORDS.en && WORDS.en.copy && WORDS.en.copy.title;
  if (!en) return 'ru';
  // Записанный в прогоне язык важнее языка браузера: его выбрал сам человек
  // первой же фразой, а браузер мог достаться ему вместе с компьютером.
  // Отчёт и ответ на «где я» берут ту же запись, и панель обязана совпадать
  // с ними — экран на одном языке и итог на другом читаются как поломка.
  const s = snapshot();
  if (s && (s.lang === 'ru' || s.lang === 'en')) return s.lang;
  return (navigator.language || '').toLowerCase().indexOf('en') === 0
    ? 'en' : 'ru';
}

function setLang(code) {
  if (!WORDS[code] || !WORDS[code].copy) return;
  LANG = code;
  W = WORDS[code];
  document.documentElement.lang = code;
  // Надстрочник собирается здесь, а не стоит в разметке: иначе название
  // страницы осталось бы на языке сборки, каким бы ни был выбор читателя.
  $('eyebrow').textContent = BRAND + ' · ' + W.copy.title;
  for (const b of document.querySelectorAll('#lang button')) {
    b.setAttribute('aria-pressed', String(b.dataset.lang === code));
  }
  try { localStorage.setItem('superstack-lang', code); } catch (e) { /* см. выше */ }
  // Перерисовываем уже показанное, а не ждём следующего опроса: три секунды
  // смеси двух языков читаются как поломка страницы.
  if (SEEN) draw(SEEN.s, SEEN.live);
}

for (const b of document.querySelectorAll('#lang button')) {
  b.addEventListener('click', () => setLang(b.dataset.lang));
}

// Первый кадр — из снимка и СРАЗУ, не дожидаясь ответа сервера: иначе файл,
// открытый с диска, стоял бы пустым до первого промаха запроса.
const kept = snapshot();
if (kept) draw(kept, false);
setLang(LANG);
tick(); setInterval(tick, 3000);
</script>
</body></html>
"""


def state_with_phase(run_dir: Path) -> dict:
    """Состояние прогона с этапом, вычисленным по следам на диске.

    Записанный этап остаётся, но уходит на второй план: из него берётся только
    подпись, и только когда записанное и вычисленное согласны. Подпись от
    ДРУГОГО этапа — самая тихая форма вранья: заголовок верный, объяснение
    под ним от прошлого часа, и заметить это нельзя.
    """
    state = derive_phase.read_state(run_dir)
    got = derive_phase.derive(run_dir)
    was = state.get("phase") or {}
    same = was.get("name") == got["name"]
    state["phase"] = {
        "name": got["name"],
        "owner": got["owner"],
        "detail": was.get("detail", "") if same else "",
        "why": got["why"],
        # Отметка времени сохраняется, пока этап тот же: иначе «идёт уже»
        # обнулялось бы каждые три секунды и застрявший этап выглядел бы
        # вечно свежим — то есть ровно наоборот.
        "since": was.get("since") if same else None,
    }
    # Режим участия берётся из записи автономии, а не заводится второй раз
    # рядом: два места, где записано «как мы работаем», расходятся молча, и
    # панель начинает показывать не то, о чём договорились.
    state["mode"] = autonomy.badges(run_dir.parent)
    return with_facts(state, run_dir)


def with_facts(state: dict, run_dir: Path) -> dict:
    """То же состояние плюс числа, посчитанные по файлам на диске.

    Считает НЕ панель: числа собирает `progress.py`, читая ход стройки,
    требования человека, последнюю проверку и снимки. Разметке остаётся
    только показать готовое — иначе счёт разъехался бы на две редакции, живую
    (сервер) и снимочную (файл), и одна из них молча врала бы.

    Факты не ложатся в state.json: они ВЫВОДЯТСЯ и обязаны пересчитываться на
    каждом показе. Записанное число живёт своей жизнью и переживает данные,
    из которых его получили, — ровно тот отказ, против которого вся страница.
    """
    return {**state, "facts": progress.панель_факты(state, run_dir, PHASES)}


def _words() -> dict:
    """Оба языка, вшитые в файл, по одним и тем же ключам.

    Английская пара лежит в том же словаре, а не вторым файлом рядом: два
    файла разъезжаются молча — в один надпись добавили, во второй забыли.
    Пустая пара — не тихая деградация: панель останется на русском и скажет
    об этом вслух.
    """
    words = plain_ru.load()
    en = plain_ru.english(words)
    if not en.get("copy"):
        print("НЕ НАШЁЛ английские надписи в словаре — панель останется "
              "только на русском", file=sys.stderr)
    return {
        "ru": {
            "roles": words["roles"]["map"],
            "phases": words["phases"]["map"],
            "modes": words["modes"]["map"],
            "depths": words["depths"]["map"],
            "statuses": words["statuses"]["map"],
            "copy": words["copy"]["map"],
        },
        "en": {
            # Названия этапов отдельным словарём: ключ этапа остаётся русским
            # (он же ключ состояния), а на экран идёт перевод.
            "names": en.get("names", {}),
            "roles": en.get("roles", {}),
            "phases": en.get("phases", {}),
            "modes": en.get("modes", {}),
            "depths": en.get("depths", {}),
            "statuses": en.get("statuses", {}),
            "copy": en.get("copy", {}),
        },
    }


def _snapshot_block(state: dict) -> str:
    """Снимок состояния куском страницы, вместе с границами.

    Данные пишет модель — имена задач, подписи этапов, — и однажды туда
    приедет угловая скобка. Внутри `<script>` она закрыла бы тег и увела
    остаток снимка в разметку, поэтому скобки и амперсанд уезжают экранированными:
    для разбора это тот же текст, для разметки — уже не тег.
    """
    body = (json.dumps(state, ensure_ascii=False)
            .replace("&", "\\u0026").replace("<", "\\u003c")
            .replace(">", "\\u003e"))
    return (SNAPSHOT_BEGIN
            + '<script type="application/json" id="snapshot">'
            + body + "</script>" + SNAPSHOT_END)


def _with_snapshot(html: str, state: dict) -> str:
    """Заменить снимок в готовой странице, не трогая всё остальное."""
    a = html.find(SNAPSHOT_BEGIN)
    b = html.find(SNAPSHOT_END)
    if a < 0 or b < a:
        raise ValueError("в странице нет границ снимка")
    return html[:a] + _snapshot_block(state) + html[b + len(SNAPSHOT_END):]


def _without_snapshot(blob: bytes) -> bytes:
    """Страница без снимка — то, что у неё неизменно.

    Нужно там, где две копии страницы сравниваются на тождество: снимок
    переписывается на каждой записи состояния, и сравнение вместе с ним
    объявляло бы «это не наша панель» всякий раз, когда рядом идёт работа.
    """
    a = blob.find(SNAPSHOT_BEGIN.encode())
    b = blob.find(SNAPSHOT_END.encode())
    if a < 0 or b < a:
        return blob
    return blob[:a] + blob[b + len(SNAPSHOT_END):]


def _write_atomically(p: Path, text: str) -> None:
    """Запись целиком или никак.

    Панель переписывается на каждой записи состояния, и её же в этот момент
    может читать браузер или сервер. Обрезанный файл — это страница без
    закрывающего тега, то есть пустой экран без единого признака поломки.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def read_state(run_dir: Path) -> dict:
    """Состояние с диска как есть. Нечитаемое — пусто, и это видно на экране."""
    try:
        return json.loads((run_dir / "state.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def embed_state(run_dir: Path, state: dict) -> Path:
    """Вписать снимок состояния в страницу — без сборки всей страницы заново.

    Это и есть то, ради чего панель переживает смерть сервера. Читать
    соседний файл с диска браузер не даёт, поэтому состояние едет ВНУТРИ
    страницы: открытый двойным кликом файл показывает последнюю запись сразу,
    через месяц и на другой машине.

    Страницы ещё нет — она собирается здесь же: снимок, которому некуда лечь,
    молча пропал бы, а первую запись состояния делают раньше первой сборки.
    """
    p = run_dir / PANEL
    # Снимок едет с фактами: файл, открытый без сервера, обязан показывать те
    # же числа, что и живая страница. Иначе панель без сервера показывала бы
    # пустые места там, где у соседа по вкладке проценты и ведомость.
    state = with_facts(state, run_dir)
    try:
        html = p.read_text("utf-8") if p.is_file() else _page()
        html = _with_snapshot(html, state)
    except ValueError:
        # Страница собрана прежней версией, без границ снимка. Пересобираем:
        # оставить её как есть значило бы тихо не вписывать снимок вовсе.
        html = _with_snapshot(_page(), state)
    _write_atomically(p, html)
    return p


def _page() -> str:
    """Страница со словами, но без данных. Снимок кладётся отдельно.

    Словарь вшивается при сборке, а состояние читается на лету: правка
    формулировки — это правка одного словаря и одна пересборка, а не выпуск
    новой версии продукта.
    """
    words = plain_ru.load()
    return (_HTML
                 .replace("__CSS__", SYSTEM_CSS)
                 .replace("__BRAND__", BRAND)
                 .replace("__PHASES__", json.dumps(PHASES, ensure_ascii=False))
                 .replace("__STALE__", str(STALE_MINUTES))
                 .replace("__STALE_RUN__", str(STALE_MINUTES_RUN))
                 .replace("__WAIT_LONG__", words["copy"]["map"]["waiting_long"])
                 .replace("__STALE_RUN_SUFFIX__", words["copy"]["map"]["stale_run"])
                 .replace("__BRAND_JSON__",
                          json.dumps(BRAND, ensure_ascii=False))
                 .replace("__LANG_RU__", words["copy"]["map"]["lang_ru"])
                 .replace("__LANG_EN__", words["copy"]["map"]["lang_en"])
                 .replace("__BUILD_PHASE__",
                          json.dumps(BUILD_PHASE, ensure_ascii=False))
                 .replace("__SNAPSHOT__", _snapshot_block({}))
                 .replace("__WORDS__",
                          json.dumps(_words(), ensure_ascii=False)))


def write_panel(run_dir: Path) -> Path:
    """Собрать страницу целиком и положить в неё свежий снимок состояния.

    Снимок кладётся ЗДЕСЬ ЖЕ, а не следующей записью: пересобранная страница
    без него показала бы пустые полосы всякому, кто откроет её с диска, — то
    есть «ничего не происходит» вместо «нечего показывать».

    Разметка собирается ЗАНОВО, а не подхватывается с диска. Запись состояния
    правит в готовой странице только снимок — это дёшево и делается десятки
    раз за прогон; сборка обязана перекрыть и саму страницу, иначе панель,
    начатая прежней версией, остаётся на прежней версии навсегда, а на диске
    лежит файл, который выглядит свежим и не является им.
    """
    p = run_dir / PANEL
    _write_atomically(p, _with_snapshot(
        _page(), with_facts(read_state(run_dir), run_dir)))
    return p


def panel_is_live(run_dir: Path, port: int) -> tuple[bool, str]:
    """Отвечает ли НА ЭТОМ порту именно panel.html из run_dir, а не сосед.

    Раньше ссылку печатали ДО того, как `--serve` пытался занять порт, а сама
    попытка шла отвязанным процессом (`nohup … &`) с выводом в /dev/null —
    если порт был занят чужим приложением, человек получал рабочую на вид
    ссылку на мёртвый сервер в первые минуты знакомства с продуктом.

    Просто «получили ответ 200» здесь недостаточно: чужой процесс на том же
    порту вполне может отвечать 200 на что угодно — это и есть тот самый
    занятый порт, который нужно поймать, а не спутать с живой панелью.
    Доказательство — побайтовое совпадение с файлом, который панель только
    что записала на диск: только оно значит, что отвечает НАША панель.
    Снимок состояния из сравнения вычитается: он переписывается на каждой
    записи хода работ, и вместе с ним «это не наша панель» выпадало бы всякий
    раз, когда рядом идёт работа. Неизменной части страницы для тождества
    достаточно — сосед на том же порту не отдаёт нашу разметку.

    Опрос повторяется в пределах CHECK_BUDGET_SECONDS: сервер подняли только
    что и отвязанно, и секунда-другая на старт Python — это норма, а не повод
    сразу объявлять порт занятым.
    """
    panel_path = run_dir / PANEL
    if not panel_path.is_file():
        return False, f"на диске нет {PANEL} — панель ещё не собрана"
    expected = panel_path.read_bytes()
    url = f"http://{HOST}:{port}/{PANEL}"
    deadline = time.monotonic() + CHECK_BUDGET_SECONDS
    others_answer = False
    while True:
        try:
            with urllib.request.urlopen(url, timeout=CHECK_TIMEOUT_SECONDS) as resp:
                if _without_snapshot(resp.read()) == _without_snapshot(expected):
                    return True, "отвечает"
                others_answer = True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            break
        time.sleep(CHECK_POLL_SECONDS)
    if others_answer:
        return False, (f"на порту {port} отвечает не эта панель — порт занят "
                       "чем-то другим")
    return False, f"порт {port} не отвечает — сервер не поднялся или ещё не успел"


def open_in_browser(path: Path) -> tuple[bool, str]:
    """Открыть файл тем, чем открывает система, и честно сказать про отказ.

    Ссылка, которую человек должен открыть сам, открывается им ровно в
    половине случаев — а панель, которую не открыли, это файл на диске, а не
    панель. Поэтому открывает система: macOS `open`, Linux `xdg-open`,
    Windows `os.startfile`.

    Успех НЕ печатается заранее: `open` может не найтись, `xdg-open` — не
    стоять вовсе, а на машине без графической оболочки открывать нечем. Тогда
    возвращается отказ с причиной, ссылку называют словами, и прогон идёт
    дальше — открытое окно не условие работы.
    """
    import platform
    import subprocess

    custom = os.environ.get(OPENER_ENV)
    system = platform.system()
    if not custom and system == "Windows":
        try:
            os.startfile(str(path))                          # noqa: S606
        except (OSError, AttributeError) as e:
            return False, f"система не открыла файл: {e}"
        return True, "открыто"
    opener = custom or ("open" if system == "Darwin" else "xdg-open")
    try:
        r = subprocess.run([opener, str(path)], capture_output=True, text=True,
                           timeout=OPEN_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{opener} не запустился: {e}"
    if r.returncode != 0:
        why = (r.stderr or r.stdout or "").strip().splitlines()
        return False, (f"{opener} вернул {r.returncode}"
                       + (f": {why[0][:120]}" if why else ""))
    return True, "открыто"


def announce(p: Path, port: int) -> None:
    """Единственное место, печатающее ссылку.

    Вызывается только тогда, когда её уже не стыдно давать: либо панель
    просто записана на диск без --serve (сервер поднимет кто-то другой), либо
    порт только что успешно привязан, либо --check только что подтвердил, что
    на порту отвечает именно эта панель.
    """
    print(f"панель: {p}", file=sys.stderr)
    print(f"открыть: http://localhost:{port}/{PANEL}", file=sys.stderr)
    print(json.dumps({"panel": str(p), "url": f"http://localhost:{port}/{PANEL}"},
                     ensure_ascii=False))


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    plain = [a for a in argv if not a.startswith("--")]
    if not plain:
        print("вызов: live_panel.py <каталог .superstack> "
              "[--serve [порт] | --check [порт] | --open]", file=sys.stderr)
        return 3
    run_dir = Path(plain[0]).resolve()
    if not run_dir.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {run_dir}", file=sys.stderr)
        return 3
    port = int(plain[1]) if len(plain) > 1 else DEFAULT_PORT

    if "--check" in argv:
        # Отдельная синхронная команда — снаружи `--serve`, который в прогоне
        # всегда отвязан (`nohup … &`) и весь свой вывод роняет в /dev/null.
        # Только код возврата ЭТОЙ команды и решает, давать ли ссылку.
        ok, why = panel_is_live(run_dir, port)
        if not ok:
            print(f"НЕ УДАЛОСЬ: {why}", file=sys.stderr)
            return PORT_BUSY
        announce(run_dir / PANEL, port)
        return 0

    if not (run_dir / "state.json").is_file():
        # Панель без состояния показала бы пустые полосы — то есть «ничего не
        # происходит» вместо «нечего показывать». Это разные утверждения.
        print("НЕ УДАЛОСЬ: нет state.json — панели нечего читать", file=sys.stderr)
        return 2

    p = write_panel(run_dir)

    if "--open" in argv:
        # Открытие — отдельная команда, а не довесок к `--serve`: тот уходит в
        # фон и не может ни дождаться открывателя, ни вернуть его код.
        opened, why = open_in_browser(p)
        announce(p, port)
        if not opened:
            print(f"НЕ ОТКРЫЛ: {why}. Скажи человеку ссылку словами — "
                  "прогон это не останавливает", file=sys.stderr)
            return OPEN_FAILED
        print(f"открыл файл: {p}", file=sys.stderr)
        return 0

    if "--serve" not in argv:
        announce(p, port)
        return 0

    import functools
    import http.server
    import socketserver

    class Fresh(http.server.SimpleHTTPRequestHandler):
        """Отдаёт всегда свежее — и этап считает сам.

        Две вещи, каждая из живого прогона.

        Первая: обычный статический сервер отвечает «не менялось» по дате
        файла, и браузер оставляет прежнюю страницу. Проверено вживую —
        панель на диске уже новая, сервер отдаёт новую, а на экране
        прежняя. Человек смотрит на устаревшее и не может об этом узнать.

        Вторая, и она важнее: этап раньше приходил из записи, которую
        делают отдельной командой. Команду забывали — за один вечер
        дважды, — и панель уверенно показывала не тот этап. Здесь состояние
        отдаётся с этапом, ВЫЧИСЛЕННЫМ по следам на диске: панель
        переключается сама, без чьей-либо дисциплины.
        """

        def end_headers(self):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            super().end_headers()

        def do_GET(self):                                  # noqa: N802
            if self.path.split("?")[0].rstrip("/") not in ("/state.json",):
                return super().do_GET()
            body = json.dumps(state_with_phase(run_dir),
                              ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # тишина: опрос идёт раз в 3 секунды
            pass

    handler = functools.partial(Fresh, directory=str(run_dir))
    # allow_reuse_address: панель перезапускают часто, и «порт занят» после
    # каждого перезапуска сделало бы её неудобной ровно настолько, чтобы ею
    # перестали пользоваться.
    socketserver.TCPServer.allow_reuse_address = True
    try:
        # Порт привязывается ДО первой печати ссылки — раньше их порядок был
        # обратным, и человек получал ссылку раньше, чем становилось известно,
        # жив ли сервер вообще.
        httpd = socketserver.TCPServer((HOST, port), handler)
    except OSError as e:
        # Соседний свободный порт здесь НЕ берём, хотя это и решило бы неудобство
        # само по себе. Во-первых, тихая замена порта разошлась бы с портом,
        # жёстко вписанным в фазу `0-завести.md`, и ссылка, которую отдаёт агент,
        # снова стала бы неверной — ровно тот же отказ, только на шаг дальше.
        # Во-вторых, занятый порт может значить, что жива ПРОШЛАЯ панель этого
        # же прогона — переезд на соседний порт тогда молча оставил бы две
        # панели вместо одной, и обе выглядели бы легитимными. Явный отказ
        # дешевле: человек тратит минуту на освобождение порта, а не гадает,
        # какая из двух ссылок настоящая.
        print(f"НЕ УДАЛОСЬ: порт {port} занят, панель не поднялась ({e})",
              file=sys.stderr)
        return PORT_BUSY

    announce(p, port)
    with httpd:
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
