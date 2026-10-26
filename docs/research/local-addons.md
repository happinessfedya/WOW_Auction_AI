# Этап 0: локальные аддоны и SavedVariables

Дата исследования: 26.10.2026, Europe/Moscow. Только чтение файлов и одноразовые проверки данных; WoW не запускался и игровые действия не выполнялись. Прочитан пакет `codex_handoff/*.md`. Код приложения и аддон-мост не создавались. Это исследование интеграций, а не успешный интеграционный тест работающего приложения.

Обозначения: **SRC** — проверено по установленным исходникам; **DATA** — проверено на существующих файлах данных; **META** — метаданные файла/установки; **PLAN** — проектное решение; **UNKNOWN** — не проверено. Идентификаторы L01… предназначены для сводного `research-evidence.md`.

## 1. Обнаруженная среда

**L01 [META].** Retail найден по пути `W:\Battle.net\Games\World of Warcraft\_retail_`. В `W:\Battle.net\Games\World of Warcraft\.build.info` активная ветка — `eu`, установленная версия — `12.1.5.70575`. Это версия установленной сборки, не доказательство успешного входа в текущий live-клиент и не подтверждение актуальности всех игровых механик. Приватные столбцы `.build.info` в отчёт не включены. Локальный Python — 3.13.7, что удовлетворяет предпочтению Python 3.12+ для будущей разработки, но зависимости приложения пока не проверялись.

В следующих таблицах `A` означает `W:\Battle.net\Games\World of Warcraft\_retail_\Interface\AddOns`; `SV` — обнаруженный каталог `WTF\Account\<локальный аккаунт>\SavedVariables`. Идентификатор каталога аккаунта намеренно не включён в отчёт. Для воспроизведения следует находить файлы рекурсивно по имени, не переносить этот идентификатор в репозиторий.

**L02 [META/SRC].** Версии непосредственно из TOC:

| Аддон / файл относительно A | Версия | Interface | Объявленные SavedVariables |
|---|---|---|---|
| `TradeSkillMaster/TradeSkillMaster.toc` | v4.14.78 | 120105, 50504, 20506, 11509 | TradeSkillMasterDB, TSMItemInfoDB |
| `TradeSkillMaster_AppHelper/TradeSkillMaster_AppHelper.toc` | v4.14.14 | 120105, 50504, 20506, 11509 | TradeSkillMaster_AppHelperDB |
| `BagSync/BagSync.toc` | 20.38 | 120105, 50504, 40402, 38002, 30405, 20506, 16001, 11509 | BagSyncDB |
| `GX/GX.toc` | 1.1.0 | 120100 | GX_DB |
| `CraftSim/CraftSim.toc` | 27.0.7 | 120005, 120007, 120100 | CraftSimDB |

GX установлен под именем каталога **GX**, поэтому поиск только каталогов с `Gold` его пропускает. Переустановка не требуется по результатам обнаружения. Отличие Interface у CraftSim/GX от установленной сборки — основание проверить совместимость в будущем runtime-тесте, но само по себе не доказывает поломку.

`W:\TradeSkillMaster Application` содержит `TSMApplication.exe` и `TSMUpdater.exe` в подкаталогах. ProductVersion/FileVersion этих двух исполняемых файлов не возвращены Windows-метаданными; версия Desktop App **не установлена**. Исполняемые файлы не запускались, конфигурация авторизации не читалась. Наличие свежего AppData ниже подтверждает ранее состоявшуюся выгрузку данных, не непрерывную работоспособность приложения.

**L03 [META/DATA].** Найдено по одному основному файлу каждого перечисленного типа; `.bak` не считается вторым независимым источником:

