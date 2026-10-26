# Этап 0: CraftSim и база преобразований Retail

Дата исследования: 26.10.2026 (Europe/Moscow). Исходные C01–C22 сохранены; дополнение R2 по S0-01 — [конкретные маршруты перечисления и пробы](transformation-discovery-r2.md). Только чтение исходников, выборочная проверка структуры существующих SavedVariables и публичных источников. Игра не запускалась, Lua внутри игры не исполнялся, аддон/импортёр/приложение не создавались. Этот документ — исследовательское приложение, а не независимая приёмка. C09 относится к первоначальному hash; более новый снимок зафиксирован отдельно в [local-addons §7](local-addons.md).

## Обозначения доказательств

- **D** — документировано автором источника.
- **S** — проверено в фактически установленном исходнике.
- **R** — проверено на существующем локальном файле данных; это не проверка игрового события.
- **H** — архитектурная гипотеза или вывод, требующий отдельного испытания.
- **B** — конкретная проверка не состоялась/заблокирована.

Для краткости `CS` ниже означает `W:\Battle.net\Games\World of Warcraft\_retail_\Interface\AddOns\CraftSim`, `TSM` — соседнюю папку `TradeSkillMaster`. Полные пути приведены в командах воспроизведения. Номера строк относятся к найденным файлам, а не к меняющейся ветке GitHub.

## Реестр фактов

