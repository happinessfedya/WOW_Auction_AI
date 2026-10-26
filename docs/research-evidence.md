# Реестр доказательств этапа 0

Дата проверки: **26.10.2026 Europe/Moscow**, ревизия разработчика R2 после аудита R1; часть первоначальных файлов и HTTP имеет UTC дату 25 октября. Исходные timestamps не переименованы в местные. Реализация приложения отсутствует. Веб-страницы читались при исследовании, а не только по памяти; реальные HTTP-пробы обозначены отдельно. Неизвестные credentials не искались в пользовательских конфигурациях. Ниже исходные L/C/M факты не переносятся молча на новые байты; новые свидетельства R2 отделены.

## Дополнение R2 и ответы на аудит

| ID | Источник / проверка | Статус и предел |
|---|---|---|
| E-R2-REVIEW | [Независимый аудит R1](reviews/stage-0-review.md), прочитан целиком; SHA-256 `0d014ecac0612cf3d99951eb5bb5e8d4fcc1e8e3939cb32d8652c4466272cc7b` | R: вердикт НЕ ПРИНЯТ, 5 замечаний S0-01–05. Файл аудитора не редактировался; исправления разработчика не новый вердикт |
| E-R2-BASE | [Исходный пакет R1](research/baselines/stage-0-r1.zip) | R: перед исправлениями сверены все22 hashes manifest, архив содержит22 файла+manifest. Archive SHA `e1ebea9524cbca41746bdc35db12c20f0fa16d034642c9f601a657d0947b8bba`; исходный manifest SHA `e713ab3d96ea24a1796fe52e2e21491e10f1ca67e8f5737a80613f33ce4d8cfe` |
| E-R2-DISCOVERY | [D2 — конкретные источники, закреплённые ревизии, пробы и traversal](research/transformation-discovery-r2.md) | D/S/R/B по каждой записи: source enumeration/sample не semantic import/current-game proof; coverage и лицензии раздельно. Отвечает S0-01 |
| E-R2-LIFECYCLE | [SL2-01–09](research/strategy-lifecycle-r2.md) | H/specification: lifecycle/monitor/authority/atomic pause/rollback/open positions;12 будущих LC2 tests PLAN. Отвечает S0-02, не работающий supervisor |
| E-R2-TEMPORAL | [T2-S01–03, C01–07, A01–14](research/temporal-evaluation-r2.md) | D: первичные Look-Ahead-Bench и UE прочитаны; H: mode/publication/context contracts;14 будущих tests PLAN. Не тест реальной модели, отвечает S0-03/05 |
| E-R2-FACTOR | [F2-01–04](research/factor-discovery-r2.md) | H/specification: open observations→candidate→novelty→temporal→experiment; future blind positive/withheld test, не результат discovery агента. Отвечает S0-04 |
| E-R2-SV | [local-addons §7](research/local-addons.md), metadata/hash read 2026-10-26T00:42:21Z | R: новые GX/BagSync/CraftSim hashes совпали с аудитом; повторены Ledger6×0 и Auctionator0 probes. Counts3 персонажа — структурный результат аудитора, не повторённый здесь parser |
| E-R2-CHECKS | [stage0-r2-checks.md](research/stage0-r2-checks.md); повтор stage0-checks/local-addons/addon-inventory | SYN:18/18 R2 contract counterexamples и13/13 исходных примеров; R: реальные пустые CSV/блок. Не приложение/LLM/атомарность БД; полный протокол в [ответе](review-responses/stage-0-r1-response.md) |

Внутренние вычитки субагентов уточнили новый фактор-взаимодействие без обязательного нового источника и запрет включения contaminated historical LLM score даже как части promotion metrics. Они не заменяют независимую повторную приёмку.

## Шкала доказательств и правила чтения

