# Полная инвентаризация каталогов аддонов Retail

Дата: 26.10.2026. **Инвентаризация метаданных и отдельная узкая проверка Auctionator A02 ниже; без запуска игры и runtime-проверки.** Корень: `W:\Battle.net\Games\World of Warcraft\_retail_\Interface\AddOns`.

Включены все непосредственные подкаталоги корня и все `*.toc` непосредственно в каждом из них. Вложенные библиотеки не перечислены как отдельные установленные аддоны. Наличие каталога/TOC не подтверждает, что аддон включён, загружается успешно или совместим с установленной сборкой. Несколько TOC одного каталога обычно описывают варианты клиента; таблица не утверждает, какой именно TOC загрузил клиент. Значения Title/Version/Interface переписаны из заголовков файлов, цветовые коды Title сохранены; отсутствие поля отмечается `—`.

Это дополнение к [детальному исследованию пяти основных аддонов](local-addons.md), не замена проверки их реальных схем. Точечные потенциально полезные источники, найденные дополнительно:

| Кандидат | Что подтверждено только TOC | Потенциальная роль / ограничение |
|---|---|---|
| Auctionator 341 | Объявляет AUCTIONATOR_PRICE_DATABASE, AUCTIONATOR_POSTING_HISTORY, AUCTIONATOR_VENDOR_PRICE_CACHE и другие SavedVariables | Узкая проверка posting history выполнена в A02: поля известны, реальная история пуста, deposit/duration не сохраняются; пробел потерянных залогов не закрыт. Остальные данные и лицензия здесь не исследованы |
| LootAppraiserReloaded 3.5.1 | Описание учёта добычи/относительной AH-стоимости; LootAppraiserReloadedDB, LALootDB | Кандидат собственных наблюдений добычи. Оценка AH не выручка; выборка добычи не доказанная распределённость случайных преобразований |
| GatherMate2 1.52.3 и GatherMate2_Data v41.0 | Категории мест сбора/рыбалки/сокровищ; модуль данных назван Wowhead dump | Discovery контекста предложения/ресурсов; не база всех преобразований. Права на переиспользование содержимого и актуальность отдельно не проверены |
| MyusKnowledgePointsTracker 0.8.12 | Описание отслеживания очков знаний профессий; MKPT_Config | Возможный дополнительный контекст крафтера; не заменяет CraftSim и не основание вводить отложенную проверку доступности профессий персонажу |
| CrateTrackerZK v2.0.25 | Описание отслеживания времени появления военных ящиков; CRATETRACKERZK_DB и связанные переменные | Только возможный контекст источников предложения; не подтверждает состав/PMF контейнера |
| CanIMogIt, BetterWardrobe | Описания/данные коллекции внешнего вида | Возможный контекст классификации личного имущества; не доказывает индивидуальную возможность продажи предмета |

Другие аддоны инвентаризированы без исследования их игровых функций. Расширять обязательные интеграции из-за одного совпадения названия не предлагается. Начать адресную проверку дополнительного кандидата имеет смысл при конкретном незакрытом пробеле основных источников.

Для воспроизведения: перечислить непосредственные каталоги AddOns, в каждом прочитать непосредственные `*.toc`, извлечь строки `## Title:`, `## Version:`, `## Interface:`. Не читать настройки аккаунтов или пользовательские профили для этой инвентаризации.

## A02 — узкая проверка Auctionator posting history

**Статус: S+R, без runtime. Вывод: установленный Auctionator 341 не закрывает пробел точных потерянных залогов L05.** Ниже рассмотрена только история выставлений. Источники относительно `W:\Battle.net\Games\World of Warcraft\_retail_\Interface\AddOns\Auctionator`.