| Файл | Байты | LastWriteTimeUtc | Фактическое содержимое, без персональных значений |
|---|---:|---|---|
| `SV/BagSync.lua` | 19 012 | 2026-10-25 23:29:25 | 2 персонажа; суммы денег согласуются с GX; общий warband объект, 1 сохранённая вкладка |
| `SV/GX.lua` | 435 | 2026-10-25 23:29:25 | 2 персонажа, warband, schema version 1, индивидуальные lastSeen |
| `SV/CraftSim.lua` | 269 299 | 2026-10-25 23:29:25 | Наличие подтверждено; содержимое исследует отдельный отчёт CraftSim |
| `SV/TradeSkillMaster.lua` | 123 026 | 2026-09-26 17:59:19 | 6 семейств accounting CSV существуют, но ни одной строки операции |
| `SV/TradeSkillMaster_AppHelper.lua` | — | — | Не найден рекурсивным поиском в WTF |
| `A/TradeSkillMaster_AppHelper/AppData.lua` | 4 283 999 | 2026-10-25 23:50:20 | 9 наборов рыночных данных + APP_INFO |

Для GX `lastSeen` двух персонажей: 2026-10-25 23:29:25 и 23:14:26 UTC, warband: 23:11:07 UTC. Для пользователя это 26.10.2026 02:29:25, 02:14:26 и 02:11:07 Москвы. Свежесть файлов и свежесть отдельных полей различаются.

В существующих записях персонажей BagSync нет сохранённых `auction` и `auction.lastscan`; у одного персонажа `mailbox` присутствует пустым, у другого отсутствует. Поэтому отсутствие аукционных/почтовых предметов **не доказано**: здесь отсутствует подтверждённый осмотр этих областей. Есть bag/bank/equip, валюты и профессии; сам факт наличия таблицы bank не доказывает полноту всех вкладок/персонажей. Имена, GUID, суммы денег и исходные строки торговых данных в документ не включены.

## 2. TSM Ledger: извлекаемые данные и важные ограничения

**L04 [SRC/DATA].** Ledger встроен в установленный TSM: UI в `TradeSkillMaster/Core/UI/MainUI/Ledger`, источники учёта в `Core/Service/Accounting`. Подтверждены схемы:

| Данные | Схема CSV | Точный исходник |
|---|---|---|
| csvSales / csvBuys | `itemString,stackSize,quantity,price,otherPlayer,player,time,source` | `Core/Service/Accounting/Transactions.lua:48–57` |
| csvIncome / csvExpense | `type,amount,otherPlayer,player,time` | `Core/Service/Accounting/Money.lua:18` |
| csvExpired / csvCancelled | `itemString,stackSize,quantity,player,time` | `Core/Service/Accounting/Auctions.lua:31` |

Регистрация полей — `LibTSMSystem/Source/AddonSettings/Schema.lua:387–392`. В реальном SV есть ключи метаданных схемы вида `r@internalData@csvSales` с числовой версией изменения и scope-ключи данных `r@<realm>@internalData@csvSales`; метаданные нельзя принять за CSV мира. Назначение `_lastModifiedVersion` подтверждает `LibTSMTypes/Source/Settings/Classes/DB.lua:188–189`. Фактические строки scope CSV в исследованном файле: expired 356, cancelled 412, income 723, sales 1376, buys 1442, expense 2934. Все содержат только заголовок. **Проверены формат и пустое состояние, успешное извлечение реальной сделки не проверено.** Возраст TSM SV почти месяц; причина (не загружался, был выключен, не записывался, другой сеанс) неизвестна и не должна подменяться диагнозом.

**L05 [SRC]. Критическая семантика цены.** `Core/Service/Accounting/Mail.lua:156–202` читает аукционную почту. Для продажи: `price = floor((bid - ahcut) / quantity + 0.5)`; для покупки: `floor(bid / quantity + 0.5)`. `Transactions.InsertAuctionSale/Buy` передают эти значения в запись. Поэтому sale `price` — **округлённые медные за единицу уже после AH cut**, а не gross цена лота. Повторное вычитание комиссии создаст ложный убыток. `quantity` — количество предметов записи; `stackSize` — размер отдельной исходной операции/стека. После объединения quantity может превышать stackSize. Общая сумма из CSV `price × quantity` может иметь ошибку округления исходной суммы: исходные bid и ahcut в этой CSV не сохраняются.

