# Независимая приёмка этапа 2 — R2

Дата: **26.10.2026**, Europe/Moscow. Полная повторная проверка по запросу «перепроверь этап 2». Проверка начата в12:22 UTC / 15:22 МСК; времена отдельных опытов приведены ниже. Это новая проверка всего этапа, а не только четырёх исправлений. Исходный отчёт R1 сохранён ниже без изменения байтов.

## 1. Вердикт и проверенная версия

**ЗАБЛОКИРОВАН внешними обстоятельствами.**

Все четыре программных недостатка R1 **S2-01–S2-04 закрыты**. Новых подтверждённых внутренних дефектов в проверенной версии не найдено. Полный набор **377/377 тестов PASS**, независимые отрицательные сценарии исправлений пройдены, реальные TSM200→304 воспроизведены. До полной приёмки остаются обязательные REAL-части **T2-04 / S2-V01** (несколько естественных обновлений) и **T2-07 / S2-V03** (реальный длительный перерыв/сон и восстановление; backfill — только для разрешённого источника). Синтетические проверки этих механизмов проходят, но соответствующих реальных наблюдений нет.

«Заблокирован» здесь означает недостаток внешних наблюдений для полного вердикта, **не обнаруженную неисправность импортёра**. Переписывать код ради этой метки не требуется. UE уже является условной возможностью по текущему плану; его отсутствие само по себе не новый программный дефект. Согласованный пользователем перенос S1-V01–07 по D1 остаётся в силе и не является причиной этого вердикта. Отдельного переноса новых S2-V01/S2-V03 в доступной истории нет: разработчик прямо сохранил их обязательность.

- Git HEAD: `13be8666b92e9dd17ee11f614025a04662e9ed68`.
- Рабочее дерево не зафиксировано этим коммитом: README изменён; исходники, tests, docs, handoff, tools и pyproject не отслеживаются Git. Проверена именно рабочая версия, а не чистое дерево HEAD.
- `docs/stage2-package-manifest.sha256`: SHA-256 **`71114ceb1ad9a49fd014f53e7b2039908dd83ca2f0c827c7388fa29407d06876`**, **107/107** записей совпали при начале проверки.
- Архив R1 `docs/research/baselines/stage-2-r1.zip`: SHA-256 `c79239bc674205ff44f4754c943f66918afdb250d2cc64fd3282d2e387a0744f`; внутренний content manifest **94/94 PASS**. Исходному manifest R1 соответствуют 93/94: единственное отличие — собственный реестр аудитора, обновлённый после исходной упаковки. Это объяснено capture и не означает редактирование аудита разработчиком.
- Зафиксированы и повторно проверены **192 файла вне собственных отчётов**, включая рабочие базы; изменённых/исчезнувших нет. Секретные файлы исключены из чтения и хеширования. Исходники, документы и результаты тестов разработчика не исправлялись.
- Прежний отчёт R1: **57 146 bytes**, SHA-256 `b8bfe328eb601f99137546939fb77754a0cf819c967a378ca575ed8591335895`. Его полная история ниже. Отчёты этапов 0 и 1 не изменены.

После обновления собственного реестра его hash закономерно отличается от manifest, снятого разработчиком до этого аудита. Manifest разработчика не переписывался.

## 2. Объём и критерии полной проверки

Прочитаны инструкции reviewer и PROJECT_CONTEXT, действующие решения README/architecture/data-model/implementation-plan/acceptance-tests/risks, текущий ответ разработчика и runbook. Сопоставлены новая ревизия и архив R1, проверены новые результаты и ссылки. Пересмотрены все рыночные компоненты: TSM/UE contracts, transport, queue/leases/budgets, source policy, clocks/freshness, storage/migration/retention/replay, resolver и CLI. Неизменённые исследования этапов 0–1 сопоставлены с прежними доказательствами и действующими изменениями источников; их старые утверждения о Blizzard не приняты за действующий сетевой маршрут. Выполнен весь набор, включая тесты этапа 1, а не только новые регрессии. Все **280 проверенных относительных ссылок** документов разработчика разрешаются.

| Критерий | Доказательство и результат R2 | Статус |
|---|---|---|
| T2-01 | `docs/acceptance-tests.md:48`, `market_worker.py:123`, transport tests: Blizzard/OAuth запрещён; получения ключей не было | SUPERSEDED по действующему решению |
| T2-02 | `market_undermine.py:240`, `tests/test_market_undermine.py`; схемы, history, depth, нулевая quantity, paid guards — DOC/SYN; live UE не вызывался | Условный OPEN S2-V02, не REAL PASS |
| T2-03 | Пять полных настоящих TSM CSV, `market_tsm.py:114`; minBuyout0→no quote, historical scalar, saleRate не вероятность сделки; tests PASS | PASS в доступном TSM-объёме |
| T2-04 | `market_freshness.py`, `market_storage.py:315`, freshness/revision tests; независимые N1/N2, REAL200/304; естественная cadence не измерена | SYN/повторы PASS; REAL OPEN S2-V01 |
| T2-05 | Worker/storage/TSM/UE tests: HTML, empty, malformed, partial, unknown schema не вытесняют last-good | PASS SYN |
| T2-06 | `market_http.py`, `market_storage.py:204`, `market_worker.py:138`; полный deadline, cleanup, exact validators, takeover, 429/backoff, очередь | PASS SYN/MOCK + REAL TSM200/304 |
| T2-07 | `tests/test_market_worker.py:180`, storage/replay/freshness tests; поздняя доступность/retention и restart проверены в моделируемом времени | SYN PASS; REAL OPEN S2-V03 |
| T2-08 | `market_common.py:97`, `market_undermine.py:165`; group/variant tests, реальные разные scopes TSM; общий upstream не независимое подтверждение | PASS реализованного разделения; UE membership REAL OPEN |
| T2-09 | Полные пять файлов, 56 318 строк, замеры ниже; рабочая БД 89 182 208 bytes, копия мигрирована без изменения входов | PASS выбранных TSM scopes; full-depth/multi-realm growth не проверен |
| T2-10 | `market_storage.py:382`, `:398`; expiry удаляет содержимое, поздняя reacquisition не восстанавливает старую available, replay явно unavailable | PASS SYN; длительный архив ограничен S2-V04 |
| T2-11 | REAL TSM без Authorization, loader spy=0 при синтетически неисправном неиспользуемом UE env; lazy-secret/CLI/config/http tests | PASS |
| T2-12 | `market_worker.py:212`, `tests/test_market_groups.py`; provider membership и конфликты без вымышленного numeric ID | DOC/SYN PASS; условный REAL OPEN S2-V02 |

Сумма строк TSM: 12 162 + 11 030 + 31 187 + 961 + 978 = **56 318**. Это размер пяти опубликованных наборов, не число уникальных вещей и не полнота всего аукциона/всей игры.

Проверена граница последующих этапов: база преобразований/экспорт CraftSim/экономика/виртуальные сделки/ИИ/UI не реализовывались и не требовались для этапа 2. Проект сохраняет полный обход согласованных корпусов всех Retail expansions, unknown PMF/coverage, runtime-сверку CraftSim, разделение наблюдения и исполнения, поздней информации и прошлых прогнозов, гипотез и причинности, shadow/контроль после promotion. Отсутствие Blizzard переносит discovery на разрешённые альтернативы с явными потерями; TSM CSV не заменяет глобальную базу преобразований. Эти планы не объявлены работающими возможностями.

## 3. Закрытие замечаний R1

| ID, прежняя критичность | Проверенные файлы и наблюдение | Решение |
|---|---|---|
| S2-01 HIGH | `market_freshness.py:26`, `market_storage.py:304`, `:321`, `:348`, `:429`; 41 новая freshness-регрессия. Независимый A09:00→B08:00→A09:00: accepted→source_regression_kept_last_good→unchanged, current id всё время 1, цена80. HTTP200 со scan2020: source_stale, capabilities=false при возрасте215146806s. Equal-time correction, unknown/future clocks, history, no-quote, mixed groups, retention watermark проверены | CLOSED R2 |
| S2-02 HIGH | `market_storage.py:204`, `:219`, `:329`, `:369`; `market_worker.py:105`. 200A(etag v1)→200B без headers→304: запрос не отправляет старый validator, результат unverified_not_modified_response/retry_scheduled, B не подменяется. Commit/validators атомарны; crash, parser revision и takeover регрессии PASS | CLOSED R2 |
| S2-03 MEDIUM | `market_http.py`, `tests/test_market_deadline.py:124`, `market_worker.py:192`. Production transport изолирован с общим monotonic budget; зависание до headers и при передаче body заканчивается остановкой child и удалением shared memory. Очередь после timeout/lease loss продолжается. Реальный transport успешно выполнил10 GET | CLOSED R2 |
| S2-04 MEDIUM | `market_worker.py:17`, `:25`, `:125`, `market_config.py:116`, `__main__.py:153`; disabled/policy-blocked UE/status/resolve не открывают секреты. Ошибка разрешённого UE локальна своему заданию. Независимый loader spy и REAL TSM при malformed synthetic env: 0 вызовов loader | CLOSED R2 |

В повторе старого N4 mock намеренно игнорирует socket timeout: теперь исключение возникает после второго чтения при искусственных58s, а не ложный успех после319s. **Это не замер production deadline в58s**: injected opener является синхронным тестовым интерфейсом. Ограничение действительно блокирующего кода подтверждают отдельные process/cleanup тесты; реальная сеть использовала production transport. Такой предел проверки не скрыт.