| Вопрос | Проверенное свидетельство | Ограничение вывода |
|---|---|---|
| Что сохраняется | `Source/PostingHistory/Mixin.lua:10–24`: `AUCTIONATOR_POSTING_HISTORY[dbKey]` содержит список `{price, quantity, bidPrice, time}`; `time=time()` | Нет deposit, duration, auctionID, owner/character, realm, состояния продажи/отмены/истечения |
| Откуда price | `Mixin.lua:57–63`: `price=eventData.buyoutAmount`, `bidPrice=eventData.bidAmount`; ModernAH `Tabs/Selling/Mixins/SaleItem.lua:705–740` передаёт введённые quantity/buyout/bid в posting API и event payload | Это объявленная цена выставления, не net proceeds, не совершённая продажа. Тип цены для commodity/item должен сохраняться, а не смешиваться со значением TSM net/unit |
| Когда создаётся запись | ModernAH `SaleItem.lua:713–750`: после вызова PostItem/PostCommodity непосредственно публикует внутренний AuctionCreated с postedInfo | В этом пути событие не является отдельным подтверждением сервера об исполненной продаже; наличие строки нельзя повышать до доказательства успешного posting без runtime-проверки ошибок/подтверждений |
| Сохраняется ли залог | ModernAH `SaleItem.lua:184–202` рассчитывает UI deposit через CalculateCommodityDeposit/CalculateItemDeposit с duration и quantity; `735–740` не включает deposit/duration в postedInfo; Mixin `22–24` их не сохраняет | Значение, показанное UI при выставлении, теряется для этой истории. Текущий default duration не восстанавливает историческую длительность |
| Полнота и объединение | `Mixin.lua:33–54`: одинаковая цена одного дня по локальному `date('*t')` объединяется суммированием quantity; затем pruning по POSTING_HISTORY_LENGTH. `Source/Config/Main.lua:112` default length=10 | Это не неизменяемый журнал каждой попытки. Действующая пользовательская настройка длины отдельно не извлекалась. Из объединения нельзя восстановить число отдельных депозитов |
| Повторное представление предмета | `Source/Utilities/DBKeyFromLink.lua:24–59`: для части Retail gear callback возвращает и `g:itemID:itemLevel`, и базовый itemID; `Mixin.lua:59–62` пишет по каждому ключу | При суммировании всех ключей одна попытка может учитываться дважды. Полной variant identity нет; canonical reconciliation необходим |
| Версия/сброс | `Source/Variables/Main.lua:4,208–218`: schema version=1; несовпадение версии вызывает создание новой таблицы | Исчезновение старой истории не доказывает отсутствия прошлых выставлений |

Реальные файлы: найден **1 account-level Auctionator.lua**, 4 713 937 байт, LastWriteTimeUtc **2026-10-26 00:03:22** (03:03:22 Москвы), и **9** маленьких character-level файлов по 38 байт, объявляющих только `AUCTIONATOR_CHARACTER_CONFIG`. Это число файлов настроек, не подтверждённое число действующих персонажей. `.bak` обнаружены, но не засчитывались как отдельные наблюдения.

В account-level файле блок `AUCTIONATOR_POSTING_HISTORY` имеет 28 байт и содержит только `__dbversion=1`: **0 item tables, 0 записей price/quantity/time/bidPrice**. Следовательно, непустая история выставлений на этих реальных данных не проверена. Имена персонажей/аккаунтов, суммы и частные значения не выводились и не копировались.

Дополнительная техническая находка: попытка декодировать **весь** account-level файл как строгий UTF-8 завершилась UnicodeDecodeError; отдельно извлечённый ASCII-блок posting history читается корректно. Причина байтов вне UTF-8 в остальных переменных не исследовалась (возможные двоичные строки/иной формат не объявляются доказанными). Нельзя автоматически считать файл повреждённым либо применять `errors='replace'` к импорту с потерей данных. Данная диагностическая проверка работала с bytes, выделила только именованный блок и посчитала поля; Lua не исполнялся. Будущий отдельный адаптер Auctionator потребует byte-safe контракта, если его действительно включат в scope.