Временная метка покупки/продажи восстанавливается из времени письма: `time() + (daysLeft - 30) × SECONDS_PER_DAY` (строки 178, 201), затем округляется вниз (`Transactions.lua:787`). Это оценка времени события, а не отдельный серверный transaction timestamp. Импорт обязан сохранить эту семантику и время получения файла отдельно.

Возвращаемый deposit не включён в `(bid - ahcut)`; такая запись корректно не превращает возврат собственного залога в доход. Но точной величины потерянного залога при неудачных выставлениях эти CSV не содержат. В `Auctions.lua` есть предмет/количество/время отмены или истечения без длительности выставления и deposit. В `Money.lua:62–87` предусмотрены Money Transfer, Postage, Repair Bill, Garrison, Crafting Order; отдельного типа исторического AH deposit среди этих insert-функций нет. Следовательно, полный чистый P&L после всех повторных выставлений **не восстанавливается гарантированно только по найденным Ledger CSV**. Дополнительный учёт собственных связей/оценки недостающих затрат оправдан; дублировать весь Ledger не нужно.

**L06 [SRC].** `Transactions.lua:53–55,784–825` объединяет совпадающие операции в пределах 300 секунд, увеличивая quantity; отсутствует глобальный transaction UUID. `SaveData:733–780` при превышении 55 000 записей обрезает до 50 000 и сохраняет признак accountingTrimmed. Для неудачных аукционов `Accounting/Core.lua:44` явно учитывает ограничение истории 180 днями; точную очистку следует проверить тестом версии перед импортом. Повторный снимок и пересобранный CSV — не новый поток событий.

**PLAN:** импортировать неизменяемые версии source snapshot + scope + family; канонические записи согласовывать как мультимножество с multiplicity и изменяемым агрегированным quantity. Один hash всей строки не решает случай, когда TSM дописал quantity в существующую агрегированную строку. Не уничтожать одинаковые реальные сделки глобальным DISTINCT. Исчезновение строки из позднего файла может быть trimming/ручным удалением; оно не означает отрицательную сделку. Вести revisions/tombstones provenance, не переписывать исходный файл. Ручное связывание purchase→transform→sale хранить в своей БД поверх оригинальных записей.

**L07 [SRC].** Поддерживаемые события шире AH: `Transactions.lua:165–194` — Auction, COD, Trade, Vendor; `Money.lua:62–87` — переводы, почта, ремонт, гарнизон, заказы. Но записи возникают при наблюдении соответствующего события/сборе почты, а не являются восстановлением всей истории аккаунта. `Mail.lua:140–153` повторяет сбор данных ограниченное число раз; неразрешённый itemString приводит к ошибке в логе (`180–182`). Покрытие не считать стопроцентным. Переводы между своими персонажами/общим банком классифицировать отдельно от торговой прибыли; невозможность сопоставить обе стороны — `unreconciled_transfer`, не доход по умолчанию.

**L08 [SRC].** `Transactions.OnDisable:156–162`, `Money.OnDisable:53–59`, `Auctions.OnDisable:84–117` сериализуют накопленные данные. `LibTSMWoW/Source/Util/Lifecycle.lua:91–103` обрабатывает PLAYER_LOGOUT. Это согласуется с записью SavedVariables при выходе/перезагрузке интерфейса; **эксперимент до/после `/reload` не проводился**. Python снаружи не получает непрерывный поток внутриигровых изменений. Чтение SV после изменения mtime должно ждать стабильного размера/хеша, разбирать ограниченную грамматику Lua-данных без `eval`, `loadstring` и выполнения произвольного кода, валидировать содержимое и атомарно принимать snapshot. При ошибке сохранить прошлую подтверждённую версию и показать новый сбой. `.bak` допустим только как явно устаревшее восстановление, не дополнительное имущество.