Сохранение `cache_available=true` при `usable=false` допустимо и документировано: локальное содержимое существует, но текущим рыночным входом оно не является. Для report дополнительно учитываются health и due time. Пороги8h/48h/6h — явно описанные операционные настройки проекта, не измеренная cadence или гарантия провайдера.

## 4. Оставшиеся недостатки доказательств

### S2-V01 — MEDIUM, внешний критерий полной приёмки: cadence не измерена

**Доказательство:** `docs/acceptance-tests.md:51`; `docs/stage-2-report.md:21`, `:121`; `docs/review-responses/stage-2-r1-response.md:157`. Наши пять HTTP200 в12:30UTC получили те же hash/source clocks, что R1 и финальный live capture разработчика. Немедленные304 подтверждают повторную проверку содержимого, но не несколько естественных обновлений. Старый переход commodity scan на10799s остаётся одним наблюдением, не измерением стабильных интервалов.

**Влияние:** нельзя обещать измеренную частоту поступления новых данных или выдавать пороги freshness за её экспериментальное подтверждение.

**Необходимое исправление:** получить датированную серию обычных обновлений действующего источника по фактически используемым типам dataset; сохранить source scan, receive/available, hash, интервалы опроса и пропуски. Различить upstream scan interval и наблюдаемое время публикации, не выдумывать точный SLA по редкому опросу. Длительное хранение raw ради этой проверки не требуется: соблюдать SourcePolicy. Альтернатива — отдельное явное решение пользователя принять подтверждённый объём с переносом этого опыта; это не REAL PASS.

### S2-V03 — MEDIUM, внешний критерий полной приёмки: реальный перерыв и восстановление не проверены

**Доказательство:** `docs/acceptance-tests.md:54`; `docs/stage-2-report.md:124`; `docs/review-responses/stage-2-r1-response.md:157`–159; `tests/test_market_worker.py:180` использует моделируемые часы/клиент. Реального сна/выключения компьютера и продолжительного offline с последующим разрешённым backfill в пакете нет.

**Влияние:** фактическое восстановление процесса/сети после такого перерыва не подтверждено, хотя логика gaps/leases/backoff/available проходит SYN.

**Необходимое исправление:** при обычном разрешённом перерыве зафиксировать состояние до/после, фактический gap, повтор/отсутствие дублей, новые receive/available, просроченный lease/retention и отказ использовать поздно полученный вход для старого решения. Не переводить ПК пользователя в сон самовольно. TSM не предоставляет history endpoint: его reacquisition не выдавать за UE backfill. Для условной UE-ветви опыт возможен только после разрешённых доступа/политики; иначе сохранить именно эту часть OPEN. Допустим отдельный явно согласованный перенос операционного опыта с точными ограничениями.

**Не открыты повторно как программные дефекты:** S2-V02 (UE-доступ/условия, membership/depth/history/paid) и S2-V04 (права архива/облака и более широкий рост storage). Они продолжают ограничивать зависимые будущие функции. Подтверждённого основания ослабить текущую policy в этом аудите не получено. Требовать UE-покупку, Blizzard credentials или реализацию этапов3–7 для закрытия этапа2 нельзя.

## 5. Воспроизведённые проверки

### Полный набор и версия среды

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -X utf8 -m unittest discover -s tests
```

Фактический stdout: **Ran 377 tests in 17.534s — OK**, exit0, без SKIP. Проверены Python3.13.7 и SQLite3.50.4 как у вызванного системного Python, так и у `.venv/Scripts/python.exe`; проект использует стандартную библиотеку. 68 добавленных регрессий не заменяют прежние309. Изменения старых тестов прочитаны: storage fixture теперь TSM, миграции воспроизводят настоящую старую схему; прежние требования не сняты ради зелёного набора.

Для адресного повторения исправлений:

```powershell
.venv/Scripts/python.exe -B -m unittest tests.test_market_freshness tests.test_market_freshness_integration tests.test_market_validators tests.test_market_deadline tests.test_market_lazy_secrets
```

Эта адресная команда приведена для воспроизведения; в аудите её тесты выполнены внутри полного suite. Повторный отдельный прогон не заявляется.

### Реальная сеть — собственный прогон аудитора

26.10.2026 **12:30:02.888–12:30:13.087 UTC**: production HttpClient→MarketWorker→SQLite **только в памяти**, действующая локальная конфигурация TSM EU/Soulflayer. Пять200 затем пять304, ровно5 snapshots и5 snapshot-bound validator rows, schema3, quick_check=ok, FK errors0. Authorization не отправлялся; loader spy=0; UE disabled; Blizzard запросов0. Неиспользуемая переменная UE была заменена только в процессе на синтетическую строку с newline; реальные секреты не читались.

| Dataset | Rows | Raw bytes | Normalized UTF-8 bytes | source_snapshot_at | SHA-256 |
|---|---:|---:|---:|---:|---|
| commodities | 12162 | 901725 | 6473291 | 1793007098 | `963ee30c408a754a84abfa3467d7b0c1001985dd8b7f1d00709c102080a6cb16` |
| realm_items | 11030 | 868751 | 5926763 | 1793007098 | `b7f2bc216c9f04b17275e3dd46a886cc99a0509b43a571b80872eb0d661bd05c` |
| region_items | 31187 | 2545285 | 17276599 | 1792890147 | `cb4abf46337a2e3907fec6f30322c03383900ad722da971ea988d220c87eb93f` |
| realm_pets | 961 | 73693 | 509050 | 1793007098 | `7fe46ad48ed44996544491657b08bd7caa3d99a329cdfb9df34d6f31bf9ab9e1` |
| region_pets | 978 | 77953 | 532839 | 1792890147 | `8854ef6934713d487150163c1af6b90b2077ba65995cc2359ce48ed055d5c240` |

Raw всего **4 467 407 bytes**, normalized **30 718 542 bytes**. Каждый HTTP200 занял0.603–1.254s; 304 —0.477–0.632s. Это пять выбранных опубликованных файлов, не нагрузочная проверка всех миров и не доказательство атомарной книги лотов. Source age после прогона:10 715s для fast datasets,127 666s для regional; последние допустимы по явной48h policy, а не потому, что только что скачаны.

Новые снимки получили собственные `received_at_ns/available_at_ns`; повтор304 их не омолодил. Например commodity receive=`1793017804125240600`, available=`1793017804299910700`, source=`1793007098`. Финальный capture разработчика отдельно прочитан и сопоставлен: те же размеры/counts/hashes, тоже5×200+5×304. Наш результат не подменён его JSON.

### Рабочая база, миграция и сохранность

Рабочая `.private/market.sqlite3` открыта SQLite URI `mode=ro`, `query_only=ON`; backup сделан **в память**. Исходная schema2,10 snapshots,89 182 208bytes. Копия мигрирована2→3: все raw/normalized/headers/received/available/expiry неизменны; все10 доступных raw SHA совпали; quick_check=ok, FK errors0,5 validator rows. Пять current snapshots повторно нормализованы текущим кодом: records,scope,clocks,capabilities,completeness совпали. Рабочая БД осталась schema2 с тем же hash; её нельзя считать уже обновлённой этим аудитом.

Архивы/JSON результаты разработчика проверялись чтением. Диагностический `market-status` на рабочей БД не запускался: он штатно мигрирует и обслуживает retention, что нарушило бы ограничение аудитора на запись. Поведение CLI проверено тестами на изолированных синтетических данных.

### Первичные источники

Повторно открыты [TSM Public Pricing Data](https://tradeskillmaster.com/public-data), [TSM Terms](https://tradeskillmaster.com/terms), [UE API](https://undermine.exchange/api.html) и [UE Terms](https://undermine.exchange/terms.html). TSM подтверждает copper, base species, upstream `updatedAt` и различную частоту fast/regional файлов. UE документирует отдельный ключ, points budget, отсутствие item metadata и дневную выборку по максимальному quantity; это не подтверждение успешного UE-запроса проекта. Конфликт условий не снят; четырёхчасовой кэш не назван лицензионным сроком владельца.

Выборочно проверены локальные первичные файлы NB-02/03/04/06 из `docs/research/stage-2-no-blizzard-alternatives.md:60`–75: SHA LibRealmInfo, ItemInfoCache, ItemInfoSerializer и CraftSim itemRecipeDB совпали с документом. Whitelist-чтение active build: EU,12.1.5.70575. Это подтверждает исследованную локальную версию, а не сегодняшнюю полную топологию серверов или выполненный расчёт CraftSim.

## 6. Унаследованные открытые проверки

Повторное чтение разрешённых SV: **12:29:37 UTC**, safe parser без Lua execution. Все четыре hash совпали с R1; hash и mtime каждого файла не изменились в ходе чтения:

| Источник | Bytes | SHA-256 |
|---|---:|---|
| GX | 662 | `025bc76a6434d5634177ea37b9d1e45224c6f919c7cd800da85f44adf6cf0607` |
| BagSync | 33 913 | `1aee395169f9b55a042fcbd3cfa5a7dd2b733da6c2b0b2e054ff89da5003c352` |
| TSM | 1 567 334 | `ede4fe92e33054d359722c2c7c7759a68437fcfc84e4a20bd7ba466ffe94ba85` |
| CraftSim | 269 336 | `d52782702e110c4e94bed540bee0f3269906d1cd9e2ed96908193d2a500e78b6` |

| ID | Факт повторной проверки R2 | Статус / предел |
|---|---|---|
| S1-V01 | TSM30 rows:4 Auction buys,4 Repair Bill,22 Vendor sales; Auction sales0 | OPEN; real net/unit/AH cut продажи не доказаны |
| S1-V02 | Income/cancelled/expired0, новых transfer/refund нет | OPEN; не подтверждён полный cashflow/PnL |
| S1-V03 | Bytes те же; новых двух состояний одного изменившегося агрегата нет | OPEN; новые разные строки не доказывают aggregate growth |
| S1-V04 | 5 cash observations,0 conflicts, UI-сверки не появилось | OPEN; абсолютный текущий NAV unknown |
| S1-V05 | BagSync244 rows, вход идентичен R1, новых наблюдений переноса/полноты нет | OPEN; прежние container/stale пределы сохраняются |
| S1-V06 | В реальной конфигурации1 account_scope | OPEN; реального multi-license опыта нет |
| S1-V07 | Те же варианты/unknown tradeability, CraftSim engine_evaluation_available=false | OPEN; цены TSM не доказывают binding или доступность изготовления |

Рабочая asset DB прочитана read-only и скопирована в память: исходная schema2, после миграции копии импорт дал2→30 Ledger rows; повтор четырёх источников `unchanged`. PnL/current NAV остались unknown. Рабочие asset DB и игровые файлы не обновлялись. Непустой Ledger подтверждён, **полноценная финансовая интеграция не объявлена**.

S2-V01–04 также пересмотрены все: V01/V03 — основания текущего внешнего блокера; V02 условен, V04 ограничивает будущие архив/облако/широкий рынок. Следующий обязательный пересмотр — следующая полная приёмка любого этапа; это закреплено в собственном `open-verifications.md`, а не оставлено только в памяти чата.

## 7. Условия приёмки и готовый промпт разработчику

Полный вердикт **ПРИНЯТ** возможен после доказанного выполнения применимых REAL-частей T2-04/T2-07 без нарушений source policy и повторной полной проверки неизменённого или нового пакета. Альтернатива — явное отдельное решение пользователя о приёмке подтверждённого объёма с переносом конкретных S2-V01/S2-V03; тогда нельзя переименовать их в PASS, а зависимые заявления остаются ограниченными. UE и широкие права остаются условными согласно уже действующему плану.

Техническая правка S2-01–04 завершена, нового задания менять код по этим ID нет. Не требуются реализация этапов3–7, специальные игровые сделки, новая лицензия, повторный обход персонажей, регистрация Blizzard или покупка UE.

**Промпт для основного Codex:**

> Полностью прочитай актуальный R2 в `docs/reviews/stage-2-review.md` и `docs/reviews/open-verifications.md`. Вердикт этапа2: ЗАБЛОКИРОВАН внешними обстоятельствами; S2-01–04 CLOSED,377 тестов и независимый REAL TSM200/304 прошли. Не переписывай исправленные механизмы без новой причины и не редактируй файлы аудитора.
>
> Подготовь завершение доказательств этапа2. Для S2-V01/T2-04 нужны несколько естественных обновлений реально используемых типов TSM dataset с source scan, receive/available, hashes, интервалами опроса и пропусками. Повтор200/304 и выбранные8h/48h не измеряют cadence. Соблюдай retention: длительные raw-архивы не разрешены этим заданием.
>
> Для S2-V03/T2-07 используй подходящий обычный разрешённый перерыв, затем проверь реальное восстановление worker, gap, lease/retry, отсутствие дублей и позднюю available_at; старое решение не должно видеть поздний вход. Не усыпляй и не перезагружай ПК самовольно. Не выдавай TSM reacquisition за исторический UE backfill. UE-часть остаётся условной до допустимых условий/собственного доступа; запрет Blizzard/OAuth сохраняется.
>
> Если таких реальных наблюдений пока нет, сохрани OPEN и подготовь конкретную область возможного переноса для решения пользователя. Не изменяй критерии и не считай D1 этапа1 автоматическим переносом новых S2-проверок.
>
> Сохрани новые свидетельства отдельно от старых, обнови свой stage-2-report, матрицу критериев и manifest. Сохрани S1-V01–07 и S2-V02/V04 с пределами: нет подтверждённой реальной Auction sale, полной торгуемости, UE depth/history/membership, долгого разрешённого replay/облачной передачи. Не начинай этап3 автоматически. Предъяви пакет для полной независимой повторной приёмки.

## 8. История вердиктов

| Версия | Вердикт | Основание / изменения |
|---|---|---|
| R1,26.10.2026 | НЕ ПРИНЯТ | S2-01/02 HIGH, S2-03/04 MEDIUM;309 тестов проходили, независимые сценарии выявили ошибки |
| R2,26.10.2026 | ЗАБЛОКИРОВАН внешними обстоятельствами | Четыре ошибки закрыты,377 PASS, REAL TSM и миграция подтверждены; S2-V01/V03 ещё не закрыты |
| D1 этапа1 | Действует в прежней области | S1-V01–07 отслеживаются, не являются новым отказом этапу2; нового D1 для этапа2 не зафиксировано |

## Приложение R2-A. Независимые воспроизводимые эксперименты

Блоки ниже — фактически выполненные программы аудитора: исходники проекта не заменяются, SQLite создаётся только в памяти. SYN блок не использует сеть; REAL блок делает публичные TSM GET, использует несекретную конфигурацию и запретительный spy на loader. Не запускайте старый REAL-блок R1 как доказательство текущего transport: новая версия использует Windows spawn.

Для запуска нужного блока из этого отчёта, без создания .py файлов:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
@'
from pathlib import Path
import re
text = Path('docs/reviews/stage-2-review.md').read_text('utf8')
code = re.search(r'<!-- S2R2-SYN -->\n```python\n(.*?)\n```', text, re.S).group(1)
exec(compile(code, '<independent-stage2-review>', 'exec'))
'@ | .venv/Scripts/python.exe -B -X utf8 -c "import sys; exec(sys.stdin.read())"
```

