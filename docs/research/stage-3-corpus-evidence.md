# Этап 3: полный статический обход корпуса ATT

Дата: 26.10.2026. Это свидетельство импорта источника, а не независимая приёмка и не подтверждение всех игровых механик.

Использован закреплённый [ATT commit](https://github.com/ATTWoWAddon/AllTheThings/commit/3d0461d9740940e09c9cb19390467ed6ec39cb9c), root tree `a1bf6ba088a155e71449d1009f4ae21967427b6f`. Получено полное дерево: 6103 entries, `truncated=false`. Согласованные **1874 Lua-файла standard вне .config** полностью включены; вместе со всеми shared Lua, конфигурациями и LICENSE — **3470 файлов, 114 285 381 bytes**. Содержимое каждого проверено по Git blob SHA-1, дополнительно сохранён SHA-256. Знаменатель относится к файлам источника, а не ко всем операциям WoW.

Машинное свидетельство с полным списком файлов, disposition, количеством кандидатов, расширениями, символическими ссылками, исключёнными по правам/формату ветвями и хешами кода находится в [stage-3-corpus-evidence.json](stage-3-corpus-evidence.json). Сами массивы авторских claims и исходные файлы находятся только в локальном `.private/stage3/`; сведения аккаунта в них не используются.

Результат нормализации: **141687 source candidates** (111225 candidate, 19664 historical, 10798 quarantined). Это не уникальные игровые операции: один объект может упоминаться в разных контекстах. Разобрано 23606 recipe associations, 15498 purchase/exchange claims, 55798 item relations, 4770 periodic candidates, 48 container relations, 13 salvage-recipe relations и 41954 узла пока неизвестной экономической семантики. Последние сохранены именно для дальнейшего исследования, а не как готовые преобразования. 36 адресных синтетических тестов пройдены.

## Что сделано

Импортёр не исполняет Lua, скрипты сборки, загрузчики ATT или игровые действия. Он лексически отделяет строки и комментарии, строит ограниченную структуру скобок, читает literal-таблицы и известные constructors, фиксирует иерархию, cost и _cost, recipe/item associations, periodic markers, container links, символические ссылки и неизвестные выражения. Поддержанный subset preprocessor учитывает Retail, известные flavor/patch-условия, регистр и пробелы директив; неподдержанное условие приводит к quarantine кандидата. Для каждого конструктора есть учёт disposition, включая неэкономические узлы и неподдержанную идентичность.

Все старые дополнения остаются в обходе; unknown-era сохраняется отдельно. Текущая профессия персонажа не фильтрует каталог. Метки расширения из пути и авторских PATCH-заголовков — таксономия источника, а не доказанная дата действия механики. Обновление получает новый exact commit, проверяет его tree и неизменность согласованного LICENSE, после чего с параметром reuse_root переносит из прежней версии только blobs, чей Git hash совпал с новым tree, и скачивает отсутствующие/изменённые blobs. Повтор использует лишь файлы с совпавшим Git hash. Новая неизвестная ветвь попадает в manifest; при ошибке загрузки `complete=false`.

Важная семантическая проверка: `local i = GetRecipeHelperForProfession(...)` в Profession DB переопределяет обычный item constructor. Согласно [Shortcuts.lua](https://github.com/ATTWoWAddon/AllTheThings/blob/3d0461d9740940e09c9cb19390467ed6ec39cb9c/.contrib/.db/shared/functions/Shortcuts.lua#L4104), `i(itemID, recipeID)` связывает предмет обучения с рецептом; `itemID=0` обращается непосредственно к RecipeDB. Эти предметы **не объявлены результатом крафта**. RecipeID и optional requirement/timeline сохранены отдельно.

Аналогично, `container` и `salvagerecipe` дают source relation, а вложенные коллекционные награды не превращены в гарантированные outputs или равномерную PMF. isWeekly/isDaily не превращены в общий лимит всех экономических действий. Parent removal не отменяется более поздней датой дочернего объекта. Четыре PREY season-one контейнера 257023/257026/262346/268545 имеют historical status по родительскому `removed 12.1.0.69214`, а ссылка на локальный PREYSEEKER_BOX_SYM_SEASON_ONE имеет найденное определение и **неразрешённую семантику**.

## Проверка препроцессора по первичному коду

В промежуточной att-static-2 два файла оставались unsupported: Brawler's Guild с `#end` и Sanctum Of Light с `#endfif`. Они не были объявлены доказанными дефектами ATT. Дополнительное чтение [Program.cs:1035–1050](https://github.com/ATTWoWAddon/AllTheThings/blob/3d0461d9740940e09c9cb19390467ed6ec39cb9c/.contrib/src/Parser/Program.cs#L1035) установило, что default-ветвь `ProcessInternalCommandBlock` закрывает текущий блок при таком токене. В att-static-3 эти две реально встреченные формы поддержаны с регрессией. Источники не изменены, чужой препроцессор не исполнялся; полный повторный обход больше не имеет этих искусственных пропусков.

Завершение обхода не означает semantic completeness: unknown expressions/sym, часть inheritance и подтверждение игровых механик остаются открытыми. Пакет не заявляет полный PASS T3-02 по всем источникам или подтверждённое покрытие всей игры.

## Права и отсутствующие данные

Для pinned author-source действует [MIT LICENSE](https://github.com/ATTWoWAddon/AllTheThings/blob/3d0461d9740940e09c9cb19390467ed6ec39cb9c/LICENSE), copyright 2026 AllTheThings WoW Addon. Полный notice сохранён с локальным корпусом. Код импорта написан отдельно; экспорт массивов в облако и их публикация не включены. Новое изменение LICENSE блокирует дальнейший bulk fetch до проверки политики.

Весь `.config/.wago/` имеет отдельный license-blocked manifest. Исторически исследованные ItemXItemEffect/ItemEffect (60644/62149 rows) **не объявлены повторно импортированными**: права исходных Wago/Blizzard данных не наследуются из MIT репозитория. Дополнительные 117 non-Lua/config entries сохранены в scope manifest с конкретными ролями, а не исключены из задачи одной меткой:

- **2 обязательных JSON base dependencies:** itemDB.json (23776743 bytes) и questDB.json (2312303 bytes). [Parser:339–355](https://github.com/ATTWoWAddon/AllTheThings/blob/3d0461d9740940e09c9cb19390467ed6ec39cb9c/.contrib/src/Parser/Program.cs#L339) загружает их перед Lua; ParseJSONFile затем вызывает Framework.Merge. Прочитаны только первые 16384 bytes каждого через HTTP206. В этих частях есть metadata/binding/spell links и quest rewards/requirements/repeatable. Их важность для полноты признана; полный импорт не заявлен.
- **104 MissingDB enumeration lists:** содержат дополнительную область discovery. Harvester.py:323 использует Wago DB2, :397–421 сравнивает profession lists, :807–810 описывает parser-debug workflow. Их происхождение/права также остаются отдельным блокером, а не поводом незаметно удалить ветвь.
- **4 Conduits CSV и 1 производный TXT:** metadata для преобразования conduit/class/spec identifiers; происхождение и права всей входной таблицы не подтверждены.
- **3 Python helper sources:** генераторы не исполнялись; **3 color TXT notes** — оформительские сведения, не экономические механики.

Для первых 111 entries установлен `rights_provenance_blocked`, `required_dependency=true`. Найден [исторический генератор:1530–1578](https://github.com/ATTWoWAddon/AllTheThings/blob/3d0461d9740940e09c9cb19390467ed6ec39cb9c/.contrib/src/Blizzard%20API%20Harvester/Program.cs#L1530), который создаёт одноимённые структуры itemDB-/questDB- из внешних игровых payloads, и Compare Tool, объединяющий версии. Это доказательство существования upstream-конвейера, **не доказательство происхождения каждой строки нынешних JSON и не заключение об их незаконности**. MIT кода не принят за автоматически подтверждённые права на все производные данные. Никаких Blizzard API запросов, ключей или запуска генераторов при исследовании не было. Полная semantic dependency closure до разрешения этих пробелов не заявляется. Точные hashes, строки кода и предел чтения — [dependency evidence](stage-3-corpus-evidence.dependencies.json). TSM Retail destructive/transform tables остаются license-blocked (ARR), а полная разрешённая таблица scrap/obliterate/mill/prospect/disenchant/container yields и совместных вероятностей не найдена. Это видимые ограничения исходных данных, а не нулевые выходы.

Статические claims не становятся confirmed-current только из-за совпадения build. Поле `confirmed_current_transformations` остаётся 0 до дополнительных механических доказательств; адаптер CraftSim предоставляет другой слой данных. Наличие нового tree, нового рынка или старого названия предмета не доказывает актуальную исполнимость.

## Воспроизведение

```powershell
.venv/Scripts/python.exe -B tools/fetch_stage3_corpora.py
.venv/Scripts/python.exe -B -m unittest tests.test_knowledge_att tests.test_knowledge_transport
```

Для статического обхода используется `wow_auction.knowledge_att.scan_corpus(Path(".private/stage3/corpora-att"))`; он возвращает per-file manifest и source candidates. Хранилище, обновления, rollback и итоговые проверочные команды описаны в основном отчёте этапа 3. Fetch требует обычного доступа к публичным GitHub host, не учётных данных. После первого полного получения статический scan и тесты работают без сети и игры.

## Изменения до итогового пакета

В разработческой ревизии att-static-1 простые локальные constants из отдельных Lua-файлов оставались unknown. Проверка реальных sentinel nodes выявила доступные `GENESIS=188957`, `AMBYSTAN_LATTICE=187634`, `FIELD_ACCOLADE=3405`. В att-static-2 поддержаны начальные однозначные local literal declarations с разделителем; повторное присваивание, арифметика и неизвестная область видимости не вычисляются. Повторный полный scan сохранил 141278 claims и изменил только доступную нормализацию. Предыдущий локальный capture сохранён; его hash и причина изменения перечислены в JSON evidence.

## Ограничение сетевого ожидания

`knowledge_transport` проверяет отдельный список публичных ATT GitHub URL, запрещает credentials, сторонние hosts, неподдержанные query/fragment/пути и принимает только HTTP200. После проверки используется уже проверенный изолированный GET-процесс этапа 2: общий deadline 30 секунд включает DNS, headers и body; cleanup ограничен прежним механизмом. Код `market_http.py` не расширялся. Собственный cap — 32MiB; он нужен для единственного source Lua размером 18,7MB. Возобновление между ревизиями копирует старый blob только после сравнения с hash нового tree. Новый реальный запрос LICENSE через этот transport получил 1079 bytes за 0,746s с совпавшим Git hash; это не проверка всех сетевых аварий в реальном интернете, отрицательные сценарии покрыты тестами.

Итоговый полный scan att-static-3 выполнен 26.10.2026 13:43:57 UTC за 33,134s (35,145s с записью JSON): 3470 terminal file dispositions, unsupported files 0. 409 ранее закрытых строгим subset source candidates добавлены из двух файлов после проверки фактической семантики ATT. Предыдущие captures v1/v2 сохранены, итоговый v3 имеет отдельный файл и hash.