## 3. BagSync и GX: покрытие, свежесть, дедупликация

**L09 [SRC/DATA].** BagSync `wireframe/data.lua:248–258,283–296` хранит `BagSyncDB[realm][character]`, в том числе `guid`, `realmKey`, `rwsKey`, class/faction и money. `wireframe/events.lua:358–363` получает `GetMoney()` минус cursor/trade money. По `wireframe/scanner.lua` доступны bag, bank, equip, mailbox, guild, warband и auctions при соответствующих включённых tracking-флагах/открытии окон. Гильдейские записи отдельны; их нельзя включать в личный капитал по умолчанию.

Warband — **одна таблица** `BagSyncDB["warband§"]` (`data.lua:1302–1304`), не банк каждого персонажа. `SaveWarbandBank:683–747` требует включённого отслеживания и открытого bank/warband UI; сохраняет `tabs[tabID]` со списком предметов. `SaveWarbandBankMoney:750–761` использует `C_Bank.FetchDepositedMoney(Enum.BankType.Account)` и также требует доступного окна. В сохранённых данных есть money и одна tabs-ветка. Нулевое сохранённое значение — наблюдение этого файла, не гарантия текущего остатка.

**L10 [SRC].** `BagSync/core.lua:479–584` хранит сокращённый item link/ID с `;count`, при наличии bonus IDs оставляет их, но очищает другие поля исходной ссылки (`557–570`). Питомцы и ключи имеют специальные представления. `EncodeOpts/DecodeOpts:381–396` добавляет служебные параметры; аукционы кодируют срок истечения (`scanner.lua:910–924`) и пишут lastscan (`964–966`). Общие slotItems часто уплотнены через `slotItems[#slotItems+1]`, поэтому порядковый индекс сохранённого списка не всегда равен физическому слоту.

**Ограничение:** BagSync не даёт в исследованной схеме универсальной гарантии сохранения всех item modifiers/instance binding и точного времени осмотра каждой области. Нельзя по itemID считать всякий предмет ликвидным; экипировку исключать из торговых запасов по умолчанию, индивидуальную привязку и возможность продажи отмечать unknown, пока нет подтверждающих данных. Разные variants не сводить молча к базовому itemID. Для недостающих instance-полей будущий минимальный собственный экспорт оправдан только после теста покрытия существующих источников.

**L11 [SRC/DATA].** GX `GX.lua:44–78,175–205` имеет схему:

```text
GX_DB.version = 1
GX_DB.characters[Name-NormalizedRealm] = { gold, lastSeen }
GX_DB.warband = { gold, lastSeen }
```

`gold` фактически содержит **copper** из GetMoney, не число gold. `87–89` задают 100 silver/copper и 100 silver/gold, то есть 10 000 copper/gold. `115–172` нормализует имя мира (пробелы/апострофы), сливает старые ключи по более свежему lastSeen. GUID, регион, profession и item inventory в этой схеме отсутствуют. Для банка `193–203` не затирает сохранённое значение нулём, если банк недоступен для просмотра; при доступном банке ноль может записаться. `956–983` слушает PLAYER_LOGIN, PLAYER_MONEY, ACCOUNT_MONEY, BANKFRAME_OPENED/CLOSED; `214–223` делает повторное отложенное чтение bank API. Историю операций GX не сохраняет.

**L12 [SRC/DATA].** GX содержит режим автоматического ReloadUI (`442–483`). В исследованном GX_DB отсутствует `autosaveMinutes`; сохранённая настройка не активирует этот режим. Есть только `autosaveNoInstances=true`. Проект не должен включать `/gx autosave`, имитировать ReloadUI или полагаться на автоматические перезагрузки. Наличие такой функции установленного аддона — обнаруженное противоречие возможного использования с запретом ТЗ, а не разрешение её включить. Фактический runtime-таймер не исследовался.