Для настоящих10 GET заменить `S2R2-SYN` на `S2R2-REAL`. Использование `-c` сохраняет совместимость multiprocessing spawn без временного скрипта и без top-level повторного запуска всей проверки в child. Сетевой исход зависит от доступности/изменений источника; он не обязан навсегда давать ровно прежние hash.

<!-- S2R2-SYN -->
```python
import json,sqlite3,time
from pathlib import Path
from unittest.mock import patch
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker
from wow_auction.market_http import HttpClient,HttpResponse
from wow_auction.market_common import MarketError
from tests.test_market_http import Response,Opener,PUBLIC_URL
from tests.test_market_worker import ScriptedClient,CSV
SECOND=10**9
clock=[1793016000*SECOND]
def mem():
 s=object.__new__(MarketStore);s.path=Path(':memory:')
 s.db=sqlite3.connect(':memory:',isolation_level=None);s.db.row_factory=sqlite3.Row
 s.db.execute('PRAGMA foreign_keys=ON');s._migrate();s._publish();return s
def setup():
 s=mem();j={'provider':'tsm','dataset':'commodities','region':'EU','interval_seconds':3600,'enabled':True}
 c=ScriptedClient(lambda:clock[0]);w=MarketWorker(s,{'jobs':[j]},client=c,secrets={});k=s.register(j);return s,j,c,w,k
out={}
with patch('time.time_ns',side_effect=lambda:clock[0]):
 s,j,c,w,k=setup();rows=[]
 for body in [CSV,CSV.replace(b'09:00:00',b'08:00:00').replace(b',80,',b',70,'),CSV]:
  clock[0]+=2*SECOND;c.replies.append((200,body,{'content-type':'text/csv'}))
  result=w.run_job(k,j,force=True);r=s.current(k);report=s.report([k])[0]['snapshot']
  rows.append({'status':result['status'],'id':r['id'],'source_time':r['source_snapshot_at'],'quote':json.loads(r['normalized'])['records'][0]['unit_price_copper'],'freshness':report['freshness_state'],'capabilities_usable':report['capabilities_currently_usable']})
 out['source_regression']=rows;s.db.close()
 s,j,c,w,k=setup()
 c.replies.append((200,CSV.replace(b'2026-10-26',b'2020-01-01'),{'content-type':'text/csv'}))
 out['stale_200_result']=w.run_job(k,j,force=True)
 r=s.report([k])[0]['snapshot'];out['stale_200_report']={f:r[f] for f in ['source_snapshot_at_age_seconds','freshness_state','capabilities_currently_usable']};s.db.close()
 s,j,c,w,k=setup()
 c.replies.append((200,CSV,{'content-type':'text/csv','etag':'"v1"'}))
 w.run_job(k,j,force=True);clock[0]+=2*SECOND
 c.replies.append((200,CSV.replace(b',80,',b',81,'),{'content-type':'text/csv'}))
 w.run_job(k,j,force=True);clock[0]+=2*SECOND
 c.replies.append((304,b'',{'etag':'"v1"'}))
 out['retained_old_validator']={'result':w.run_job(k,j,force=True),'request_headers':c.calls[-1]['headers'],'current_quote':json.loads(s.current(k)['normalized'])['records'][0]['unit_price_copper']}
 s.db.close()
ticks=[0.0]
class SlowResponse(Response):
 def read(self,size):
  ticks[0]+=29.0
  return super().read(size)
response=SlowResponse(b'1234567890',chunk_size=1)
with patch('time.monotonic',side_effect=lambda:ticks[0]):
 try:
  r=HttpClient(opener=Opener(response),timeout=30).request(PUBLIC_URL)
  out['slow_read']={'unexpected_success':True}
 except MarketError as e:
  out['slow_read']={'code':e.code,'synthetic_elapsed_seconds':ticks[0],'closed':response.closed,'read_calls':response.read_calls}
with patch('wow_auction.market_worker.load_secrets',side_effect=MarketError('synthetic_disabled_ue_secret_error')) as loader:
 try:
  MarketWorker(None,{'jobs':[{'provider':'tsm','enabled':True}]})
  out['disabled_ue_secret_dependency']='no_error'
 except MarketError as e:out['disabled_ue_secret_dependency']={'error':e.code,'loader_calls':loader.call_count}
print(json.dumps(out,indent=2))
```