Точный потерянный deposit требует связки конкретного выставления, его duration/quantity/варианта, размера залога и исхода. Эти поля не появляются из имеющейся posting history. Поэтому L05 остаётся открытым; Auctionator можно рассматривать как дополнительный источник агрегированных объявленных цен/намерений, после проверки непустых данных и условий использования, но не как замену Ledger или доказательство прибыли.

Контрольные SHA-256:

| Объект | SHA-256 |
|---|---|
| Source/PostingHistory/Mixin.lua | 84a038f0970f6c06a45ab81ff8bd7b08f6831261d2473364ce4c5552c6513ed4 |
| Source_ModernAH/Tabs/Selling/Mixins/SaleItem.lua | 7c098b9a4b6622597a588032c5a5d6337e174234f52fd17e00590c867fc8eb79 |
| Source/Variables/Main.lua | 7435ec19031db4cbf63e1374d605ee4abcbe9c0a59b04658d1331167fe61a717 |
| Source/Utilities/DBKeyFromLink.lua | 121ab8deb657d7f95c2be48a15bd98962fc74434d06431735988c40560b00972 |
| Source/Config/Main.lua | 8f0caf409340f9bd4cf0862cfa3c5beb914a611fc1884284e5e22ef6fd5e20d5 |
| Account-level Auctionator.lua (содержимое не опубликовано) | 3c6836e1572f56cc0c7f6c62dd63d57b487b0dd262eacee179f798f788b12c1e |

Воспроизводимая узкая проверка реального блока без печати содержимого:

```python
from pathlib import Path
import re
root = Path(r'W:\Battle.net\Games\World of Warcraft\_retail_\WTF')
for path in root.rglob('Auctionator.lua'):
    raw = path.read_bytes()
    block = re.search(rb'^AUCTIONATOR_POSTING_HISTORY\s*=\s*(\{.*?)(?=^[A-Z_]+\s*=|\Z)', raw, re.M | re.S)
    if block:
        value = block.group(1)
        print('block_bytes', len(value))
        for field in (b'price', b'quantity', b'time', b'bidPrice'):
            print(field.decode('ascii'), len(re.findall(rb'\["' + field + rb'"\]\s*=', value)))
```

Этот ограниченный probe не является общим парсером произвольного Lua; для исследованного пустого блока результат дополнительно сверён со схемой исходника. Изменение формата требует отдельной проверки.

## Таблица всех обнаруженных TOC

<!-- INVENTORY -->

Catalog directories: **52**. Top-level TOC files: **145**. Directories without TOC: **0**.

SHA-256 of sorted relative TOC paths and bytes (NUL separators): `623a4e9ceadfed0bda6ce0febab28f81344e55ab21357ba3db4c1231f18c123d`.