**PLAN — выбор источника поля, а не сложение источников:**

1. Объект персонажа связывать по GUID BagSync, realm/region и нормализованным aliases; GX name-realm — кандидат на связь, не универсальный GUID. При коллизии не сливать автоматически.
2. Деньги персонажа: предпочитать подтверждённый свежий GX lastSeen, сверять BagSync/TSM. Последний источник не всегда лучший: фиксировать качество и время наблюдения каждого поля. Файл с новым mtime может содержать старое значение другого персонажа.
3. Warband — один ownership scope на подтверждённую группу общей собственности; не суммировать GX/BagSync/TSM и не умножать на число персонажей. При нескольких WoW-лицензиях связь общей собственности устанавливать явно; папка SV сама по себе не доказывает отдельный warband.
4. Bag/bank/auction/mailbox — альтернативные наблюдения соответствующей области. Не складывать BagSync total с TSM Inventory total. Не выводить «все активы актуальны» по одному свежему файлу; старые местоположения при переносе могут давать один предмет дважды. Хранить reconciliation/conflict и диапазон оценки при невозможности подтвердить движение.
5. Свежесть денежных данных GX не переносить на имущество BagSync. Для per-container времени, которого нет в источнике, хранить `observed_at=null`, `file_written_at`, `imported_at` и статус freshness unknown; manual confirmation отдельным событием.

**L13 [SRC].** TSM тоже имеет деньги/warbank, поэтому подключение всех трёх источников без reconciliation опасно: `Schema.lua:172–175,437–444`, `Accounting/GoldTracker.lua:54–66,225–245` — warbankMoney, warbankGoldLogLastUpdate, money и goldLogLastUpdate. `LibTSMApp/Source/Service/Inventory.lua:36–65` включает bag/bank/auction/mail/warbankQuantity. Существующие TSM остатки в старом SV не выбираются как более свежие без проверки. GoldLog — временной ряд изменения богатства, не доказательство торговой прибыли: он включает переводы, добычу и иные движения.

## 4. TSM Desktop App / AppHelper: реальный рыночный файл

**L14 [DATA/SRC].** `A/TradeSkillMaster_AppHelper/AppData.lua` — выход Desktop App, **не** SavedVariables игрового клиента. Первые 9 строк содержат `select(2,...).LoadData(tag, realmOrRegion, payload)`. Подтверждены категории и поля:

| Строка / tag | Scope | fields | downloadTime UTC |
|---|---|---|---|
| 1 NON_COMMODITY_DATA | Свежеватель Душ | itemString,minBuyout,numAuctions,marketValueRecent | 2026-10-25 23:31:40 |
| 2 NON_COMMODITY_HISTORICAL | тот же мир | itemString,historical | 2026-10-25 00:33:49 |
| 3 NON_COMMODITY_SCAN_STAT | тот же мир | itemString,marketValue | 2026-10-25 23:31:40 |
| 4 REGION_SALE | EU | itemString,regionSale,regionSoldPerDay,regionSalePercent | 2026-10-25 01:02:25 |
| 5 REGION_HISTORICAL | EU | itemString,regionHistorical | 2026-10-25 01:02:27 |
| 6 REGION_STAT | EU | itemString,regionMarketValue | 2026-10-25 00:27:04 |
| 7 COMMODITY_SCAN_STAT | EU | itemString,marketValue | 2026-10-25 23:31:40 |
| 8 COMMODITY_HISTORICAL | EU | itemString,historical | 2026-10-25 00:26:50 |
| 9 COMMODITY_DATA | EU | itemString,minBuyout,numAuctions,marketValueRecent | 2026-10-25 23:31:40 |

Все tag имеют префикс `AUCTIONDB_`. Строка 10 APP_INFO содержит lastSync=1792972220 (2026-10-25 23:50:20 UTC). Название downloadTime не переименовывать в «время всех сделок рынка» или «точный момент снимка Blizzard» без отдельного контракта. `minBuyout`/marketValue — не полная глубина предложений; исторические агрегаты не являются сырым часовым архивом. `regionSalePercent` и `regionSoldPerDay` не подтверждают объём всего рынка/вероятность исполнения собственной позиции без проверки методологии и выборки.