Наблюдаемые результаты SYN: N1 current IDs1,1,1; B отклонён как source regression. N2 source_stale/capabilities=false. N3 старого приложения по validators теперь unverified_not_modified_response/retry_scheduled; current quote81 сохранена. Slow-read: market_request_deadline_exceeded,2 reads,response closed. Конструктор публичного worker при запрещающем secret spy — no_error.

<!-- S2R2-REAL -->
```python
import json,sqlite3,time,hashlib
from pathlib import Path
from unittest.mock import patch
import os
from datetime import datetime,timezone
from wow_auction.market_config import load_market_config
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker
from wow_auction.market_http import HttpClient
config=load_market_config(Path('.private/market.local.json'))
s=object.__new__(MarketStore);s.path=Path(':memory:');s.db=sqlite3.connect(':memory:',isolation_level=None)
s.db.row_factory=sqlite3.Row;s.db.execute('PRAGMA foreign_keys=ON');s._migrate();s._publish()
class Capture(HttpClient):
 def __init__(self):super().__init__();self.calls=[]
 def request(self,url,**kw):
  r=super().request(url,**kw)
  self.calls.append({'url':url,'status':r.status,'sha256':hashlib.sha256(r.body).hexdigest() if r.body else None,'bytes':len(r.body),'wire_bytes':r.wire_bytes,'received_at_ns':r.received_at_ns,'elapsed_seconds':round(r.elapsed_seconds,3),'authorization_sent':'Authorization' in kw.get('headers',{}),'conditional':'If-None-Match' in kw.get('headers',{}) or 'If-Modified-Since' in kw.get('headers',{})})
  return r
guard=patch('wow_auction.market_worker.load_secrets', side_effect=AssertionError('unexpected credential inspection')); spy=guard.start()
env=patch.dict(os.environ, {'WOW_UNDERMINE_API_KEY':'SYNTHETIC\nBROKEN'});env.start()
c=Capture();w=MarketWorker(s,config,client=c)
out={'started_at':datetime.now(timezone.utc).isoformat(),'first':w.scan(force=True)}
out['snapshots']=[]
for key,job in w.jobs():
 if job['provider']!='tsm':continue
 r=s.current(key)
 if not r:continue
 n=json.loads(r['normalized'])
 out['snapshots'].append({'dataset':job['dataset'],'id':r['id'],'sha256':r['sha256'],'records':r['record_count'],'raw_bytes':len(r['raw']),'normalized_utf8_bytes':len(r['normalized'].encode()),'source_snapshot_at':r['source_snapshot_at'],'received_at_ns':r['received_at_ns'],'available_at_ns':r['available_at_ns'],'scope':n['scope'],'normalizer_revision':n['normalizer_revision']})
time.sleep(1.1)
out['repeat']=w.scan(force=True);out['calls']=c.calls
out['snapshot_count_after_repeat']=s.db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
out['sqlite_quick_check']=s.db.execute('PRAGMA quick_check').fetchone()[0]
out['foreign_keys']=s.db.execute('PRAGMA foreign_key_check').fetchall()
out['status']=s.report([k for k,_ in w.jobs()]);out['schema']=s.db.execute('PRAGMA user_version').fetchone()[0];out['secret_loader_calls']=spy.call_count;out['validators_count']=s.db.execute('SELECT count(*) FROM http_validators').fetchone()[0]
env.stop();guard.stop();s.db.close();print(json.dumps(out,ensure_ascii=False,indent=2))
```

Полные содержимое цен и персональные массивы не включены в отчёт; сохраняются только воспроизводимая процедура, hashes, счётчики, статусы и времена. Результат сетевого блока описан в разделе5; исходные ответы существовали только в памяти процесса.

---

# Архив предыдущей полной проверки — неизменённый R1

Ниже сохранены все57 146 исходных байтов R1 с SHA-256 `b8bfe328eb601f99137546939fb77754a0cf819c967a378ca575ed8591335895`. Его прежний вердикт/замечания являются историей; действующий результат находится в R2 выше.

# Независимая приёмка этапа 2 — R1

Дата: **26.10.2026**, Europe/Moscow. Полная проверка по запросу «проверь этап 2», а не проверка только прежних замечаний.

## 1. Вердикт и проверенная версия

**НЕ ПРИНЯТ.** Обнаружены четыре воспроизводимых дефекта реализации: старые рыночные данные получают статус текущих; HTTP validators могут относиться к другому содержимому; загрузка не ограничена общим временем; отключённый Undermine сохраняет зависимость запуска TSM от своих секретов. Наличие 309 успешных штатных тестов этих дефектов не опровергает.

Это **не** вердикт «ЗАБЛОКИРОВАН внешними обстоятельствами». Для исправления перечисленных ошибок не нужны новые игровые сделки, Blizzard credentials, регистрация UE или реализация последующих этапов.

| Идентификатор | Проверенное значение |
|---|---|
| Репозиторий | `W:\VSProjects\WOW_AUC_AI`, тот же checkout разработчика |
| Git HEAD | `13be8666b92e9dd17ee11f614025a04662e9ed68` |
| Рабочее дерево | README изменён; handoff/docs/tests/wow_auction/pyproject/.gitignore ещё не отслеживаются Git. HEAD сам по себе не описывает проверенный пакет |
| Manifest этапа 2 | `docs/stage2-package-manifest.sha256`; **94/94** файлов совпали |
| SHA-256 manifest | `1ab9e1e854f46b7eeeb9c7082c8614bc3fc0564aeacfb91c005b54e4d2f64c13` |
| Принятый baseline этапа 1 | `docs/research/baselines/stage-1-r2.zip`; SHA-256 `94a1e497a7121273c71454b09aeb682b6feb26baa2308fa008afe324bb6dd94e`; **62/62** entries проверены по вложенному manifest |
| Среда | Python **3.13.7**, SQLite **3.50.4**, существующее окружение `.venv` |
| Полный штатный набор | **309/309 PASS**, **14.393 s**, exit 0; включает 160 регрессионных тестов этапа 1 |

Сравнение с принятым baseline подтвердило: прежние source-модули этапа 1 и его тесты не изменены, кроме расширения CLI `wow_auction/__main__.py`; изменены также README, pyproject и семь общих документов. Исторические аудиты не переписывались. Новый рыночный код и все его тестовые модули просмотрены, проверены CLI, конфигурация, нормализация, HTTP, политика источников, SQLite и очередь.

Основания области: `codex_handoff/INDEPENDENT_REVIEWER_PROMPT.md`, `PROJECT_CONTEXT.md`, `MAIN_CODEX_CORRECTION.md`, применимые требования основного задания; текущие architecture/data-model/implementation-plan/acceptance-tests/sources/risks/open-questions; весь новый пакет этапа 2, его JSON-свидетельства и матрица альтернатив. Устаревшие утверждения принятых исследований сопоставлялись с текущими изменениями и первичными источниками.

**Границы работы аудитора:** исходники, документы и результаты тестов разработчика, рабочие БД и игровые файлы не редактировались. Новые эксперименты выполнялись в памяти; штатный набор использовал собственные синтетические временные каталоги. Реальные БД открывались через SQLite `mode=ro` и `query_only`; база активов копировалась и мигрировалась только в память. Секретный файл не читался и не хешировался. Новые постоянные записи аудитора — только в `docs/reviews/`.

## 2. Изменение источников и соответствие общей архитектуре

В документах разработчика зафиксировано последующее решение пользователя полностью исключить прямой Blizzard API и получение его ключей: `docs/research/stage-2-no-blizzard-alternatives.md:7`, `docs/architecture.md:5–21`, `docs/acceptance-tests.md:5,48`. Отдельная история чата разработчика аудитору не предоставлена; основание этого изменения здесь — согласованная запись в доступном пакете. Проверка не возвращает запрещённый маршрут и не требует Blizzard credentials.

Отключение действительно выполнено в runtime: `market_worker.py:45–46,104–105` и список разрешённых HTTP hosts; tests проверяют прямые GET/POST и redirects. Сохранённый `market_blizzard.py` — автономный нормализатор переданных байтов, не подключённый сетевой fallback. Его наличие само по себе не нарушает ограничение.

Замена явно теряет возможности: действующий TSM не даёт книгу предложений, количества, точные варианты и ряд истории; UE пока только DOC/SYN/MOCK, сеть закрыта политикой. Числовые connected IDs не выдуманы; TSM realm scope остаётся `realm_unresolved`, а UE имеет отдельную идентичность по provider/region/membership. Соответствие групп разных поставщиков не выводится из одинакового slug. Эти ограничения согласованы между архитектурой, моделью и планом.