| ID | Факт и точный источник | Статус и предел вывода |
|---|---|---|
| C01 | `CS\CraftSim.toc:1,20,26–28`: версия **27.0.7**, Interface `120005,120007,120100`, `SavedVariables: CraftSimDB`, зависимость `Blizzard_Professions`, опциональная TSM. | S. Установка подтверждена; загрузка и совместимость с фактическим клиентом не проверены. |
| C02 | `CS\Util\API.lua:6–93`, `Util\API_README.md`: `CraftSimAPI:GetRecipeData(options)`, `GetOpenRecipeData()`, `GetLastCraftingCost(itemIDOrLink)`, `RegisterEvents(table,events)`. [Публичный исходник автора](https://github.com/derfloh205/CraftSim/blob/main/Util/API.lua) также получен через GitHub connector; reported modified 2026-08-17. | D+S. Реально существуют; внешнего HTTP/Python API здесь нет. |
| C03 | README ссылается на `Data/Classes/RecipeData.lua`, которого нет; фактически `CS\Classes\RecipeData.lua`. README помечает `OptimizeProfit()` deprecated; реализация остаётся (`:2174`). | D+S. Документацию нельзя применять без сверки установленной версии. |
| C04 | `Classes\RecipeData.lua:107–153,266–342,2704–2725`: конструктор принимает recipeID, crafterData, recraft/order/forceCache; вызывает `C_TradeSkillUI.GetRecipeSchematic` и operation info, создаёт reagent/profession/result/price objects; при отсутствии схемы возвращается без полноценного результата. | S. Расчёт зависит от игрового runtime/кэшей; Python не может просто импортировать эти Lua-файлы как автономный движок. |
| C05 | `RecipeData.lua:1124–1129`: `Update()` пересчитывает professionStats → resultData → priceData. `ResultData.lua:120–173` рассчитывает expectedQuality/concentration quality, expectedYieldPerCraft. `ProfitCalculation.lua:105–268` возвращает среднюю прибыль и таблицу вероятностей proc-состояний. | S. Это модель CraftSim; её вероятности/константы не становятся доказанными игровыми вероятностями автоматически. |
| C06 | `RecipeData.lua:2332–2374` содержит `GetJSON`; `ResultData.lua:252–269` в JSON **не включает expectedYieldPerCraft**, хотя `Serialize()` включает его (`:219–230`). RecipeData JSON не содержит полноценный паспорт снимка/версии/времени, min/max yield и достаточный журнал цен. | S. Готовый JSON полезен, но не является полным контрактом внешней воспроизводимости. |
| C07 | `Util\Events.lua:24–31` описывает `CRAFTSIM_RECIPE_DATA_MODIFIED` и `CRAFTSIM_RECIPE_DATA_UPDATED`; `API.lua:76–78` регистрирует callbacks. | D+S. Подходящая точка для будущего собственного экспортёра; фактические порядок/частота/аргументы должны проверяться в игре. |
| C08 | `DB\lastCraftingCostDB.lua:9–11,18–28,50–88`: хранится `data[itemID или itemID:qualityID][crafterUID]={cost,timestamp}`, только ожидаемое качество, cost per item in copper; записывается только learned recipe. README описывает заполнение опцией Recipe Scan/Craft Lists. | D+S. Это кэш себестоимости, не полные входы/выходы/статы и не база всех рецептов. |
| C09 | Найден реальный `WTF\Account\<account>\SavedVariables\CraftSim.lua`: 269299 байт, mtime `2026-10-25T23:29:25Z`. `lastCraftingCostDB` имеет `version=1,data={}`. Лексических ключей `cost`, `timestamp`, `resultData`, `expectedYieldPerCraft` — 0; `crafterDB` — 1, `concentrationData` — 18, `cooldownData` — 4. | R. Структура исследована без исполнения Lua; имена аккаунтов/персонажей не публикуются. mtime — запись файла, не время подтверждения каждого значения. Пустой кэш не доказывает неисправность CraftSim. |
| C10 | В том же SV присутствуют repositories `crafterDB`, `craftListsDB`, `craftQueueDB`, `customerHistoryDB`, `itemCountDB`, `itemOptimizedCostsDB`, `itemRecipeDB`, `lastCraftingCostDB`, `multicraftPreloadDB`, `optimizationOptionsDB`, `optionsDB`, `patronMoxieValuesDB`, `priceOverrideDB`, `recipeSubCrafterDB`. `DB\crafterDB.lua:16–28` описывает gear, specialization, cooldown, concentration; нет гарантии полноты всех рецептов. | R+S. Из наличия repositories нельзя заключать, что любое необходимое поле заполнено или актуально. |
| C11 | `Pricing\ProfitCalculation.lua:118,140–148,190–197,251–254`: в негрей/не-order ветви `adaptResultValue` вычитает expectedDeposit, затем в ветвях с Resourcefulness этот депозит вычитается ещё раз. `DataSource\PriceAPIs.lua:270–322` возвращает 0 при выключенной опции/отсутствии данных/TSM. Default `Util\Const.lua:529` — false; в исследованном SV ключ TSM_DEPOSIT_ENABLED отсутствует. | S для потока исходника; **H для реального влияния/дефекта у пользователя**. Не воспроизведено в игре, активность опции не установлена. Не утверждать, что текущая прибыль пользователя неверна. |
| C12 | `RecipeData.lua:291` берёт baseItemAmount как `(quantityMin+quantityMax)/2`; `ProfitCalculation.lua:32–43` использует среднее multicraft-выхода; `:175–178` перемножает вероятности multicraft/resourcefulness. | S. Среднее min/max не доказывает равномерность; таблица proc-состояний не полное распределение целочисленных выходов и их корреляций. Нужна явная квалификация model estimate. |
| C13 | `CS\LICENSE:1–21` — MIT; обязательны уведомления при использовании существенных частей. | S. Лицензия CraftSim не распространяется автоматически на игровые данные/другие источники. |
| C14 | `TSM\LibTSMData\Destroy\{Disenchant,Mill,Prospect}.lua`, `Convert\{Transform,VendorTrade}.lua` имеют отдельную **DATA.Retail**, отличную от Vanilla/BCC/Panda Classic; `LibTSMTypes\Source\Item\Conversion.lua:40–63` читает эти таблицы. | S. Есть реальный большой кандидатный источник непрофессиональных преобразований; нельзя импортировать Classic-секции в Retail. |
| C15 | `TSM\LICENSE.md:1–13`: all rights reserved, разрешены личные модификации, распространение зарезервировано правообладателю; использование частей предлагается согласовывать с автором. | S. До массового копирования таблиц/распространения seed нужны подтверждённые условия; здесь таблицы в репозиторий не копировались. Анализ своего установленного исходника не равен полученной лицензии на издание базы. |
| C16 | [WoWDBDefs README](https://github.com/wowdev/WoWDBDefs/blob/master/README.md) и [SpellReagents](https://github.com/wowdev/WoWDBDefs/blob/master/definitions/SpellReagents.dbd), [ItemExtendedCost](https://github.com/wowdev/WoWDBDefs/blob/master/definitions/ItemExtendedCost.dbd) получены через GitHub connector. Описаны build/layout-specific поля реагентов, количеств, валют, reputation/achievement requirements; неизвестные поля помечены `?`. | D+S (удалённый исходник). Это **схемы**, не скачанные актуальные данные и не доказательство исполнимости рецепта/наличия NPC. |
| C17 | [WoWDBDefs LICENSE.md](https://github.com/wowdev/WoWDBDefs/blob/master/LICENSE.md): definitions/data — CC BY-SA 4.0, code/exedumper — BSD-3-Clause. | D. Лицензия описаний форматов не лицензирует автоматически сами извлечённые данные Blizzard. |
| C18 | [Blizzard-generated TradeSkillUI API в зеркале Gethe](https://github.com/Gethe/wow-ui-source/blob/live/Interface/AddOns/Blizzard_APIDocumentationGenerated/TradeSkillUIDocumentation.lua): `GetCraftingOperationInfo` (173), `GetRecipeInfo` (624), `GetRecipeSchematic` (738); [MerchantFrame API](https://github.com/Gethe/wow-ui-source/blob/live/Interface/AddOns/Blizzard_APIDocumentationGenerated/MerchantFrameDocumentation.lua) содержит `C_MerchantFrame.GetItemInfo`. Получены через GitHub connector, reported modified 2026-10-13. | D (первичный генерированный код в стороннем зеркале), не runtime. Параметры и nilability сверять с фактическим build. |
| C19 | Прямые открытия `https://wago.tools/` и `https://wow.tools/` вернули 403 через web tool; `https://wago.tools/db2/SpellReagents/csv?build=12.1.5.70575&locale=enUS` не доступен через этот инструмент. | B. Не доказано отсутствие сервиса или запрет для пользователя; реальная выгрузка DB2 не проверена. Последний CSV URL — проверяемый кандидатный маршрут, не установленный публичный API-контракт. |
| C20 | `https://www.wowhead.com/terms-of-service` не доступен через инструмент. [Собственная политика Warcraft Wiki](https://warcraft.wiki.gg/wiki/Fair_use) описывает attribution/share-alike для текстового переиспользования и отдельные права на материал Blizzard. | B для Wowhead; D для политики wiki. Нет подтверждённого разрешения/лимитов массового сбора Wowhead/Wago; не закладывать scraping как работающий источник. |
| C21 | `Pricing\ProfitCalculation.lua:274–283`: после проверки supportsCraftingStats ветвь без resourcefulness и fallback возвращают `0,{}`. | S. Нулевое значение с пустым распределением может означать неподдержанный/неготовый расчёт, а не нулевую экономическую прибыль. Классификация каждого сценария — runtime test, не выполнен. |
| C22 | Пять адресных примеров из локальных `TSM\LibTSMData\...` Retail-секций приведены ниже: точные ID/коэффициенты и строки, без переноса массивов данных. Направление target/source подтверждено `LibTSMTypes\Source\Item\Conversion.lua:40–63`. | S. Факты о содержимом установленного источника; текущие игровые операции, batch sizes и вероятности экспериментально не подтверждены. |

## Что CraftSim уже позволяет и что потребуется

Подтверждён полноценный расчётный объект с качеством, реагентами, профессиями, концентрацией, gear/buffs/spec и моделями proc. Поэтому правильная интеграция — **использовать установленный движок**, сохраняя его механические результаты, исходные параметры и ограничения. Переписывать CraftSim в Python или заменять его только справочником рецептов не требуется.

Предлагается маленький отдельный аддон-мост на этапе 3; в этапе 1 можно лишь безопасно распознавать доступные текущие SavedVariables. Мост подписывается на документированные события готовности, получает RecipeData, копирует разрешённые скалярные данные в **свои** SavedVariables. Никаких вызовов `RecipeData:Craft`, автоматических кнопок/покупок, изменения чужих таблиц или ключей API. Полная сериализация произвольного объекта опасна из-за ссылочных циклов и неполного JSON; нужен явный DTO.

Контракт экспортёра как предложение (не реализован):

- exportSchemaVersion, addonVersion, gameVersion/build, source hashes, UTC/game timestamp, export sequence, locale, region/realm, псевдоним crafters;
- recipeID, isRecraft/isWorkOrder, flags, механические требования, snapshot completeness, live/cache/simulation provenance;
- точные reagents по слотам, варианты/качества/optional/finishing reagents, quantity, распределение расхода либо unknown;
- recipe difficulty, profession skill/stats, gear/spec/buffs fingerprint, concentration mode/cost/availability observation time, cooldown group/charges;
- output item IDs + ссылки/контекст/bonus IDs/качество, min/max yield, expected yield, отдельные proc coefficients и вероятностные состояния с пометкой model estimate;
- исходные reagent/output prices, currency, единицы copper, source/override flags/time, CraftSim crafting cost и средняя прибыль как сопоставительный результат;
- error/nil/unsupported и отдельные validity flags вместо искусственных нулей.

Требования профессии хранить независимо от пользователя. **Не выполнять фильтр «этот персонаж может/не может»**: это отложено ТЗ. Конкретный crafter snapshot нужен для условного расчёта; наличие снимка не означает разрешение проверять или скрывать глобальные преобразования по составу персонажей.

С закрытой игрой можно переоценить **фиксированный экспортированный вариант** по свежим ценам, если известны все необходимые расходные/выходные коэффициенты, их совместное поведение и расходы. Если есть только `lastCraftingCost`, пересчёт ингредиентов невозможен: это сумма по старым ценам. Если имеется только ожидаемый yield, можно оценить ожидание, но нельзя симулировать достоверные хвосты риска. После изменения цен ранее оптимальная комбинация реагентов/gear может перестать быть оптимальной: пересчитывать набор уже экспортированных вариантов; не выдавать его за глобальный оптимум. Новый build, спеки/gear/buffs, изменённая механика, неизвестный reagent combination или концентрация требуют нового расчёта CraftSim/явного статуса stale. Это **H**, который должен пройти сравнительный тест в игре.

### Подозрение C11: минимальная воспроизводимая проверка

В предположении негрей результата, отсутствия work order, включённого TSM deposit и `d>0`, приведённый исходник даёт `resultValue=0.95*P-d`, затем `profit=resultValue-C-d=0.95*P-C-2d` в ветвях Resourcefulness. Для `P=100000`, `C=60000`, `d=1000` получается 33000 copper вместо результата с одним вычетом 34000. Это символическая трассировка, **не выполненный Lua-тест** и не установленный дефект авторской экономической трактовки.

В этапе 3 проверить на фиксированном RecipeData с ресурсностью: options disabled/enabled, d=0/d>0, серый результат, work order, MC on/off. Сопоставить с семантикой TSM expression (expected loss vs refundable deposit; per unit vs craft). Сначала отчёт о расхождении, затем изоляция экономического компонента; не менять сторонний аддон. Депозит в нашем движке — отдельный денежный поток/возврат/потеря, чтобы не вычитать дважды через импортированную уже чистую прибыль.

## Практически найденные кандидаты преобразований

Лексический подсчёт **только DATA.Retail** установленного TSM, 26.10.2026:

| Файл в `TSM\LibTSMData` | Начало Retail | Выходные ключи `i:` | Строки source→target / диапазоны |
|---|---:|---:|---:|
| Destroy\Disenchant.lua | 1139 | 49 | 177 class/quality/itemLevel conditions |
| Destroy\Mill.lua | 215 | 44 | 225 пар |
| Destroy\Prospect.lua | 488 | 148 | 678 пар |
| Convert\Transform.lua | 316 | 148 | 174 пары |
| Convert\VendorTrade.lua | 98 | 21 | 21 пара |

Это не число уникальных операций и не процент покрытия игры. Одно преобразование может дать несколько совместных выходов; строки с общей входной вещью нельзя превратить в независимые производственные действия. Дробные `amountOfMats`/rates часто представляют ожидание на единицу входа, а не физический recipe quantity. Прежде чем перевести таблицу в операции, нужно восстановить batch size, целочисленность и совместность выходов; `minAmount/maxAmount/matRate` не всегда задают полную PMF. Источник не предоставляет для каждой строки дату измерения/размер выборки/текущий hotfix — не присваивать confirmed-current только потому, что таблица поставляется со свежей версией аддона.

`LibTSMTypes\Source\Item\Conversion.lua:32` имеет MAX_CONVERSION_DEPTH=3. Это ограничение чужого поиска, **не ограничение нашего проекта**: переиспользование таблиц не должно импортировать скрытый предел длины цепочки.

### C22: пять конкретных примеров для повторной проверки

Ниже кратко зафиксированы найденные числовые сведения для исследования, а не публикуемый seed или инструкция к торговле. Полные таблицы не копируются. У таблиц conversion внешний ключ — целевой предмет, внутренний — исходный; это подтверждает читающий их код `Conversion.lua:40–63`. Для всех строк статус **S**, runtime-current = **не проверено**.

| Семейство и кандидат | Точные сведения из источника | Локальный источник | Что нельзя заключить без проверки |
|---|---|---|---|
| Объединение/разделение эссенций | `52718 → 52719`: коэффициент `1/3`; обратный `52719 → 52718`: `3` | `TSM\LibTSMData\Convert\Transform.lua:318–322` | Это отношение выход/вход. Целочисленная кандидатная нормализация первого направления — 3→1; **одну треть физического предмета** создавать нельзя. Сам коэффициент не доказывает минимальный разрешённый batch, текущую доступность действия и отсутствие дополнительных условий. Обратный цикл возвращает исходное количество и сам по себе не приносит прибыль. |
| Обмен чернил у торговца | `173058 → 39469`: коэффициент `1` | `TSM\LibTSMData\Convert\VendorTrade.lua:98–100` | В строке нет NPC ID, наличия в продаже, даты проверки и ограничений. Нельзя утверждать, что обмен сейчас доступен или что это полный перечень требований. |
| Измельчение Silverleaf | источник `765`, выход `39151`; `matRate=1.0000`, `minAmount=2`, `maxAmount=4`, `amountOfMats=0.5780` | `TSM\LibTSMData\Destroy\Mill.lua:215–218` | `0.5780` — коэффициент модели TSM, а не целое количество результата одной операции. Из min/max не следует равномерная PMF; строка не задаёт вероятности отдельно для 2, 3 и 4. Batch size и нормировку нужно подтвердить независимо. |
| Просеивание Copper Ore | источник `2770`, выход `774`; `matRate=0.5000`, `minAmount=1`, `maxAmount=1`, `amountOfMats=0.1000`. Соседняя запись другого выхода `818` для того же `2770` также имеет `0.5000/1/1/0.1000` | `TSM\LibTSMData\Destroy\Prospect.lua:488–494` | Эти marginal coefficients не доказывают независимость/взаимоисключение двух выходов. Нельзя выдать 0.1 камня за гарантированный предмет, объединить две строки в две бесплатные операции или получить joint PMF перемножением без доказательств. |
| Распыление класса предметов | выход `10940`, `expansion=0`; строка `ARMOR`, quality `2`, itemLevel `2..7`: `matRate=0.800`, `minAmount=1`, `maxAmount=6`, `amountOfMats=1.222` | `TSM\LibTSMData\Destroy\Disenchant.lua:1139–1146` | **Точного входного item ID эта строка не даёт.** Требуется сопоставить реальный вариант вещи, уровень и исключения. Не создавать выдуманный входной ID и не считать все вещи диапазона распыляемыми. Полного распределения 1..6 и остальных совместных/альтернативных выходов здесь нет. |

Для этих пяти примеров остаются отдельно: проверка текущего Retail build/hotfix, transfer/sale restrictions, расхода входа, разрешённого batch, cooldown и источник вероятностей. Наличие старого item ID в актуально установленной Retail-таблице подтверждает **покрытие кандидата источником**, а не истинность всех его коэффициентов в текущей игре. Текст лицензии TSM (C15) остаётся ограничением массового копирования/распространения; эти краткие адресные исследовательские примеры не являются разрешением на такой импорт.

## Первичное массовое наполнение: решение и обоснование

Единого проверенного полного источника всех преобразований не найдено. Нужен объединяющий каталог, а не один «master API». На первом запуске охватывать **все расширения Retail** из одного подтверждённого Retail build, включая старый контент; не брать механики Classic как замену современному поведению старых предметов.

1. Зафиксировать build/region, manifest источников, лицензий и hashes. Индексы профессий/tiers/recipe Blizzard (контракт исследуется отдельно в market-sources), игровые схемы рецептов и доступные разрешённые структурированные данные дают первоначальный перечень. Все страницы пройти с checkpoint, retry/backoff, cache; полный статус зависит от успешного завершения индекса, не от sample.
2. Нормализовать профессиональные recipes со всеми входными слотами/валютами/вариантами и выходами. CraftSim добавляет **calculation profiles**, а не владеет глобальной базой преобразований.
3. Добавить кандидатов из DB2 `SkillLine/SkillLineAbility`, `SpellReagents`, `SpellEffect`, `ItemEffect`, `ItemExtendedCost`, item/currency metadata. DB2 и схемы позволяют обнаружить связь, но не доказывают серверные loot tables, доступность NPC, скрытый cooldown или текущую продаваемость. Неразрешённые связи остаются quarantined.
4. Разрешённые данные TSM/других авторов могут наполнить milling/prospecting/disenchanting/convert/vendor families после проверки условий использования. До неё — только сведения о наличии и план проверки, без копирования массивов в seed.
5. Контейнеры, item use, salvage/recycling/scrapping, NPC barter, currency purchases, periodic rewards/cooldowns: полный обход конкретных закреплённых discovery corpora и таблиц по [плану R2](transformation-discovery-r2.md), затем evidence validation, а не подбор небольшого curated batch. Записи сохраняют item/spell/NPC IDs, контекст и подтверждения. Локальные merchant observations — резервный путь верификации при обычных действиях пользователя; они не заменяют массовое перечисление и не обещают полный ассортимент всех NPC.
6. Вероятностные выходы: хранить исходную выборку/источник, n, build, conditions, модель распределения и интервалы. Если известны только среднее/диапазон — именно эти поля; unknown probability остаётся unknown. Не «достраивать» неизвестные вероятности равномерным распределением и не присваивать нулевые неувиденные события.
7. Проверка предметных вариантов, ограничений передачи/продажи, обязательных расходников, cooldown groups, batch sizes, shared currency, balances. Удалённые/устаревшие рецепты сохранять как historical/inactive, без рекомендации их исполнения.
8. Выпустить отчёт полноты и список исключённых записей. Отсутствие закрытого семейства не блокирует импорт остальных подтверждённых, но должно быть явно видно в первом отчёте.

Обновления: локальные hashes/build и source manifest сравниваются до вызова ИИ. Еженедельная задача исследует только изменившиеся записи/категории-пробелы, после патча — дополнительные затронутые family/build checks. Вести source published/observed/ingested/effective dates отдельно. Переоценка по ценам — зависимостями item→transform→chain на каждом снимке; новый вызов ИИ не обязателен. Ошибка пользователя связывается с immutable recommendation/transform version, проверяется независимыми источниками на текущем build; при сомнении quarantine/lower trust, при подтверждении новая версия и обратимый пересчёт.

## Матрица покрытия

Сейчас **семантический импорт не выполнялся**: confirmed records = 0 во всех ячейках; это стадия исследования. Число всей игры неизвестно. Нельзя писать «0% игры» или «покрыто 100%» без корректного знаменателя.

| Retail expansion scope | Профессии | Disenchant/mill/prospect | Use/container/conversion | NPC/currency/особые |
|---|---|---|---|---|
| Base / Vanilla content в Retail | индекс/API + схемы, проверить | TSM Retail кандидаты | TSM/DB2 кандидаты | отдельный каталог, denominator unknown |
| Burning Crusade content в Retail | то же | то же | то же | то же |
| Wrath of the Lich King content в Retail | то же | то же | то же | то же |
| Cataclysm content в Retail | то же | то же | то же | то же |
| Mists of Pandaria content в Retail | то же | то же | то же | то же |
| Warlords of Draenor content в Retail | то же | то же | то же | то же |
| Legion content в Retail | то же | то же | то же | то же |
| Battle for Azeroth content в Retail | то же | то же | то же | то же |
| Shadowlands content в Retail | то же | то же | то же | то же |
| Dragonflight content в Retail | API + CraftSim profile candidates | TSM/DB2, проверить quality | TSM/DB2 кандидаты | то же |
| The War Within content в Retail | API + CraftSim profile candidates | TSM/DB2, проверить quality | TSM/DB2 кандидаты | то же |
| Midnight content в Retail | найден в `CS\Util\Const.lua:171–183`, API/profile нужно проверять | неизвестная фактическая полнота | неизвестная фактическая полнота | то же |

Каждая строка дополнительно разбивается по profession/category, operation family, stochastic/deterministic, item variant, status и build. В будущей сводке хранить `source_enumerated_total`, `fetched`, `normalized`, `validated_current`, `quarantined`, `unsupported`, `missing_inputs`, `unknown_probability`, `unknown_total`, `license_blocked`. Измеримое покрытие = validated/source_enumerated **для именованного источника**, не «процент всей игры». Для открытых категорий NPC/контейнеров глобальный denominator=null; внутри закреплённого корпуса знаменатель — полный перечисленный scope, его обход измерим. Матрица выше отражает ранние направления R1; повторяющееся «то же» не достаточный контракт завершения. R2 задаёт конкретные источники, file/page traversal, разделение metadata/sample/semantic completion и обязательный disposition всех записей в [D2](transformation-discovery-r2.md). Каждая expansion/category строка появляется даже с нулём, чтобы старое расширение не исчезло молча.

## Минимальные испытания следующих этапов

1. **Реальная интеграция CraftSim:** пользователь открывает выбранный рецепт, мост захватывает DTO после ready event, ручной `/reload`/выход записывает свой файл. Python читает его, UI/CraftSim/DTO совпадают в copper, item IDs, реагентах, качестве и stats; экспорт без learned recipe и недоступной схемы даёт явный статус. Сейчас не выполнено.
2. **Смена состояний:** gear/spec/buffs/reagents/quality/concentration/price override/build invalidates fingerprint; кэш другого крафтера не считается live; stale export не даёт уверенную рекомендацию. Сейчас не выполнено.
3. **Экономическая сверка:** synthetic фиксированный профиль, раздельный депозит/комиссия/выходы/стоимость ресурсов, C11 case. Expected profit CraftSim хранится отдельно от market valuation. Сейчас только исходник/символическая трассировка.
4. **Массовый импорт:** полный pass каждого заявленного индекса всех Retail tiers, restart after page failure без дублей, Classic rows rejected, source totals reconcile, отсутствующий output → quarantine. Сейчас не выполнено.
5. **Вероятности:** fractional expected yield не создаёт дробный физический предмет в реализации; совместные co-products, редкий неувиденный исход и неизвестная PMF не заменяются уверенностью; risk interval не строится из одного среднего. Сейчас критерий.
6. **Цепочки:** 3 lesser→1 greater→3 lesser не создаёт прибыль без рыночной разницы/издержек; одна руда не расходуется в двух дочерних путях; общий daily cooldown/currency budget учитывается один раз; consumable tool и reusable tool различны. Сейчас критерий.
7. **Откат знаний:** hotfix/feedback создаёт новую версию, пересчитывает только затронутые цепочки, сохранённый прогноз восстанавливается по старому knowledge state; unknown остаётся допустимым. Сейчас критерий.

## Воспроизведение фактических проверок

Ни одна команда ниже не исполняет Lua и не записывает WoW. Для SV отчёт намеренно не публикует путь с именем аккаунта или значения персонажей.

```powershell
$cs = 'W:\Battle.net\Games\World of Warcraft\_retail_\Interface\AddOns\CraftSim'
Get-Content -LiteralPath "$cs\CraftSim.toc" -TotalCount 28
rg -n 'function CraftSimAPI|function CraftSim.RecipeData|expectedDeposit|combinationProfit' "$cs\Util\API.lua" "$cs\Classes\RecipeData.lua" "$cs\Pricing\ProfitCalculation.lua"
Get-FileHash -LiteralPath "$cs\CraftSim.toc","$cs\Util\API.lua","$cs\Classes\RecipeData.lua","$cs\Pricing\ProfitCalculation.lua" -Algorithm SHA256
$sv = Get-ChildItem -LiteralPath 'W:\Battle.net\Games\World of Warcraft\_retail_\WTF' -Filter CraftSim.lua -File -Recurse | Select-Object -First 1
$svText = Get-Content -LiteralPath $sv.FullName -Raw
[regex]::IsMatch($svText, '\["lastCraftingCostDB"\] = \{\s*\["version"\] = 1,\s*\["data"\] = \{\s*\}')
```

Последняя проверка дала `True`. Regex здесь только ограниченная проверка известного фрагмента; это **не выбранный способ парсинга Lua приложения**.

| Файл | SHA-256 |
|---|---|
| CraftSim.toc | `1BA0191AE7208F95B1EBF75150EC332240F3B2EDC090C46F9A03EE469CD2128C` |
| Util/API.lua | `A4A5573AF2D6C7B7BB0EE65BCA5F3AEFF83FA8FA9D27B43BF58DDC3060E2A247` |
| Classes/RecipeData.lua | `4EC007B240C784ECFED3E9D99F5511C4BCEA6B13BEBEFCFEC0833BED14E5AE44` |
| Pricing/ProfitCalculation.lua | `4CB252683F0F49FC0038D027CE26CC1D770C9F9733308A25BA30EFF144C17980` |
| Реальный CraftSim.lua SV | `5413DBC15F6D9614A267CE7EF221641330807E013037F33C2104D62CF5707C43` |

Счётчики таблиц воспроизводятся чтением участка от первого `DATA.Retail = {` до EOF и regex: output keys `(?m)^\t\["i:`, пары `(?m)^\t\t\["i:`, disenchant conditions `\{class =`. Файлы заканчиваются Retail-секцией; это лексические строки, не выполненный нормализатор.

## Конкретные оставшиеся условия

- **Runtime CraftSim:** на разрешённом будущем этапе — короткая сессия игры с ручным открытием тестовых рецептов, затем экспорт собственного моста и ручной flush; API-ключи не требуются. Без этого невозможно подтвердить качество/события/DTO на реальных расчётах.
- **Источник преобразований:** документированные bulk endpoints/разрешённые дампы и условия использования для Wago/Wowhead; для TSM — допустимость reuse таблиц в локальной базе и отдельно распространения. Не отправлялись письма правообладателям и не обходились 403.
- **Патч:** локальный build и TOC — разные свидетельства. Не объявлять актуальным любой upstream table без сравнения game build/hotfix; сам факт установленного аддона не гарантирует поддержку локального build.
- **Неопределённые механики:** полные joint distributions для destructive operations, NPC availability/cooldowns/stock, правдоподобие серверных loot tables, точные item variants. Эти пробелы блокируют надёжную рекомендацию соответствующей цепочки, а не импорт всех остальных подтверждённых записей.