`LibTSMApp/Source/Service/AppHelper.lua:42–55,69–106` различает realm/region/commodity наборы; `Core/Service/AuctionDB/Core.lua:176` возвращает metadata.downloadTime. Сам TSM использует `loadstring` для части AppData (`AppHelper.lua:82`); внешнее приложение не должно копировать этот небезопасный способ — только ограниченный разбор проверенного формата, с лимитами и отказом на произвольном Lua.

**L15 [DATA].** Автоопределение возможно: AppData указывает регион EU и мир «Свежеватель Душ»; BagSync содержит тот же мир при сравнении без учёта регистра. Разница с написанием «Свежеватель душ» из ТЗ — капитализация, не другой сервер. Для окончательного market scope всё ещё нужны подтверждённые realm ID/connected-realm ID из справочника/Blizzard API; не выводить их из русского имени наугад. Настройка ручного выбора обязательна. При конфликте источников показывать кандидаты и времена, не молча выбирать наиболее удобный.

**L16 [SRC/UNKNOWN].** `TradeSkillMaster/LICENSE.md` — all rights reserved, с ограниченными исключениями использования аддона/личных изменений и запретом распространения без согласия. `AppHelper/LICENSE.md` также присутствует. Просмотр установленных исходников не даёт права копировать их в приложение или распространять рыночную базу. Сохранять ссылки/контракты, собственные адаптеры и свои игровые записи; возможность внешнего использования/хранения/распространения AppData должна проверяться по действующим условиям TSM отдельно. Локальный факт наличия файла не заменяет разрешённый публичный API и его условия. Юридическое заключение в этом исследовании не делалось.

## 5. Что проверено, а что ещё требуется

**Проведённые проверки:** обнаружение установки, TOC, hashes; чтение конкретных функций; безопасный одноразовый синтаксический разбор GX и BagSync без исполнения Lua, подсчёт объектов и сравнение денежных данных без их публикации; извлечение заголовков/числа строк TSM CSV; метаданные и поля AppData. Приватные файлы не скопированы в репозиторий. Аддоны не модифицировались. Никаких реальных или виртуальных сделок не проводилось.

**Не выполнено / не утверждается:** загрузка аддонов на 12.1.5; совместимость runtime API; полнота банков/почты/аукциона; реальная строка Ledger; корректность относительных timestamp при задержках; before/after `/reload`; реакция будущего импортера на повреждения/частичную запись; непрерывная работа TSM Desktop; права на массовый AppData импорт. Наличие кода обработки события не равно тесту его выполнения.

Минимальные будущие подтверждения, без переустановок:

- Для Ledger: уже существующие непустые записи покупок/продаж/отмен/истечений либо добровольно выполненные пользователем обычные действия после разрешения этапа 1; до/после сохранённые версии с обезличенным контрольным примером. Не требовать купить что-либо специально сейчас.
- Для имущества: пользовательский осмотр сумок, личного банка, warband, почты, своих аукционов с обычным ручным `/reload`/выходом; сверка количества и полей до/после перемещения, включая один и тот же предмет в нескольких источниках. Не автоматизировать.
- Для нулевого warband: тест доступного/недоступного банка и не затирающего неизвестность поведения; ноль и unknown должны оставаться разными состояниями.
- Для комиссии: контрольная непустая sale CSV и игровое письмо invoice подтверждают unit/net трактовку, округление и возврат deposit. Отдельный тест потерянного deposit покажет фактический пробел учёта.
- Для TSM обновления: несколько естественных изменений AppData с отдельно сохранёнными file time, downloadTime и hash; не обещать почасовую частоту по одному наблюдению.