Требование глобального каталога профессиональных и непрофессиональных преобразований **всех Retail expansions** сохранено. ATT traversal и допустимые build-данные заменяют прежний обязательный profession API маршрут как план этапа 3; полнота ATT не объявлена полнотой игры. Локальные 71 связи CraftSim не стали знаменателем каталога. Реальный CraftSim runtime/export, вероятности и сравнение расчётов остаются непроверенными возможностями этапа 3. Требовать их реализации сейчас было бы выходом за область приёмки.

Архитектура виртуальной торговли, причинных расследований, обнаружения неизвестных факторов и защиты после продвижения стратегии не отменена. Снимки/TSM saleRate не превращаются в исполнение или доказательство причины; общий upstream TSM/UE не считается независимыми свидетельствами. Но дефекты свежести и HTTP-сверки ниже должны быть устранены до использования рынка экономическим движком: иначе он будет расследовать ложные изменения, вызванные импортом. Долговременный replay и обучение пока ограничены политикой хранения; hash после удаления не восстанавливает входы.

## 3. Матрица требований этапа 2

Исходные формулировки: `docs/acceptance-tests.md:42–59`. PASS ограничен реально проведённой проверкой.

| Критерий | Независимая проверка и доказательство | Результат |
|---|---|---|
| T2-01 | Прямой Blizzard/OAuth исключён; offline-код и runtime guards просмотрены; штатные отрицательные тесты пройдены | **SUPERSEDED**, не ожидание ключа |
| T2-02 | UE URL/schema/price levels/variants/history/source clocks изучены по коду, тестам и документации владельца; реального разрешённого authenticated payload нет | **DOC + SYN/MOCK**, REAL OPEN; не работающая история |
| T2-03 | Реально загружены все 5 TSM CSV; повторно нормализованы также 5 текущих сохранённых наборов; нулевые котировки, региональные scalars, точность copper и отсутствие quantities проверены | **PASS REAL + SYN** в пределах агрегатов |
| T2-04 | 5 HTTP200, затем 5 HTTP304; при корректных validators осталось 5 snapshots. У commodities есть новый естественный upstream scan относительно сохранённого разработчиком; этого недостаточно для нескольких интервалов cadence | **Частично**; S2-01/02 нарушают крайние случаи repeated/stale inputs; cadence OPEN |
| T2-05 | Empty/unconfirmed, HTML, изменённый header, повреждённые строки, duplicate IDs/keys, gzip/framing/limits, partial batches и last-good проверены штатными тестами и просмотром | **PASS SYN**; полная корректная по синтаксису CSV без внешнего manifest не доказывает полноту всего рынка |
| T2-06 | 401/403/429/5xx, Retry-After, persistent provider cooldown, force/restart и квоты покрыты MOCK; реальные 200/304 воспроизведены без провокации 429 | **FAIL S2-02/03**; медленное чтение не ограничено общим deadline |
| T2-07 | Lease, sleep-gap, late availability, crash before/after commit и replay cutoff проверены SYN; настоящий sleep/wake и UE backfill не выполнялись | **PASS SYN / REAL OPEN** |
| T2-08 | Region/realm/commodity разделены в реальных TSM данных; variants, membership/overlap/conflicts и общий upstream проверены SYN/DOC | **Частично REAL + SYN**; UE REAL OPEN |
| T2-09 | Полные пять доступных CSV: размеры, объёмы нормализации, скорость; рабочая БД 89 182 208 bytes, 10 snapshots, quick_check/FK; крупнейший CSV повторно измерен | **PASS для выбранного TSM scope**; UE/full depth/долгий рост не измерены |
| T2-10 | Удаляются raw и normalized, secure_delete/DELETE, input reference становится unreplayable, миграция v1→v2 сохраняет записи | **PASS SYN**; срок 4 h — проектное ограничение, не доказанная лицензия |
| T2-11 | TSM работает без Authorization; Blizzard transport запрещён; UE disabled не делает запросов | **Частично FAIL S2-04**: инициализация всё равно читает/проверяет UE secret configuration |
| T2-12 | Provider group key зависит от региона и состава; порядок slugs не влияет; conflicting groups не сливаются; old numeric mapping не переносится в TSM | **PASS SYN/DOC**, REAL UE OPEN |
| Регрессии этапа 1 | Все прежние тесты внутри 309 PASS; текущие игровые файлы заново прочитаны; импорт 30 Ledger rows в память идемпотентен | **PASS проверенного объёма**; S1-V01–V07 не закрыты |

## 4. Подтверждённые дефекты

### S2-01 — HIGH: старое содержимое допускается как текущий рынок

**Места:** `wow_auction/market_storage.py:247–262` (безусловная замена current для complete), `:227–231,316–333` (usable/freshness); `wow_auction/market_worker.py:59–61` проверяет лишь часы из будущего. Требования: T2-04/06, `docs/data-model.md:13,19`, `codex_handoff/INDEPENDENT_REVIEWER_PROMPT.md:57`.

**Воспроизведение N1:** один TSM job получает валидные CSV в порядке source time **09:00 → 08:00 → 09:00**, цены в синтетическом примере **80 → 70 → 80 copper**. Все три результата — `accepted`, IDs 1/2/3. После второго запроса current сдвинут на час назад, однако `freshness_state=current_observation`, `capabilities_currently_usable=true`. Третий ответ с исходным hash создаёт ещё одну запись. Причина HTTP200 не исследуется и никакого предупреждения о регрессии source time нет.

**Воспроизведение N2:** при часах 26.10.2026 CSV со scan time **01.01.2020** также получает `accepted/current_observation/capabilities_currently_usable=true`. Возраст источника **215 146 806 s** показан числом, но не участвует в пригодности. Новое получение/план следующего опроса подменяет проверку свежести источника.

**Влияние:** откат CDN/поставщика или застрявший файл может подменить более новый срез; дальнейший код получает положительный машинный флаг пригодности старых агрегатов. На этом этапе прибыль ещё не считается, поэтому фактической потери денег не утверждается.

**Исправление:** разделить успешность HTTP, возраст локального кэша, возраст источника и допустимость current-входа. Для сопоставимого current dataset не заменять более новый подтверждённый срез более старым без явного состояния regression/correction и сохранённого происхождения. Задать обоснованную source/dataset-specific freshness policy; unknown/слишком старый source time не должен выдавать безусловный current usable. Порог не выдавать за установленную частоту поставщика. Исторические endpoints и zero-quantity lastSeen обрабатывать по их собственной семантике: старый исторический point не ошибка сам по себе. При одинаковом source time, но ином содержимом сохранять ревизию; не запрещать все такие корректировки. Проверить A→B→A, stale HTTP200/304, parser revision, retention/reacquisition, last-good и replay без изменения прошлой доступности.

### S2-02 — HIGH: ETag/Last-Modified не связаны атомарно с проверяемым snapshot

**Места:** `wow_auction/market_storage.py:168–175` использует COALESCE для validators; `:254–269` отдельно фиксирует снимок. `wow_auction/market_worker.py:83–91` берёт validators из jobs, а `:121–128` применяет 304 к текущему снимку без сохранённой связи с условным запросом.

**Воспроизведение N3:** первый HTTP200 содержит CSV A и `ETag: "v1"`. Второй HTTP200 содержит другой CSV B и **не содержит ETag/Last-Modified**. Current уже B, но в jobs остаётся validator A. Третий запрос отправляет `If-None-Match: "v1"`. Ответ 304 приводит к `unchanged, snapshot_id=2`, хотя проверялся A, а код подтверждает B. В синтетическом выводе current quote=81, у A была 80.

Это не требование к серверу отвечать неверно: если сервер/узел вновь выдаёт A с validator v1, 304 на отправленный клиентом v1 корректен; клиент неверно связывает его с B. Тот же разрыв возможен при остановке между commit нового snapshot и отдельным finish job: в сохранённом snapshot headers новые, в jobs старые.

**Влияние:** ложная успешная сверка содержимого; фактическое изменение рынка скрывается, восстановление после сбоя не восстанавливает согласованность HTTP-кэша.

**Исправление:** связать validators с конкретным content/snapshot/revision и атомарным переходом current. На новом 200 отсутствие validator должно очищать прежний validator этого вида; ошибочный/частичный ответ не должен портить last-good validators. Для 304 хранить identity действительно отправленного условного запроса, проверять совместимость нормализатора, lease и неизменность соответствующего current. Неподтверждённый 304 — безопасная ошибка/последующий ограниченный full fetch, не `unchanged`. Нужны тесты ETag/Last-Modified по отдельности, 200 без одного/обоих, crash/restart между фиксациями, смена parser revision и конкурирующий worker.

Основание семантики: [RFC 9110, If-None-Match](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-none-match) — условие относится к выбранному representation и переданному validator, а не к произвольной последней строке клиентской БД.

### S2-03 — MEDIUM: timeout не ограничивает полное время запроса

**Места:** `wow_auction/market_http.py:108–111,138–154`; `market_worker.py:175–178` выполняет задания последовательно; lease `market_storage.py:160–161` рассчитан на 180 s. Отчёт разработчика `docs/stage-2-report.md:14` заявляет ограничение временем, T2-06 требует устойчивую обработку timeout.

**Воспроизведение N4:** HttpClient(timeout=30), маленький ответ 10 bytes, 11 чтений с продвижением искусственных монотонных часов на 29 s каждое. Результат **HTTP200, elapsed_seconds=319.0**, а не timeout; ответ закрыт лишь после окончания. Реального ожидания 319 s не было: это детерминированный MOCK времени. Штатный тест проверяет передачу timeout в opener, но общего deadline не проверяет.