- **D — документировано** владельцем/автором. Не означает успешный запрос/загрузку аддона.
- **S — проверено по исходнику** конкретной установленной версии или указанного удалённого файла. Не означает выполнение ветви.
- **R — реальные данные/метаданные/ответ** получены. `R(401)` не равно рабочей интеграции; `R(empty)` не равно проверенной сделке.
- **H — гипотеза/предлагаемое решение**. Собственные архитектурные решения обоснованы в architecture/data-model/economics; они не факты об API.
- **B — проверка не выполнена/заблокирована** указанным внешним условием. Открытые запросы: [Q01–Q11](open-questions.md).

Локальные META/DATA/SRC в исследовании L соответствуют R/R/S здесь. Точные пути, строки, хеши и команды раскрыты в трёх первичных исследовательских приложениях. Номера строк относятся к байтам на дату проверки; после обновления аддона нужно сверить hash. Приватные SV в Git не копировались. Ни один источник не получает статус confirmed-current только потому, что установлен недавно.

## Среда, исходные требования и выбранные технологии

| ID | Существенный факт / источник | Статус, результат и предел |
|---|---|---|
| E-ENV-01 | `rg --files` и `Get-ChildItem -Force` в `W:\VSProjects\WOW_AUC_AI`; `git log -1 --format='%h %s'` | R: baseline `13be866 first commit`, корневой README и 6 handoff MD; исходников/тестов приложения нет. Исходный handoff уже untracked, сохранён без изменений |
| E-ENV-02 | Все `codex_handoff/*.md` и `README.md`, прочитаны целиком; ссылки перечислены `rg -n '\.md|https?://' codex_handoff` | R: 6 файлов связаны между собой; 8 docs путей — создаваемые результаты, не потерянные входы. MAIN_CODEX_CORRECTION приоритетен |
| E-ENV-03 | Проверка `W:\AGENTS.md`, `W:\VSProjects\AGENTS.md`, workspace `AGENTS.md`; `rg --files -g AGENTS.md` | R: применимых AGENTS.md не найдено в проверенных путях |
| E-ENV-04 | `python -c "import sys,sqlite3; print(sys.version.split()[0]); print(sqlite3.sqlite_version)"` | R: Python3.13.7, SQLite3.50.4; зависимости будущего приложения не установлены/не тестировались |
| E-ENV-05 | `Wow.exe` ProductVersion; `.build.info` активная eu запись [L01](research/local-addons.md) | R: 12.1.5.70575; runtime/live состояние не проверено |
| A01 | Все непосредственные AddOns каталоги и TOC: [addon-inventory.md](research/addon-inventory.md) | R+S metadata: 52 каталога, 145 TOC, 0 каталогов без TOC; включение в игре не доказано; aggregate SHA в приложении |
| A02 | Auctionator `Source/PostingHistory/Mixin.lua:22–24`, `Source_ModernAH/Tabs/Selling/Mixins/SaleItem.lua:735–740`; узкий bytes probe SV, hash в [инвентаризации](research/addon-inventory.md) | S+R(empty): posting price/quantity/bidPrice/time, без deposit/duration/outcome; 0 реальных записей. Не закрывает L05. Strict UTF-8 всего файла отвергнут, причина B; никакого lossy decode/import |
| E-TECH-01 | [SQLite WAL](https://www.sqlite.org/wal.html), §§1,2.2,4,11, чтение 26.10.2026 | D: один писатель; WAL требует локального host; backup учитывает WAL; WAL-reset исправлен в 3.51.3+, backports3.50.7/3.44.6. R local3.50.4. H: выбрать исправленную сборку до WAL, не утверждать воспроизведённую порчу |
| E-TECH-02 | [FastAPI features](https://fastapi.tiangolo.com/features/), OpenAPI/Validation | D: OpenAPI/JSON Schema/Pydantic; H: подходит DTO-first локальному backend. Установка/нагрузка не проверены |
| E-TECH-03 | [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) | D: задачи после HTTP ответа, отдельная оговорка для тяжёлой работы. H: долговечный scheduler/worker нужен отдельно, не обещать надёжность in-process callback |
| E-GAME-01 | [Blizzard 9.2.7 notes](https://news.blizzard.com/en-us/article/23833174/shadowlands-9-2-7-update-notesnow-live), Auction House update | D: введён региональный commodity рынок; прочее realm-specific. Текущий connected-realm контракт отдельно M08; историческую статью не использовать как текущую поддержку Companion App |
| E-AI-01 | [OpenAI Usage/Costs cookbook](https://developers.openai.com/cookbook/examples/completions_usage_api#setup-api-credentials-and-parameters), fetched official docs | D: пример организационной статистики использует Admin key. H: не делать обязательными расширенные права; стоимость unknown при недоступности. Live платных calls не было |
| E-AI-02 | [OpenAI Authentication](https://developers.openai.com/api/reference/overview#authentication), fetched official docs | D: ключ секретен, на сервере/в окружении, не браузер. H: provider abstraction/secret storage/лог redaction. Наличие ключа пользователя не проверялось |

## Локальные интеграции L01–L16

Корень исходников: `W:\Battle.net\Games\World of Warcraft\_retail_\Interface\AddOns`. **Точный расширенный источник каждой строки, команды и SHA-256: [local-addons.md](research/local-addons.md)**.

| ID | Точный предмет проверки | Статус / что установлено |
|---|---|---|
| L01 | `.build.info`; локальная установка | R: EU/12.1.5.70575 |
| L02 | TOC пяти аддонов | R+S: TSM4.14.78, AppHelper4.14.14, BagSync20.38, GX1.1.0, CraftSim27.0.7; Desktop version B |
| L03 | Именованные SV metadata и ограниченный structural read | R: BagSync/GX по2 персонажа, согласование денег; TSM старый/пустой; coverage не полное |
| L04 | TSM `Accounting/Transactions.lua:48–57`, `Money.lua:18`, `Auctions.lua:31`, `Schema.lua:387–392`; реальные scoped CSV | S+R(empty): шесть схем, все только header |
| L05 | TSM `Accounting/Mail.lua:156–202`, `Transactions.lua:787`, `Money.lua:62–87` | S: округлённый net copper/unit, время из письма, неизвестные потерянные залоги; B живой invoice |
| L06 | TSM `Transactions.lua:53–55,733–825`, `Accounting/Core.lua:44` | S: combine300s, mutable quantity, trim; H reconciliation revisions |
| L07 | TSM `Transactions.lua:165–194`, `Money.lua:62–87`, `Mail.lua:140–182` | S: разные event types, возможные пропуски; B полнота истории |
| L08 | TSM OnDisable/Lifecycle `PLAYER_LOGOUT` | S: сериализация; B before/after flush experiment |
| L09 | BagSync `wireframe/data.lua:248–296,1302–1304`, scanner bank/warband, events money | S+R: owner/schema/данные; B все контейнеры |
| L10 | BagSync `core.lua:381–396,479–584`, scanner auctions | S: сокращённая ссылка, неполная variant/binding/time информация |
| L11 | GX `GX.lua:44–78,115–223,956–983`; GX_DB | S+R: gold фактически copper, lastSeen, singleton bank, нет GUID/region |
| L12 | GX `GX.lua:442–483`; сохранённая настройка | S+R: autosave code есть, autosaveMinutes в SV нет; runtime таймер B; проект не использует |
| L13 | TSM `Schema.lua:172–175,437–444`, GoldTracker/Inventory | S: дополнительные пересекающиеся активы; goldLog не profit |
| L14 | `TradeSkillMaster_AppHelper/AppData.lua:1–10`, AppHelper service | S+R: 9 datasets+APP_INFO, поля/times; нет глубины |
| L15 | AppData и BagSync realm/region | R: EU/«Свежеватель Душ»; H auto-detect; B numeric connected ID |
| L16 | TSM/AppHelper LICENSE.md | S: права ограничены; B конкретное право перераспространения/долгого AppData archive |

## CraftSim и преобразования C01–C22

Корень CS: `...\Interface\AddOns\CraftSim`. **Точные строки, исходные URL, hashes, формулы и coverage: [craft-transformations.md](research/craft-transformations.md)**.

| ID | Источник | Статус / установленное |
|---|---|---|
| C01 | CraftSim.toc:1,20,26–28 | S: версия/Interface/SV, runtime B |
| C02 | Util/API.lua:6–93 и API_README.md; [автор](https://github.com/derfloh205/CraftSim/blob/main/Util/API.lua) | D+S: реальные Lua entrypoints, не Python API |
| C03 | API_README и Classes/RecipeData.lua | D+S: устаревший путь README, deprecated method |
| C04 | RecipeData.lua:107–153,266–342,2704–2725 | S: зависимость от WoW runtime/кэша |
| C05 | RecipeData.Update/ResultData/ProfitCalculation | S: движок расчётов/модельные outcomes; runtime B |
| C06 | RecipeData.GetJSON и ResultData.lua:219–269 | S: экспорт неполон для воспроизводимости |
| C07 | Util/Events.lua:24–31; API.lua:76–78 | D+S: callbacks; H мост, B реальный порядок/events |
| C08 | DB/lastCraftingCostDB.lua:9–88 | D+S: cache cost/time, не полный расчёт |
| C09 | Реальный CraftSim.lua SV | R: lastCraftingCostDB пуст, 0 нужных result/cost ключей |
| C10 | Реальный CraftSim.lua repositories; DB/crafterDB.lua:16–28 | R+S: crafter/concentration/cooldown поля; полнота B |
| C11 | Pricing/ProfitCalculation.lua:118,140–148,190–197,251–254; PriceAPIs/Const | S flow, H defect impact: потенциальный повтор deposit; не воспроизведён |
| C12 | RecipeData.lua:291; ProfitCalculation.lua:32–43,175–178 | S: средние/независимость как model assumptions; не empirical joint PMF |
| C13 | CraftSim/LICENSE | S: MIT для кода, не всех игровых данных |
| C14 | TSM LibTSMData Retail Destroy/Convert, Conversion.lua:40–63 | S: обнаружены таблицы-кандидаты; semantic import не выполнялся |
| C15 | TSM/LICENSE.md:1–13 | S: all rights reserved; B reuse/redistribution seed |
| C16 | [WoWDBDefs](https://github.com/wowdev/WoWDBDefs), SpellReagents/ItemExtendedCost | D+S remote: схемы, не фактический DB2 дамп |
| C17 | [WoWDBDefs LICENSE](https://github.com/wowdev/WoWDBDefs/blob/master/LICENSE.md) | D: лицензия схем/code отдельно, не лицензия всех Blizzard data |
| C18 | [Blizzard generated API mirror](https://github.com/Gethe/wow-ui-source/tree/live/Interface/AddOns/Blizzard_APIDocumentationGenerated) | D primary-generated in mirror: TradeSkillUI/MerchantFrame; runtime B |
| C19 | wago.tools/wow.tools и кандидат CSV endpoint | B: инструмент403, bulk контракт/данные не установлены |
| C20 | Wowhead terms; [Warcraft Wiki policy](https://warcraft.wiki.gg/wiki/Fair_use) | B Wowhead; D собственная policy wiki; bulk права не доказаны |
| C21 | ProfitCalculation.lua:274–283 | S: `0,{}` fallback; H импорт validity status, B классификация в игре |
| C22 | Пять адресных Retail source примеров с ID/коэффициентами/строками в [C-отчёте](research/craft-transformations.md) | S: conversion/vendor/mill/prospect/disenchant кандидаты; batch/PMF/current mechanics B; не готовая импортированная база |

Все expansions/category строки запланированы в C-отчёте. Числа строк локальных TSM таблиц — лексические counts, не число доказанных уникальных операций. Изначально imported confirmed records=0, поскольку этап0 не импортирует базу приложения.

## Рыночные и игровые источники M01–M12

**Точные URL методов, headers/times/count/hash и команды: [market-sources.md](research/market-sources.md)**.

| ID | Источник | Статус / установленное |
|---|---|---|
| M01 | [Undermine API](https://undermine.exchange/api.html) | D: опубликованный контракт/auth/квота/история/clock semantics; B authenticated data |
| M02 | `https://api.undermine.exchange/v1/static/realms.json` | R:401 без ключа, не успешная интеграция |
| M03 | [Shatari](https://github.com/erorus/shatari), battlenet.js blob | S: OAuth/namespace implementation автора; лицензия кода отдельно от data |
| M04 | [TSM support](https://support.tradeskillmaster.com/en_US/api-documentation/tsm-public-web-api), [Public Data](https://tradeskillmaster.com/public-data) | D: актуальный CSV путь; старый auth API не выбран |
| M05 | Public TSM EU commodities/soulflayer/region CSV | R:200, 12162/11027/31186 rows; схемы/zeros/times; depth отсутствует |
| M06 | TSM definitions value sources/market value | D: статистические показатели; B текущая commodity denominator semantics |
| M07 | TSM support accounting export/missing transactions | D: экспорт/timestamps/возможные пропуски; R local отдельно L04 |
| M08 | [Official Game Data JSON](https://community.developer.battle.net/api/pages/content/documentation/world-of-warcraft/game-data-apis.json) | R(document200)+D paths/namespaces; B auth responses; без Bearer404 не missing endpoint |
| M09 | Official using-oauth/getting-started JSON | D: client credentials/token/36k per hour+100/sec; B live auth |
| M10 | [Blizzard API terms](https://www.blizzard.com/ko-kr/legal/a2989b50-5f16-43b1-abec-2ae17cc09dd6/blizzard-developer-api-terms-of-use), [FAQ](https://us.forums.blizzard.com/en/blizzard/t/data-protection-notice-and-faq/609), [TSM terms](https://tradeskillmaster.com/terms) | D: тексты; B разрешение противоречия 30-day/long history. Не юридическое заключение |
| M11 | [Blizzard CS 12.1.5](https://us.forums.blizzard.com/en/wow/t/midnight-1215-info-known-issues/2377638) | D: опубликованный контекст патча; R local build отдельно E-ENV-05 |
| M12 | Приведённые в M-отчёте публичные GET commands | R: команды выполнялись; no credentials/no saved full CSV; hashes воспроизводимы до обновления источника |

## Входные контрольные суммы

SHA-256 исходного пакета; проверены до правок docs. Handoff не редактировался.

| codex_handoff файл | SHA-256 |
|---|---|
| FULL_MAIN_PROMPT.md | B9EBFADB6E363C1C517B11351E0021F51B11DA53928902A7DB21F198B1E7DC6D |
| PROJECT_CONTEXT.md | 21BDB9B334CCDE2FFA2583FC8720E30886BB4E4732261F8D048F1A5D53E8604C |
| MAIN_CODEX_CORRECTION.md | 54B8A512DF2E7098EFE8B53B0C8933925C17845AF95A1B8A6D509CB030469EF8 |
| STAGE_WORKFLOW.md | 98EC181C6029A010B1C261938E1CD681099DBE3A22C5ABC78A3AC6DBFCFA8E1A |
| INDEPENDENT_REVIEWER_PROMPT.md | D30F3238600C65F35D1FBB6EB1E70E0C174405A96B8F34BD554FEF83F0A059B0 |
| README.md | 8FC4C0A3ACD2BC2F6C7D948DA93258EC40D4FD98560AAB19B9A16DA777E19308 |

## Что нельзя заключать из этого исследования

Не проверены runtime аддонов, полнота Ledger/банков, auth data UE/Blizzard, connected-realm numeric ID, полный каталог преобразований, игровые вероятности, выигрыш стратегии, причинность событий, работоспособность приложения. Synthetic PASS доказывает только контрольную арифметику/условие примера. История цен не доказывает сделки. Политика хранения, право seed и ограничения платного доступа требуют внешнего подтверждения. Эти пробелы включены в [риски](risks.md), [вопросы](open-questions.md) и [приёмочные тесты](acceptance-tests.md).