## 6. Воспроизведение и контрольные хеши

Команды только читают данные; полный текст SavedVariables выводить не следует. Абсолютный корень можно задавать локально:

```powershell
$wowResearchRoot = 'W:\Battle.net\Games\World of Warcraft\_retail_'
$wowResearchAddons = Join-Path $wowResearchRoot 'Interface\AddOns'
foreach ($name in @('TradeSkillMaster','TradeSkillMaster_AppHelper','BagSync','GX','CraftSim')) {
  Get-ChildItem -LiteralPath (Join-Path $wowResearchAddons $name) -Filter '*.toc' |
    ForEach-Object {
      Select-String -LiteralPath $_.FullName -Pattern '^## (Interface|Version|SavedVariables):'
    }
}
Get-ChildItem -LiteralPath (Join-Path $wowResearchRoot 'WTF') -Recurse -File |
  Where-Object Name -In @('BagSync.lua','GX.lua','TradeSkillMaster.lua','CraftSim.lua','TradeSkillMaster_AppHelper.lua') |
  Select-Object Name,Length,LastWriteTimeUtc
rg -n 'CSV_KEYS|COMBINE_TIME_THRESHOLD|MAX_CSV_RECORDS|function Transactions.OnDisable' (Join-Path $wowResearchAddons 'TradeSkillMaster\Core\Service\Accounting\Transactions.lua')
rg -n 'bid - ahcut|InsertAuctionSale|InsertAuctionBuy' (Join-Path $wowResearchAddons 'TradeSkillMaster\Core\Service\Accounting\Mail.lua')
rg -n 'CheckWarbandBankDB|SaveWarbandBankMoney|lastscan' (Join-Path $wowResearchAddons 'BagSync\wireframe')
```

Для повторения проверки пустых Ledger CSV следующий ограниченный read-only Python-код разбирает только строковые литералы через JSON, не исполняет Lua, не выводит scope или значения операций. При изменении Lua escape-синтаксиса он может отказать — это ожидаемо для диагностической проверки, не готовый импортёр:

```python
from pathlib import Path
import re, json
root = Path(r'W:\Battle.net\Games\World of Warcraft\_retail_\WTF')
families = ('csvSales', 'csvBuys', 'csvIncome', 'csvExpense', 'csvExpired', 'csvCancelled')
for path in root.rglob('TradeSkillMaster.lua'):
    for number, line in enumerate(path.read_text('utf-8-sig').splitlines(), 1):
        key = re.search(r'\["([^"\n]+)"\]', line)
        parts = key.group(1).split('@') if key else []
        family = parts[-1] if len(parts) == 4 and parts[-1] in families else None
        if family:
            match = re.search(r'=\s*("(?:[^"\\]|\\.)*")', line)
            if not match:
                raise ValueError('Unsupported diagnostic string format')
            value = json.loads(match.group(1))
            print(family, number, 'data_rows', max(0, len(value.splitlines()) - 1))
```

Для исходников SHA-256 вычислен при исследовании. Пути ниже относительно A:

| Файл | SHA-256 |
|---|---|
| BagSync/BagSync.toc | fb6b2209230612727a3bee8354d774da09a1610028f7a3d37c8d00710833c4e4 |
| BagSync/core.lua | 82c960db86e51bdafc848b665b792dfda17c0016ba2fb82f787a29b2d49e976a |
| BagSync/wireframe/scanner.lua | 6966a2882985ae6f332da7cdd1906e0ab81527e1e5f93ec532ea7819a0016cad |
| BagSync/wireframe/data.lua | fa35ffe1aa99b27f035b417d1fcd56cb30e79517f1a435196015932a135fc7f1 |
| GX/GX.toc | f0262a417bcbe36a31e058f56f2472066274415b205542962e1550cb59d0709a |
| GX/GX.lua | 831bca30b81b4a6e88f35ab254072f84ac0e797ecde378f5bdd2f4c8aa6838e4 |
| TradeSkillMaster/TradeSkillMaster.toc | 4a685b1a79533214b904b15f2c64cad0ddcadfa75357eb696a98d58091e9c05f |
| TradeSkillMaster/Core/Service/Accounting/Transactions.lua | 7fec75992e3ce8118a7a2639ad9e3cdc560a5426ae632f9ceb10a64cc8ab1df1 |
| TradeSkillMaster/Core/Service/Accounting/Mail.lua | 8e2646298eeb0553ba2f9f1ac601b942f762772d1d4378d7fc1a59f88dafbaef |
| TradeSkillMaster/Core/Service/Accounting/Money.lua | e611fcc10d829565f380dcda03e92e940739f64249bb15f94cdf0ffd31905d6e |
| TradeSkillMaster/Core/Service/Accounting/Auctions.lua | 0bc11ab048eb782b66b090056d46e87ac7e5945c1a645e06439252862911c70e |
| TradeSkillMaster_AppHelper/TradeSkillMaster_AppHelper.toc | 4a99df46fb505c922c4540f845f5ac60401467ae447e705f032221ab720b6179 |
| TradeSkillMaster/LibTSMApp/Source/Service/AppHelper.lua | 957f2dc7755ce207af12d9cd06575c638c8bbec318413b1344392f1db2dac7ad |

Контрольные SHA-256 основных SV (для локального сверяющего, без публикации содержимого):

| Имя | SHA-256 |
|---|---|
| GX.lua | 0f5a994107e611d357405e9823c76f7647396a86d1aed84e02c368682bac9850 |
| BagSync.lua | e18e791f8d649a8fe6587ac339d82537ee1119934b7ce57244df5583ebcc3c8c |
| TradeSkillMaster.lua | 1261ccfda9d7cb199b0535cbe6ebbbe1826b8ff8276db5f4ed356068d37fd9f1 |
| CraftSim.lua | 5413dbc15f6d9614a267ce7ef221641330807e013037f33c2104d62cf5707c43 |

Файлы могут естественно обновиться после игры/Desktop App: несовпадение хеша требует повторной фиксации версии, а не утверждения, что старое исследование относится к новым байтам.

## 7. Дополнительная фиксация R2 после аудита

26.10.2026 00:42:21 UTC (03:42:21 Москвы) повторены диагностические блоки Ledger и Auctionator: шесть CSV по0 операций; Auctionator block28 bytes и0 полей price/quantity/time/bidPrice. Повторное чтение **метаданных и hashes**, без выгрузки имён/денег, дало:

| Имя | Байты | mtime UTC | SHA-256 |
|---|---:|---|---|
| GX.lua | 553 | 2026-10-26 00:03:22 | `0cfd4f0af10df67ef311616c2c4af210a979608884a458b324e5fd2af1060fe1` |
| BagSync.lua | 28208 | 2026-10-26 00:03:22 | `2e30c659047d17938ad6b7fedcfe902e9c610f1654aebfd37442e40a4e4d4233` |
| CraftSim.lua | 269302 | 2026-10-26 00:03:22 | `55ee49aa9756730ff8caf832fa70dec2dac4180f834591e4da0367d87e017c95` |
| TradeSkillMaster.lua | 123026 | 2026-09-26 17:59:19 | `1261ccfda9d7cb199b0535cbe6ebbbe1826b8ff8276db5f4ed356068d37fd9f1` |

Они совпадают со снимками независимого аудитора V05/V06. Его отдельное структурное чтение установило3 персонажа GX/BagSync, согласование денег и поле auction у одного персонажа. В R2 эти counts не объявляются новым независимым структурным разбором разработчика: здесь заново выполнены metadata/hash и два указанных диагностических блока. Исходные L03 с2 персонажами остаются верным свидетельством прежних байтов; они не описывают текущую версию. Ни flush, ни runtime игры не запускались. Источник: [аудит R1, §6](../reviews/stage-0-review.md).