| Folder | TOC file | Title | Version | Interface |
|---|---|---|---|---|
| Auctionator | Auctionator.toc | Auctionator | 341 | 120100, 120105, 16001, 50504, 38000, 20506, 11508, 11509 |
| BagSync | BagSync.toc | BagSync | 20.38 | 120105, 50504, 40402, 38002, 30405, 20506, 16001, 11509 |
| BetterBlizzFrames | BetterBlizzFrames.toc | Better\|cff00c0ffBlizz\|rFrames | v2.1.8 | 120105, 120100 |
| BetterBlizzFrames | BetterBlizzFrames_Camelot.toc | Better\|cff00c0ffBlizz\|rFrames | v2.1.8 | 16001 |
| BetterBlizzFrames | BetterBlizzFrames_Cata.toc | Better\|cff00c0ffBlizz\|rFrames | v2.1.8 | 40402 |
| BetterBlizzFrames | BetterBlizzFrames_Mists.toc | Better\|cff00c0ffBlizz\|rFrames | v2.1.8 | 50504 |
| BetterBlizzFrames | BetterBlizzFrames_TBC.toc | Better\|cff00c0ffBlizz\|rFrames | v2.1.8 | 20506 |
| BetterBlizzFrames | BetterBlizzFrames_Vanilla.toc | Better\|cff00c0ffBlizz\|rFrames | v2.1.8 | 11509, 11508 |
| BetterBlizzFrames | BetterBlizzFrames_Wrath.toc | Better\|cff00c0ffBlizz\|rFrames | v2.1.8 | 30405, 38000, 38002 |
| BetterBlizzPlates | BetterBlizzPlates.toc | Better\|cff00c0ffBlizz\|rPlates | v2.2.2 | 120105, 120100 |
| BetterBlizzPlates | BetterBlizzPlates_Camelot.toc | Better\|cff00c0ffBlizz\|rPlates | v2.2.2 | 16001 |
| BetterBlizzPlates | BetterBlizzPlates_Cata.toc | Better\|cff00c0ffBlizz\|rPlates | v2.2.2 | 40402 |
| BetterBlizzPlates | BetterBlizzPlates_Mists.toc | Better\|cff00c0ffBlizz\|rPlates | v2.2.2 | 50504 |
| BetterBlizzPlates | BetterBlizzPlates_TBC.toc | Better\|cff00c0ffBlizz\|rPlates | v2.2.2 | 20506, 20505 |
| BetterBlizzPlates | BetterBlizzPlates_Vanilla.toc | Better\|cff00c0ffBlizz\|rPlates | v2.2.2 | 11509, 11508 |
| BetterBlizzPlates | BetterBlizzPlates_Wrath.toc | Better\|cff00c0ffBlizz\|rPlates | v2.2.2 | 30405, 38000, 38002 |
| BetterWardrobe | BetterWardrobe.toc | BetterWardrobe (6.12.3) | 6.12.3 | 120100 |
| BetterWardrobe_SourceData | BetterWardrobe_SourceData.toc | BetterWardrobe_SourceData (6.12.3) | 6.12.3 | 120100 |
| CanIMogIt | CanIMogIt.toc | Can I Mog It? | 12.1.5-1 | 120105 |
| CooldownManagerCentered | CooldownManagerCentered.toc | \|cff008945Cool\|r\|cff1e9a4e\|r\|cff3faa4fdown Ma\|r\|cff5fb64anag\|r\|cff7ac243er Ce\|r\|cff8ccd00ntered\|r by Wildu | 5.9.3 | 120107, 120105, 120100, 16001 |
| CraftSim | CraftSim.toc | CraftSim | 27.0.7 | 120005, 120007, 120100 |
| CrateTrackerZK | CrateTrackerZK.toc | CrateTrackerZK | v2.0.25 | 120105, 120100 |
| Details | Details.toc | Details! Damage Meter | #Details.20261015.15339.172 | 120105 |
| Details | Details_Camelot.toc | Details! Damage Meter | #Details.20261015.15339.172 | 16001 |
| Details | Details_Cata.toc | Details! Damage Meter | #Details.20261015.15339.172 | 40402 |
| Details | Details_Classic.toc | Details! Damage Meter | #Details.20261015.15339.172 | 11509 |
| Details | Details_Mists.toc | Details! Damage Meter | #Details.20261015.15339.172 | 50504 |
| Details | Details_TBC.toc | Details! Damage Meter | #Details.20261015.15339.172 | 20506 |
| Details | Details_Wrath.toc | Details! Damage Meter | #Details.20261015.15339.172 | 30405, 38002 |
| Details_Compare2 | Details_Compare2.toc | Details!: Compare 2.0 | — | 120105 |
| Details_Compare2 | Details_Compare2_Camelot.toc | Details!: Compare 2.0 | — | 16001 |
| Details_Compare2 | Details_Compare2_Cata.toc | Details!: Compare 2.0 | — | 40402 |
| Details_Compare2 | Details_Compare2_Mists.toc | Details!: Compare 2.0 | — | 50504 |
| Details_Compare2 | Details_Compare2_Wrath.toc | Details!: Compare 2.0 | — | 30405, 38002 |
| Details_DataStorage | Details_DataStorage-Wrath.toc | Details!: Storage | — | 30405, 38002 |
| Details_DataStorage | Details_DataStorage.toc | Details!: Storage | — | 120105 |
| Details_DataStorage | Details_DataStorage_Camelot.toc | Details!: Storage | — | 16001 |
| Details_DataStorage | Details_DataStorage_Cata.toc | Details!: Storage | — | 40402 |
| Details_DataStorage | Details_DataStorage_Mists.toc | Details!: Storage | — | 50504 |
| Details_DataStorage | Details_DataStorage_Wrath.toc | Details!: Storage | — | 30405, 38002 |
| Details_EncounterDetails | Details_EncounterDetails-Wrath.toc | Details!: Encounter Breakdown (plugin) | — | 30405, 38002 |
| Details_EncounterDetails | Details_EncounterDetails.toc | Details!: Encounter Breakdown (plugin) | — | 120105 |
| Details_EncounterDetails | Details_EncounterDetails_Camelot.toc | Details!: Encounter Breakdown (plugin) | — | 16001 |
| Details_EncounterDetails | Details_EncounterDetails_Cata.toc | Details!: Encounter Breakdown (plugin) | — | 40402 |
| Details_EncounterDetails | Details_EncounterDetails_Mists.toc | Details!: Encounter Breakdown (plugin) | — | 50504 |
| Details_EncounterDetails | Details_EncounterDetails_Wrath.toc | Details!: Encounter Breakdown (plugin) | — | 30405, 38002 |
| Details_RaidCheck | Details_RaidCheck-Wrath.toc | Details!: Raid Check (plugin) | — | 30405, 38002 |
| Details_RaidCheck | Details_RaidCheck.toc | Details!: Raid Check (plugin) | — | 120105 |
| Details_RaidCheck | Details_RaidCheck_Camelot.toc | Details!: Raid Check (plugin) | — | 16001 |
| Details_RaidCheck | Details_RaidCheck_Cata.toc | Details!: Raid Check (plugin) | — | 40402 |
| Details_RaidCheck | Details_RaidCheck_Mists.toc | Details!: Raid Check (plugin) | — | 50504 |
| Details_RaidCheck | Details_RaidCheck_Wrath.toc | Details!: Raid Check (plugin) | — | 30405, 38002 |
| Details_Streamer | Details_Streamer-Wrath.toc | Details!: Streamer (plugin) | — | 30405, 38002 |
| Details_Streamer | Details_Streamer.toc | Details!: Streamer (plugin) | — | 120105 |
| Details_Streamer | Details_Streamer_Camelot.toc | Details!: Streamer (plugin) | — | 16001 |
| Details_Streamer | Details_Streamer_Cata.toc | Details!: Streamer (plugin) | — | 40402 |
| Details_Streamer | Details_Streamer_Mists.toc | Details!: Streamer (plugin) | — | 50504 |
| Details_Streamer | Details_Streamer_Wrath.toc | Details!: Streamer (plugin) | — | 30405, 38002 |
| Details_TinyThreat | Details_TinyThreat-Classic.toc | Details!: Tiny Threat (plugin) | — | 11509 |
| Details_TinyThreat | Details_TinyThreat-Wrath.toc | Details!: Tiny Threat (plugin) | — | 30405, 38002 |
| Details_TinyThreat | Details_TinyThreat.toc | Details!: Tiny Threat (plugin) | — | 120105 |
| Details_TinyThreat | Details_TinyThreat_Camelot.toc | Details!: Tiny Threat (plugin) | — | 16001 |
| Details_TinyThreat | Details_TinyThreat_Cata.toc | Details!: Tiny Threat (plugin) | — | 40402 |
| Details_TinyThreat | Details_TinyThreat_Classic.toc | Details!: Tiny Threat (plugin) | — | 11509 |
| Details_TinyThreat | Details_TinyThreat_Mists.toc | Details!: Tiny Threat (plugin) | — | 50504 |
| Details_TinyThreat | Details_TinyThreat_TBC.toc | Details!: Tiny Threat (plugin) | — | 20506 |
| Details_TinyThreat | Details_TinyThreat_Wrath.toc | Details!: Tiny Threat (plugin) | — | 30405, 38002 |
| Details_Vanguard | Details_Vanguard-Wrath.toc | Details!: Vanguard (plugin) | — | 30405, 38002 |
| Details_Vanguard | Details_Vanguard.toc | Details!: Vanguard (plugin) | — | 120105 |
| Details_Vanguard | Details_Vanguard_Camelot.toc | Details!: Vanguard (plugin) | — | 16001 |
| Details_Vanguard | Details_Vanguard_Cata.toc | Details!: Vanguard (plugin) | — | 40402 |
| Details_Vanguard | Details_Vanguard_Mists.toc | Details!: Vanguard (plugin) | — | 50504 |
| Details_Vanguard | Details_Vanguard_Wrath.toc | Details!: Vanguard (plugin) | — | 30405, 38002 |
| DynamicCam | DynamicCam.toc | DynamicCam | 2.21.5 | 120105, 16001, 50504, 20506, 11509 |
| FarmHud | FarmHud.toc | FarmHud | 10.3.0-release | 120100, 50504, 38002, 20506, 11509 |
| FrameSort | FrameSort.toc | FrameSort | 7.20.1 | 120105, 120100, 16001, 120007, 50504, 40402, 38002, 38001, 30405, 30300, 20506, 11509 |
| GatherMate2 | GatherMate2.toc | GatherMate2 | 1.52.3 | 120007,120100 |
| GatherMate2_Data | GatherMate2_Data.toc | GatherMate2Data | v41.0 | 120001 |
| GatherMate2Storage_Dragonflight | GatherMate2Storage_Dragonflight.toc | GatherMate2: Storage for Dragonflight | 1.52.3 | 120007,120100 |
| GatherMate2Storage_Midnight | GatherMate2Storage_Midnight.toc | GatherMate2: Storage for Midnight | 1.52.3 | 120007,120100 |
| GatherMate2Storage_TheWarWithin | GatherMate2Storage_TheWarWithin.toc | GatherMate2: Storage for The War Within | 1.52.3 | 120007,120100 |
| GX | GX.toc | GX - Gold Export | 1.1.0 | 120100 |
| LootAppraiserReloaded | LootAppraiserReloaded.toc | LootAppraiser Reloaded | 3.5.1 | 120105, 50504, 40402, 38002, 30405, 20506, 11509 |
| MiniAuras | MiniAuras.toc | MiniAuras | 5.44.0 | 120105, 120100, 16001 |
| MiniCC | MiniCC.toc | MiniCC \|cff808080(settings bridge for MiniAuras)\|r | 5.44.0 | 120105, 120100, 16001 |
| MyusKnowledgePointsTracker | MyusKnowledgePointsTracker.toc | Myu's Knowledge Points Tracker | 0.8.12 | 120007, 120100 |
| Narcissus | Narcissus.toc | Narcissus | 1.8.7 | 120105, 16001 |
| Narcissus_Achievements | Narcissus_Achievements.toc | Narcissus Achievements | 12.1.5 | 120105, 16001 |
| Narcissus_BagFilter | Narcissus_BagFilter.toc | Narcissus Bag Filter | 1.0.2 | 120105, 16001 |
| Narcissus_Barbershop | Narcissus_Barbershop.toc | Narcissus Barbershop | 1.7 | 120105, 16001 |
| Narcissus_Database_Item | Narcissus_Database_Item.toc | Narcissus Database Item | 9.2.0 | 120105, 16001 |
| Narcissus_Database_NPC | Narcissus_Database_NPC.toc | Narcissus Database NPC | 11.0.2 | 120105, 16001 |
| Narcissus_GamePad | Narcissus_GamePad.toc | Narcissus GamePad | 1.0 | 120105, 16001 |
| RaidMount | RaidMount.toc | \|cFFFF0000Raid\|r and \|cFFFF0000Dungeon\|r \|cFF33CCFFMount\|r Tracker | 27.12.25.50 | 120000, 120001, 110207 |
| Routes | Routes.toc | Routes | v1.8.7 | 120005, 120007, 11508, 50503, 20505 |
| RoutesImportExport | RoutesImportExport.toc | Routes Import/Export | v1.0.14 | 120000 |
| RoutesImportExport | RoutesImportExport_Cata.toc | Routes Import/Export | v1.0.14 | 40400 |
| RoutesImportExport | RoutesImportExport_Mists.toc | Routes Import/Export | v1.0.14 | 50502 |
| RoutesImportExport | RoutesImportExport_TBC.toc | Routes Import/Export | v1.0.14 | 20505 |
| RoutesImportExport | RoutesImportExport_Vanilla.toc | Routes Import/Export | v1.0.14 | 11508 |
| RoutesImportExport | RoutesImportExport_Wrath.toc | Routes Import/Export | v1.0.14 | 30403 |
| sArena_Reloaded | sArena_Reloaded.toc | sArena \|cffff8000Reloaded\|r | v2.6.8 | 120105, 120100 |
| sArena_Reloaded | sArena_Reloaded_Mists.toc | sArena \|cffff8000Reloaded\|r \|T135884:16:16\|t | v2.6.8 | 50504 |
| sArena_Reloaded | sArena_Reloaded_TBC.toc | sArena \|cffff8000Reloaded\|r | v2.6.8 | 20506, 20505 |
| sArena_Reloaded | sArena_Reloaded_Vanilla.toc | sArena \|cffff8000Reloaded\|r | v2.6.8 | 11508 |
| sArena_Reloaded | sArena_Reloaded_Wrath.toc | sArena \|cffff8000Reloaded\|r \|T135884:16:16\|t | v2.6.8 | 30405, 38001, 38002 |
| SkillCapped | SkillCapped.toc | \|cFFFFFFFFSkill\|r\|cff7ba4fcCapped\|r | v2.3.3i | 120100 |
| SkillCapped | SkillCapped_Mists.toc | \|cFFFFFFFFSkill\|r\|cff7ba4fcCapped\|r | v2.3.3i | 50504 |
| SkillCapped | SkillCapped_TBC.toc | \|cFFFFFFFFSkill\|r\|cff7ba4fcCapped\|r | v2.3.3i | 20506, 20505 |
| SkillCapped | SkillCapped_Vanilla.toc | \|cFFFFFFFFSkill\|r\|cff7ba4fcCapped\|r | v2.3.3i | 11509, 11508 |
| SkillCapped | SkillCapped_Wrath.toc | \|cFFFFFFFFSkill\|r\|cff7ba4fcCapped\|r | v2.3.3i | 30405, 38000, 38002 |
| TradeSkillMaster | TradeSkillMaster.toc | \|cff00fe00TradeSkillMaster\|r | v4.14.78 | 120105, 50504, 20506, 11509 |
| TradeSkillMaster_AppHelper | TradeSkillMaster_AppHelper.toc | \|cff00ff00TradeSkillMaster_AppHelper\|r | v4.14.14 | 120105, 50504, 20506, 11509 |
| WeakAuras | WeakAuras.toc | WeakAuras | 5.21.1 | 110207 |
| WeakAuras | WeakAuras_Cata.toc | WeakAuras | 5.21.1 | 40402 |
| WeakAuras | WeakAuras_Mists.toc | WeakAuras | 5.21.1 | 50503 |
| WeakAuras | WeakAuras_TBC.toc | WeakAuras | 5.21.1 | 20505 |
| WeakAuras | WeakAuras_Vanilla.toc | WeakAuras | 5.21.1 | 11508 |
| WeakAuras | WeakAuras_Wrath.toc | WeakAuras | 5.21.1 | 30405, 38000 |
| WeakAurasArchive | WeakAurasArchive.toc | WeakAuras Archive | 5.21.1 | 110207 |
| WeakAurasArchive | WeakAurasArchive_Cata.toc | WeakAuras Archive | 5.21.1 | 40402 |
| WeakAurasArchive | WeakAurasArchive_Mists.toc | WeakAuras Archive | 5.21.1 | 50503 |
| WeakAurasArchive | WeakAurasArchive_TBC.toc | WeakAuras Archive | 5.21.1 | 20505 |
| WeakAurasArchive | WeakAurasArchive_Vanilla.toc | WeakAuras Archive | 5.21.1 | 11508 |
| WeakAurasArchive | WeakAurasArchive_Wrath.toc | WeakAuras Archive | 5.21.1 | 30405, 38000 |
| WeakAurasModelPaths | WeakAurasModelPaths.toc | WeakAuras Model Paths | 5.21.1 | 110207 |
| WeakAurasModelPaths | WeakAurasModelPaths_Cata.toc | WeakAuras Model Paths | 5.21.1 | 40402 |
| WeakAurasModelPaths | WeakAurasModelPaths_Mists.toc | WeakAuras Model Paths | 5.21.1 | 50503 |
| WeakAurasModelPaths | WeakAurasModelPaths_TBC.toc | WeakAuras Model Paths | 5.21.1 | 20505 |
| WeakAurasModelPaths | WeakAurasModelPaths_Vanilla.toc | WeakAuras Model Paths | 5.21.1 | 11508 |
| WeakAurasModelPaths | WeakAurasModelPaths_Wrath.toc | WeakAuras Model Paths | 5.21.1 | 30405, 38000 |
| WeakAurasOptions | WeakAurasOptions.toc | WeakAuras Options | 5.21.1 | 110207 |
| WeakAurasOptions | WeakAurasOptions_Cata.toc | WeakAuras Options | 5.21.1 | 40402 |
| WeakAurasOptions | WeakAurasOptions_Mists.toc | WeakAuras Options | 5.21.1 | 50503 |
| WeakAurasOptions | WeakAurasOptions_TBC.toc | WeakAuras Options | 5.21.1 | 20505 |
| WeakAurasOptions | WeakAurasOptions_Vanilla.toc | WeakAuras Options | 5.21.1 | 11508 |
| WeakAurasOptions | WeakAurasOptions_Wrath.toc | WeakAuras Options | 5.21.1 | 30405, 38000 |
| WeakAurasTemplates | WeakAurasTemplates.toc | WeakAuras Templates | 5.21.1 | 110207 |
| WeakAurasTemplates | WeakAurasTemplates_Cata.toc | WeakAuras Templates | 5.21.1 | 40402 |
| WeakAurasTemplates | WeakAurasTemplates_Mists.toc | WeakAuras Templates | 5.21.1 | 50503 |
| WeakAurasTemplates | WeakAurasTemplates_TBC.toc | WeakAuras Templates | 5.21.1 | 20505 |
| WeakAurasTemplates | WeakAurasTemplates_Vanilla.toc | WeakAuras Templates | 5.21.1 | 11508 |
| WeakAurasTemplates | WeakAurasTemplates_Wrath.toc | WeakAuras Templates | 5.21.1 | 30405, 38000 |
| WhispList | WhispList.toc | WhispList | 4.52 | 120007, 120100, 50504 |
| Wildu_SharedMedia | Wildu_SharedMedia.toc | !\|cff008945W\|r\|cff0c8f3fi\|r\|cff17943al\|r\|cff239a34du_\|r\|cff2fa02eS\|r\|cff3aa528h\|r\|cff46ab23a\|r\|cff52b11dr\|r\|cff5db617ed\|r\|cff69bc11M\|r\|cff75c20ce\|r\|cff80c706d\|r\|cff8ccd00ia\|r | 1.3.8 | 120107, 120105, 120100, 16001 |