[Документация Python urllib](https://docs.python.org/3.13/library/urllib.request.html#urllib.request.urlopen) описывает timeout блокирующих операций; он сам по себе не является общим бюджетом всей загрузки. В цикле собственного контроля времени нет. Малые регулярные порции способны удерживать запрос существенно дольше заданного значения, а buffered read дополнительно требует учёта остатка deadline.

**Влияние:** одно медленное соединение задерживает все остальные jobs; lease может истечь до публикации. Ограничения размера не ограничивают приемлемую длительность скачивания.

**Исправление:** явный monotonic deadline для connect/headers/body с учётом оставшегося времени на каждом блокирующем шаге, ограниченным разбором/завершением запроса и корректным закрытием. Согласовать deadline и lease; истёкший/перехваченный lease не должен прекращать обслуживание остальных jobs или публиковать результат от старого владельца. Проверить slow-drip при регулярных байтах, отсутствие Content-Length, gzip, зависание до headers и нормальное продолжение очереди после тайм-аута. Не решать проблему бесконтрольным увеличением lease или timeout.

### S2-04 — MEDIUM: отключённый UE способен остановить независимый TSM

**Места:** `wow_auction/market_worker.py:17–20` безусловно вызывает load_secrets; `market_config.py:106–122` читает файл/окружение и проверяет UE ключ до выяснения enabled/policy; `__main__.py:137–154` делает это также для status/resolve. Противоречит `docs/implementation-plan.md:38` и T2-11 (`docs/acceptance-tests.md:58`).

**Воспроизведение N5:** включён только TSM, UE disabled; секретный файл отсутствует; в изолированном окружении задан исключительно искусственный `WOW_UNDERMINE_API_KEY="SYNTHETIC\nBROKEN"`. Уже конструктор MarketWorker завершается `invalid_credential_format`. Ни один реальный ключ не использован. Отдельный spy подтвердил вызов loader при TSM-only конфигурации.

**Влияние:** устаревший/повреждённый неиспользуемый UE secret configuration блокирует публичный импорт и диагностику. Отсутствие любой записи с ключом работает, но изоляция отключённого адаптера неполная.

**Исправление:** получать и проверять только секреты реально необходимого, включённого и допущенного политикой адаптера, непосредственно перед соответствующим запросом. Ошибка UE должна быть ограничена его job, сохраняя работу TSM и read-only диагностики. Для disabled/policy-blocked UE не открывать файл ключа; сохранить запрет поиска Blizzard secrets. Добавить случаи malformed/missing/unreadable UE configuration при отключённом UE и штатный случай включённого допустимого UE с неверным ключом. Не печатать секреты и не обходить source policy.

## 5. Воспроизведённые технические проверки

### Штатный набор и артефакты

Из корня репозитория:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

Фактический результат: `Ran 309 tests in 14.393s — OK`. Это SYN/MOCK + локальные synthetic SQLite/file tests; ими не объявлена REAL-интеграция UE.

Дополнительно:
- 94 хеша текущего пакета и 62 entries архива baseline совпали; код/тесты этапа 1 сопоставлены с архивом.
- Проверено **215** локальных Markdown file targets в README, верхнем уровне docs, handoff и новой матрице альтернатив: отсутствующих файлов нет. Проверка target-файла не означает проверку всех URL/fragment anchors.
- Рабочая market.sqlite3 прочитана без записи: schema=2, quick_check=ok, foreign_key_check=0 строк; 10 snapshots, 89 182 208 bytes.
- У всех 10 сохранённых raw совпал SHA-256, counts normalized совпали с metadata. Пять текущих наборов повторно нормализованы: records/scope/source clock совпадают с сохранёнными. Новые parser projections разработчика не названы новыми наблюдениями рынка.
- Крупнейший сохранённый region_items CSV: 2 545 285 bytes, 31 187 rows, **2.055 s** нормализации с tracemalloc, peak **57 035 020 bytes**. Это traced Python allocations одного разбора, не измеренный полный RSS и не размер всей рыночной истории.
- SYN проверяет crash/rollback/reopen, publication после commit, quota/cooldown restart, retention и отсутствие восстановления удалённых цен по hash. Отдельное независимое воспроизведение N1–N5 приведено далее.

### Реальные запросы аудитора

26.10.2026 **11:14:25–11:14:33 UTC** / **14:14:25–14:14:33 МСК**. Исполнялись реальные HttpClient→MarketWorker→нормализатор→SQLite **в памяти**, конфигурация jobs взята из существующей несекретной market.local.json; secrets явно пусты. Первый запуск из ограниченной сетевой среды получил пять `market_network_unavailable`; backoff не дал force немедленно повторить их. После разрешённого сетевого запуска все запросы прошли. Это ограничение среды не осталось блокером аудита.

| TSM dataset | Rows | Raw bytes | Normalized UTF-8 bytes | Source scan epoch seconds | HTTP200 время, s |
|---|---:|---:|---:|---:|---:|
| commodities EU | 12 162 | 901 725 | 6 473 291 | 1793007098 | 0.683 |
| realm_items EU/soulflayer | 11 030 | 868 751 | 5 926 763 | 1793007098 | 0.522 |
| region_items EU | 31 187 | 2 545 285 | 17 276 599 | 1792890147 | 0.647 |
| realm_pets EU/soulflayer | 961 | 73 693 | 509 050 | 1793007098 | 0.472 |
| region_pets EU | 978 | 77 953 | 532 839 | 1792890147 | 0.387 |

Итого raw **4 467 407 bytes**, normalized **30 718 542 bytes**. Последовательный повтор дал **5 HTTP304**, прежние IDs 1–5, count snapshots=5, quick_check=ok, foreign keys=0 нарушений. Все десять запросов без Authorization, только к публичному TSM; UE job=disabled. Blizzard/OAuth не вызывались. Монотонный порядок received→available сохранён; для обычного 304 снимок не пересоздан.

SHA-256 реально прочитанных ответов:

| Dataset | SHA-256 |
|---|---|
| commodities | `963ee30c408a754a84abfa3467d7b0c1001985dd8b7f1d00709c102080a6cb16` |
| realm_items | `b7f2bc216c9f04b17275e3dd46a886cc99a0509b43a571b80872eb0d661bd05c` |
| region_items | `cb4abf46337a2e3907fec6f30322c03383900ad722da971ea988d220c87eb93f` |
| realm_pets | `7fe46ad48ed44996544491657b08bd7caa3d99a329cdfb9df34d6f31bf9ab9e1` |
| region_pets | `8854ef6934713d487150163c1af6b90b2077ba65995cc2359ce48ed055d5c240` |

У commodities по сравнению с финальным capture разработчика изменились hash, размер и source scan: **1792996299 → 1793007098**, разность **10 799 s**. Это реальное дополнительное наблюдение изменения; оно не доказывает стабильную периодичность публикаций или точный момент доступности между двумя загрузками. Остальные четыре hash совпали. Успешный немедленный 304 — проверка conditional retrieval, не измерение естественного обновления.

Тела новых ответов не сохранены аудитором на диск; в этом отчёте только metadata/hashes. Exact replay этих конкретных bytes в будущем не обещается. Реальные прежние raw пока находятся в рабочей БД разработчика под её retention; аудитор не продлевал их срок и не запускал команды обслуживания рабочей БД.

### Первичные источники и выборочная проверка существенных утверждений

- [TSM Public Pricing Data](https://tradeskillmaster.com/public-data): подтверждает публичные CSV без ключа, copper, базовую идентичность питомцев, upstream scan meaning updatedAt и различную частоту realm/region. Это соответствует адаптеру. Локальные 120/h и 5/s — собственный бюджет приложения; в публичном описании CSV нет такой обязательной квоты поставщика.
- [Undermine API](https://undermine.exchange/api.html): сверены требование собственного ApiKey, 3000 points/hour, группы миров, разделение snapshot/lastUpdated/lastSeen, free/paid детали, отсутствие item metadata и особенности daily. Код правильно не считает daily цену закрытием, total quantity оборотом, а цену/исчезновение лота продажей. API-документация не заменяет authenticated REAL payload.
- [Undermine Terms, §7](https://undermine.exchange/terms.html): ограничение систематического сбора базы сосуществует с опубликованным API. Сеть закрыта текущим решением проекта; аудитор не объявляет весь API незаконным и не считает один ключ разрешением хранения.
- [TSM Terms](https://tradeskillmaster.com/terms) не устанавливают в прочитанном тексте проектный срок четыре часа. Наличие публичного импорта поддерживает текущий рабочий путь, но бессрочный архив/облачная передача остаются отдельным нерешённым использованием.
- На диске повторно сверены четыре SHA-256 из NB-02/03/04/06 новой матрицы: LibRealmInfo.lua, ItemInfoCache.lua, ItemInfoSerializer.lua, CraftSim itemRecipeDB.lua — **4/4 совпали**. В LibRealmInfo действительно minor17, строка 986 содержит EU/Soulflayer/1604, строка 1557 — библиотечную connection row. Это не live-подтверждение состояния сервера. CraftSim itemRecipeDB действительно описывает recipeID/itemID/qualityID/crafters, не полную схему механики или исполненный расчёт.

## 6. Унаследованные открытые проверки

Решение пользователя D1 о продолжении соблюдено: отсутствие этих свидетельств само по себе **не является причиной отрицательной приёмки этапа 2**. Они ограничивают только зависимые выводы. Реестр обновлён в [open-verifications.md](open-verifications.md).

Текущие игровые файлы заново прочитаны 26.10.2026 в **11:15:21 UTC**, затем дополнительно проверены импортом. Относительно прежнего аудита появились новые данные. Рабочая база активов по-прежнему schema2 с 2 Ledger rows; в копии **только в памяти**, мигрированной до schema3, текущий импорт дал **30 rows** (4 purchases Auction, 4 Repair Bill, 22 Vendor sales), повтор четырёх источников — `unchanged`. Полная прибыль остаётся unknown, cash conflicts=0, наблюдений кошельков/warband=5. Само отсутствие конфликтов не является независимой сверкой полноты.

| Файл источника | Bytes | SHA-256 |
|---|---:|---|
| GX | 662 | `025bc76a6434d5634177ea37b9d1e45224c6f919c7cd800da85f44adf6cf0607` |
| BagSync | 33 913 | `1aee395169f9b55a042fcbd3cfa5a7dd2b733da6c2b0b2e054ff89da5003c352` |
| TradeSkillMaster | 1 567 334 | `ede4fe92e33054d359722c2c7c7759a68437fcfc84e4a20bd7ba466ffe94ba85` |
| CraftSim | 269 336 | `d52782702e110c4e94bed540bee0f3269906d1cd9e2ed96908193d2a500e78b6` |

В момент стабильного чтения bytes/mtime каждого источника до/после совпали; аудитор не записывал в игру. Имена персонажей, абсолютные суммы и приватные paths аккаунта не опубликованы.

| ID | Новое свидетельство / фактический предел | Статус и влияние |
|---|---|---|
| S1-V01 | 22 продажи — **Vendor**, Auction sales=0. 4 покупки Auction не заменяют продажу | **OPEN**; net/unit/AH-cut реальной аукционной продажи не подтверждены; TSM market import не зависит от этого |
| S1-V02 | 4 расхода имеют money_type=Repair Bill; csvIncome/csvCancelled/csvExpired по 0. Transfer/refund не появились | **OPEN**; нельзя обещать полный cashflow/PnL |
| S1-V03 | Четыре прежние покупки и прежняя Vendor sale сохранили известные количества. Новые строки не доказали рост одного существующего агрегата | **OPEN**; SYN роста и replay пройдены, REAL изменившаяся пара всё ещё отсутствует |
| S1-V04 | 5 денежных наблюдений, 0 конфликтов GX/BagSync; независимого одновременного UI остатка нет | **OPEN**; абсолютный доступный капитал/current NAV остаётся unknown |
| S1-V05 | BagSync: 244 item rows, в том числе warband38/bag81/bank20/auction10/equip79/equipbags16; 56 контейнеров без собственного observed_at | **OPEN**; новые auction rows полезны, но полного синхронного охвата/контролируемого переноса не доказывают |
| S1-V06 | В конфигурации один account_scope | **OPEN / второго account нет**; второй лицензии не требовать, межлицензионная дедупликация остаётся SYN |
| S1-V07 | 149 item rows с tradeability=unknown; 95 исключены как оборудование/сумки оборудования. CraftSim engine_evaluation_available=false | **OPEN**; новая цена TSM не доказывает binding/точный вариант/торгуемость имущества |

S1-01–03, ранее исправленные программные дефекты, не переоткрываются: код baseline неизменен, регрессионный набор прошёл. Сохранённый reload-опыт и R1/R2/D1 этапа 1 остаются историей, не заменены новыми числами задним числом.

## 7. Оставшиеся ограничения и условия приёмки

Внешние/непроверенные возможности отделены от S2-01–04:

| ID контроля | Что осталось | Условие закрытия / правило до него |
|---|---|---|
| S2-V01 | T2-04: несколько естественных обновлений/cadence для разных TSM dataset | Датированные последовательные наблюдения hash/source clocks/received/available в обычном разрешённом worker, измерение нескольких интервалов. Один новый scan и 304 недостаточны |
| S2-V02 | UE authenticated current/history/depth/provider membership и платные варианты; Q03/Q06 | Только допустимый собственный UE доступ и разрешённая политика, затем REAL проверки выбранных методов. До этого disabled + DOC/MOCK; не искать Blizzard fallback, не покупать доступ и не писать владельцам автоматически |
| S2-V03 | T2-07 REAL sleep/wake/длительный offline, поздняя история | Контролируемое допустимое наблюдение обычного worker; gap/истинная поздняя available_at. SYN не выдавать за проведённый сон ПК; UE backfill зависит от V02 |
| S2-V04 | Долгий архив/raw+derived, облачная передача, full-depth/multi-scope рост | Отдельная source policy и измерения до соответствующих функций. Четырёхчасовой кэш не даёт истории для многодневного exact replay; после удаления входа честный unavailable |

Этап можно принять после:
1. Исправления **S2-01–04** и целевых regression tests, полного повторного прогона и независимой **полной** перепроверки пакета.
2. Повторного REAL TSM импорта и conditional retrieval с согласованными validator/content identities, без изменения рабочих активов/игровых файлов.
3. Обновления developer report/матрицы/manifest по фактической версии. В отчёте отразить результаты freshness/slow-drip/crash tests и отделить TSM агрегаты от отсутствующей UE history/depth.
4. Выполнения оставшихся обязательных операционных REAL-критериев T2-04/T2-07 либо отдельного явного согласования их переноса с ограниченным объёмом приёмки. Аудитор не превращает старое D1 для этапа 1 в автоматическое разрешение перенести любые новые проверки этапа 2.
5. Сохранения условного статуса UE: действующий план уже допускает DOC/MOCK до разрешённого доступа. Подключение UE или реализация этапов 3–7 не требуется для исправления четырёх дефектов. Будущая функция, которой нужны history/depth/variant detail, не получает фиктивный PASS из TSM CSV.

Нет требования специально торговать в WoW, покупать вторую лицензию, повторять обход персонажей или включать Blizzard API. Никакие следующие этапы этим отчётом не разрешены и не начаты.

## 8. Готовый промпт для Codex-разработчика

> Исправь **только этап 2** по полной независимой приёмке `docs/reviews/stage-2-review.md`, R1 от 26.10.2026. Вердикт НЕ ПРИНЯТ вызван S2-01–04, а не нехваткой Ledger sales или Blizzard credentials.
>
> Сначала изучи весь отчёт, актуальные acceptance-tests/architecture/data-model и `docs/reviews/open-verifications.md`. Не переписывай независимые отчёты, их историю и прежние результаты. Сохрани baseline проверенного пакета и сделай адресные изменения.
>
> 1. **S2-01:** предотврати молчаливый откат current snapshot по source time. Отдели HTTP health/cache retention/source age/current capability. Обоснуй и документируй per-source/per-dataset freshness policy; неизвестные/старые часы не должны давать безусловную пригодность. Исторические points, lastSeen при quantity0 и корректировки того же source timestamp имеют другую семантику. Сохраняй evidence/revisions и действительную available_at. Добавь A→B→A, stale200/304, restart/expiry/parser-revision tests.
> 2. **S2-02:** сделай validators свойством точного content/snapshot/revision с атомарной фиксацией. Новый HTTP200 без ETag/Last-Modified очищает устаревший validator соответствующего вида. 304 подтверждает только реально отправленный conditional input при совместимом parser и действительном lease. Проверь crash между snapshot commit и finish, restart, конкурентного владельца, отсутствие headers и full-fetch recovery без storm.
> 3. **S2-03:** введи настоящий общий monotonic deadline загрузки, включая медленное регулярное чтение и headers, с bounded blocking и закрытием ответа. Согласуй lease/timeout, сохраняй last-good и продолжай остальные jobs после ошибки. Добавь deterministic slow-drip/gzip/no-length и lease-loss проверки, не заменяя их тестом передачи timeout в opener.
> 4. **S2-04:** отключённый/policy-blocked UE не должен читать файл/окружение ключа и мешать TSM/status/resolve. Загружай необходимые secrets перед допустимым запросом конкретного адаптера. Ошибка UE локальна его job; Blizzard secrets не искать; policy не обходить. Проверь malformed/missing/unreadable UE secret config при TSM-only.
>
> Выполни полный suite и новые адресные тесты. Повтори разрешённый REAL TSM импорт, опиши фактические scopes/counts/hashes/времена и ограничения; несколько мгновенных 304 не называй measured cadence. T2-04/T2-07 доведи до реальных свидетельств, где доступно, либо явно вынеси конкретные оставшиеся ограничения на решение о переносе; не объявляй их закрытыми самостоятельно.
>
> Новые игровые данные аудитор уже проверил: 4 Auction purchases, 4 Repair Bill, 22 Vendor sales, Auction sales=0; S1-V01–V07 остаются OPEN. Используй актуальные данные, но не требуй специальных сделок/обхода персонажей и не объявляй полный PnL. Рабочую базу активов и чужие SavedVariables не переписывай.
>
> Сохрани запрет прямого Blizzard/OAuth и получения его ключей, блокировку сети UE до допустимой policy, отсутствие игровой автоматизации и честные unavailable для depth/history/variants. Не реализуй этап 3, CraftSim bridge, торговый движок, ИИ или UI. Обнови свои документы, результаты и package manifest; предоставь пакет повторной полной независимой приёмки и остановись.

## 9. История вердиктов

Финальная проверка границ аудита: все **180** зафиксированных до проверки несекретных файлов вне `docs/reviews/` сохранили SHA-256; отсутствующих файлов нет. Секретный файл исключён из чтения/хеширования. Хеши `stage-0-review.md` и `stage-1-review.md` не изменились. Из собственных файлов создан этот отчёт и обновлён `open-verifications.md` с сохранением прежней истории. Три Python-блока воспроизведения в записанном отчёте отдельно проверены на синтаксис.

| Дата / ревизия | Вердикт | Основание |
|---|---|---|
| 26.10.2026, R1 | **НЕ ПРИНЯТ** | Полная проверка этапа 2; 309 штатных PASS, REAL TSM200/304; подтверждены S2-01–04; внешние S2-V01–04 отделены, S1-V01–07 пересмотрены |

При следующей проверке сохранять этот вердикт и историю исправлений. Повторная проверка должна охватывать весь этап, а не только перечисленные четыре случая.

## Приложение A. Независимые отрицательные эксперименты N1–N5

Запуск: из корня проекта передать следующий Python в `.venv\Scripts\python.exe -B -X utf8 -` через буквальный PowerShell here-string. Ничего в проекте создавать не требуется. SQLite только `:memory:`, сеть отсутствует, данные/ключ искусственные. Вспомогательные Response/ScriptedClient взяты из существующих SYN fixtures; последовательности и проверки ниже добавлены аудитором в отчёт, тесты разработчика не изменялись.

```python
import json,sqlite3,time
from pathlib import Path
from unittest.mock import patch
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker
from wow_auction.market_http import HttpClient,HttpResponse
from wow_auction.market_common import MarketError
from tests.test_market_http import Response,Opener,PUBLIC_URL
from tests.test_market_worker import ScriptedClient,CSV
SECOND=10**9
clock=[1793016000*SECOND]
def mem():
 s=object.__new__(MarketStore);s.path=Path(':memory:')
 s.db=sqlite3.connect(':memory:',isolation_level=None);s.db.row_factory=sqlite3.Row
 s.db.execute('PRAGMA foreign_keys=ON');s._migrate();s._publish();return s
def setup():
 s=mem();j={'provider':'tsm','dataset':'commodities','region':'EU','interval_seconds':3600,'enabled':True}
 c=ScriptedClient(lambda:clock[0]);w=MarketWorker(s,{'jobs':[j]},client=c,secrets={});k=s.register(j);return s,j,c,w,k
out={}
with patch('time.time_ns',side_effect=lambda:clock[0]):
 s,j,c,w,k=setup();rows=[]
 for body in [CSV,CSV.replace(b'09:00:00',b'08:00:00').replace(b',80,',b',70,'),CSV]:
  clock[0]+=2*SECOND;c.replies.append((200,body,{'content-type':'text/csv'}))
  result=w.run_job(k,j,force=True);r=s.current(k);report=s.report([k])[0]['snapshot']
  rows.append({'status':result['status'],'id':r['id'],'source_time':r['source_snapshot_at'],'quote':json.loads(r['normalized'])['records'][0]['unit_price_copper'],'freshness':report['freshness_state'],'capabilities_usable':report['capabilities_currently_usable']})
 out['source_regression']=rows;s.db.close()
 s,j,c,w,k=setup()
 c.replies.append((200,CSV.replace(b'2026-10-26',b'2020-01-01'),{'content-type':'text/csv'}))
 out['stale_200_result']=w.run_job(k,j,force=True)
 r=s.report([k])[0]['snapshot'];out['stale_200_report']={f:r[f] for f in ['source_snapshot_at_age_seconds','freshness_state','capabilities_currently_usable']};s.db.close()
 s,j,c,w,k=setup()
 c.replies.append((200,CSV,{'content-type':'text/csv','etag':'"v1"'}))
 w.run_job(k,j,force=True);clock[0]+=2*SECOND
 c.replies.append((200,CSV.replace(b',80,',b',81,'),{'content-type':'text/csv'}))
 w.run_job(k,j,force=True);clock[0]+=2*SECOND
 c.replies.append((304,b'',{'etag':'"v1"'}))
 out['retained_old_validator']={'result':w.run_job(k,j,force=True),'request_headers':c.calls[-1]['headers'],'current_quote':json.loads(s.current(k)['normalized'])['records'][0]['unit_price_copper']}
 s.db.close()
ticks=[0.0]
class SlowResponse(Response):
 def read(self,size):
  ticks[0]+=29.0
  return super().read(size)
response=SlowResponse(b'1234567890',chunk_size=1)
with patch('time.monotonic',side_effect=lambda:ticks[0]):
 r=HttpClient(opener=Opener(response),timeout=30).request(PUBLIC_URL)
out['slow_read']={'timeout':30,'elapsed_seconds':r.elapsed_seconds,'status':r.status,'body_bytes':len(r.body),'closed':response.closed,'read_calls':response.read_calls}
with patch('wow_auction.market_worker.load_secrets',side_effect=MarketError('synthetic_disabled_ue_secret_error')) as loader:
 try:
  MarketWorker(None,{'jobs':[{'provider':'tsm','enabled':True}]})
  out['disabled_ue_secret_dependency']='no_error'
 except MarketError as e:out['disabled_ue_secret_dependency']={'error':e.code,'loader_calls':loader.call_count}
print(json.dumps(out,indent=2))

```

N5 с настоящей функцией load_secrets, изолированным окружением и отсутствующим искусственным файлом:

```python
import json,os
from unittest.mock import patch
from pathlib import Path
from wow_auction.market_worker import MarketWorker
from wow_auction.market_common import MarketError
cfg={'jobs':[{'provider':'tsm','enabled':True},{'provider':'undermine','enabled':False}], 'secrets_path':str(Path('docs/reviews/nonexistent-synthetic-key-file.json').resolve())}
with patch.dict(os.environ,{'WOW_UNDERMINE_API_KEY':'SYNTHETIC\nBROKEN'},clear=True):
 try:
  MarketWorker(None,cfg)
  out={'outcome':'unexpected_success'}
 except MarketError as e:
  out={'outcome':e.code,'only_enabled_provider':'tsm','undermine_enabled':False,'real_secret_file_read':False}
print(json.dumps(out))

```

Фактически получено: N1 — три accepted/current_observation с 09:00→08:00→09:00; N2 — возраст 215146806 s и capabilities_currently_usable=true; N3 — If-None-Match v1 при current B, unchanged snapshot2; N4 — elapsed_seconds319.0 при timeout30; N5 — invalid_credential_format при disabled UE. Это подтверждённые отрицательные результаты, не новые PASS-тесты.

## Приложение B. Процедура независимого REAL TSM прогона

Ниже исполнявшийся код. Сеть только через проверяемый клиент, данные хранятся исключительно в памяти. В показанной проверенной конфигурации пять TSM jobs включены, UE отключён; реальный secret loader не вызывается. Обычные CLI market-status/scan аудит не запускал на рабочей БД, поскольку они применяют retention и изменяют служебные записи.

```python
import json,sqlite3,time,hashlib
from pathlib import Path
from datetime import datetime,timezone
from wow_auction.market_config import load_market_config
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker
from wow_auction.market_http import HttpClient
config=load_market_config(Path('.private/market.local.json'))
s=object.__new__(MarketStore);s.path=Path(':memory:');s.db=sqlite3.connect(':memory:',isolation_level=None)
s.db.row_factory=sqlite3.Row;s.db.execute('PRAGMA foreign_keys=ON');s._migrate();s._publish()
class Capture(HttpClient):
 def __init__(self):super().__init__();self.calls=[]
 def request(self,url,**kw):
  r=super().request(url,**kw)
  self.calls.append({'url':url,'status':r.status,'sha256':hashlib.sha256(r.body).hexdigest() if r.body else None,'bytes':len(r.body),'wire_bytes':r.wire_bytes,'received_at_ns':r.received_at_ns,'elapsed_seconds':round(r.elapsed_seconds,3),'authorization_sent':'Authorization' in kw.get('headers',{}),'conditional':'If-None-Match' in kw.get('headers',{}) or 'If-Modified-Since' in kw.get('headers',{})})
  return r
c=Capture();w=MarketWorker(s,config,client=c,secrets={})
out={'started_at':datetime.now(timezone.utc).isoformat(),'first':w.scan(force=True)}
out['snapshots']=[]
for key,job in w.jobs():
 if job['provider']!='tsm':continue
 r=s.current(key)
 if not r:continue
 n=json.loads(r['normalized'])
 out['snapshots'].append({'dataset':job['dataset'],'id':r['id'],'sha256':r['sha256'],'records':r['record_count'],'raw_bytes':len(r['raw']),'normalized_utf8_bytes':len(r['normalized'].encode()),'source_snapshot_at':r['source_snapshot_at'],'received_at_ns':r['received_at_ns'],'available_at_ns':r['available_at_ns'],'scope':n['scope'],'normalizer_revision':n['normalizer_revision']})
time.sleep(1.1)
out['repeat']=w.scan(force=True);out['calls']=c.calls
out['snapshot_count_after_repeat']=s.db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
out['sqlite_quick_check']=s.db.execute('PRAGMA quick_check').fetchone()[0]
out['foreign_keys']=s.db.execute('PRAGMA foreign_key_check').fetchall()
s.db.close();print(json.dumps(out,ensure_ascii=False,indent=2))

```

