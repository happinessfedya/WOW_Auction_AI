# Этап 0 — рыночные источники и Blizzard API

Исследовательский отчёт субагента `market_sources`; не является независимой приёмкой. Проверено 26 октября 2026, примерно 02:50–03:05 Europe/Moscow (25 октября 23:50 — 26 октября 00:05 UTC). Прочитан весь пакет `codex_handoff`. Выполнялись только чтение документации, исходников и публичные HTTP GET. Ключи не искались, не передавались; приложение, адаптеры и фоновые задания не создавались. Факты ниже имеют идентификаторы M01–M12 для общего реестра доказательств.

Статусы: **D** — документировано владельцем; **S** — прочитан исходный код; **R** — наблюдение реального ответа/данных; **H** — проектная гипотеза; **B** — конкретная непроверенная зависимость. Статусы могут сочетаться: HTTP 401 подтверждает ответ службы, но не успешную интеграцию.

## M01. Undermine Exchange: действующий опубликованный контракт

**D; успешный запрос с ключом B.** [Официальный API](https://undermine.exchange/api.html): Retail; US/EU/TW/KR; `Authorization: ApiKey …`, ключ после входа через Patreon даже для бесплатных методов. Квота — 3000 points за скользящий час; gzip уменьшает стоимость. Базовые методы бесплатны, детализация уровней/суффиксов/пород требует подписки. Метаданные предметов отсутствуют.

| Путь после `https://api.undermine.exchange` | Points с gzip | Данные |
|---|---:|---|
| `/v1/static/realms.json` | 1 | Миры |
| `/v1/region/:region/commodities.json` | 3 | Региональный снимок |
| `/v1/realm/:region/:realm/items.json` | 3 | Серверный снимок |
| `/v1/region/:region/commodities/:itemId/now.json` | 1 | Цены/количества предложений |
| `/v1/realm/:region/:realm/items/:itemId/now.json` | 1 | Предложения предмета |
| `/v1/region/:region/commodities/:itemId/hourly.json` | 3 | Примерно 14 дней |
| `/v1/region/:region/commodities/:itemId/daily.json` | 3 | Дневная история |

`snapshot` — время Blizzard, `lastUpdated` — генерация файла; `lastSeen` — последний ненулевой остаток. При нулевом количестве историческая цена переносится. Дневная точка соответствует максимальному количеству, не закрытию дня. Часовая периодичность не гарантирована. Связанные миры разделяют аукцион. Услуга предоставляется без гарантий доступности/точности.

## M02. Undermine: проверка транспорта

**R/B.** GET `https://api.undermine.exchange/v1/static/realms.json` без Authorization: **401**, JSON, 50 bytes, HTTP Date `Sun, 25 Oct 2026 23:58:33 GMT`. Это согласуется с необходимостью ключа; содержимое рынка не получено. Веб-поисковик этот JSON не смог открыть; PowerShell с разрешённым сетевым чтением получил конкретный ответ.

На [главной странице](https://undermine.exchange/) поисковое представление содержало строку о maintenance одновременно с обычной оболочкой интерфейса. Она не доказывает остановку API: её нельзя использовать вместо HTTP и authenticated smoke-test. Проектное решение **H**: поддерживать отдельное состояние здоровья каждого метода/набора данных; не объявлять весь источник исправным по доступности HTML.

## M03. Undermine: проверка открытого кода и лицензии

**S.** Через GitHub connector прочитаны [репозиторий Shatari](https://github.com/erorus/shatari), его README, список `src`, [battlenet.js](https://github.com/erorus/shatari/blob/master/src/battlenet.js). Blob SHA последнего: `fc4009539794f59b05192dee89e8c3ffb43a9a11` на момент чтения. Файл формирует региональный Blizzard URL, namespace, Bearer header, запрашивает client-credentials token и кеширует его по `expires_in`. Это проверка реализации автора Undermine, а не выполнения этой реализации и не официальный контракт Blizzard.

README описывает получение данных Blizzard и собственные агрегаты; опубликованная лицензия кода — Apache-2.0. Лицензия кода **не устанавливает автоматически** разрешение на повторную публикацию всех рыночных данных, вечное хранение или обход Patreon. История Undermine и Blizzard не являются двумя независимыми наблюдениями торговой активности, если имеют общий исходный снимок. Для lineage нужен `upstream_snapshot_id`, для проверки гипотез — независимость по происхождению, а не числу доменов.

## M04. TSM: новый публичный путь вместо непроверенного старого API

**D.** [TSM Public Web API support](https://support.tradeskillmaster.com/en_US/api-documentation/tsm-public-web-api), обновление 5 июля 2026: рекомендуется публичный CSV, без ключа, входа и объявленных rate limits; нестандартные применения предлагается обсуждать с TSM. Старую статью 2022 года нельзя считать актуальным контрактом авторизации.

[Public Pricing Data](https://tradeskillmaster.com/public-data) задаёт файлы `{gameType}/{region}/realm/{slug}/{items|pets}.csv`, `{gameType}/{region}/commodities.csv`, `{gameType}/{region}/region/{items|pets}.csv`. Цены в copper; `updatedAt` означает upstream scan, не генерацию файла. Серверные/товарные файлы меняются раз в несколько часов, региональные — ежедневно; это описание, не SLA. Питомцы представлены базовым species, без уровней. Корень хоста вернул 404 через веб-инструмент; корректные файлы ниже доступны.

## M05. TSM: реальные публичные CSV

**R.** GET без авторизации, PowerShell `Invoke-WebRequest`, `ConvertFrom-Csv`; содержимое не сохранялось. Для commodities и realm проверка HTTP около `2026-10-25T23:58:31Z`, повторная проверка схем/счётчиков около полуночи UTC.

| URL | HTTP | Строк данных | `updatedAt` | HTTP Last-Modified |
|---|---:|---:|---|---|
| [EU commodities](https://public-data.tradeskillmaster.com/retail/eu/commodities.csv) | 200 | 12162 | `2026-10-25T21:31:39Z` | `2026-10-25T21:36:12Z` |
| [EU Soulflayer items](https://public-data.tradeskillmaster.com/retail/eu/realm/soulflayer/items.csv) | 200 | 11027 | `2026-10-25T21:31:39Z` | `2026-10-25T21:36:44Z` |
| [EU region items](https://public-data.tradeskillmaster.com/retail/eu/region/items.csv) | 200 | 31186 | `2026-10-24T00:55:11Z` | `2026-10-25T00:31:31Z` |

Первые два заголовка: `itemId,name,marketValue,minBuyout,recent,historical,updatedAt`. Третий: `itemId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt`.

В первых двух файлах соответственно **1550 и 4979 строк с `minBuyout=0`**. Нулевое поле нельзя превратить в бесплатный покупаемый товар; адаптер должен сохранять исходное значение и отдельно интерпретировать отсутствие котировки после проверки семантики. В этих двух схемах нет глубины, доступного количества и идентификаторов лотов. Поле `historical` — скалярная статистика, не восстановленная последовательность исторических снимков. Региональный файл заметно старше времени HTTP получения.

SHA-256 UTF-8 декодированного содержимого (не hash сжатого сетевого потока):

- commodities: `70E3DCB6ADCE49C6B9B5AAF8CA5ECC733EA8F7BEE20370A3DA2B960047323981`;
- Soulflayer: `E19CFA420B685C491B890BE7A7D5F005D4722091ED82307F91904BC6AFB06A96`;
- region: `37673AABECF9A86B1E3D782C5CD873AC7DE8F31D0A00C9F570B1A1761CBB0A30`.

Успешный slug `soulflayer` **не подтверждает connected-realm ID** и не делает этот сервер константой приложения. Для IDs нужна отдельная проверка Blizzard `realm` → `connected_realm` → список миров группы. Уровни/bonusIDs нельзя восстановить из этих CSV по одному itemId.

## M06. TSM: смысл агрегатов и риск ложной ликвидности

**D.** [Определения value sources](https://support.tradeskillmaster.com/en_US/custom-strings/which-value-sources-can-i-use-and-what-do-they-mean) описывают `DBRegionSaleRate` как частоту продаж на выставление, усреднённую по аукционам региона; `DBRegionSoldPerDay` — среднее в день на аукцион. Документ 2021 года предшествует региональным commodities: перенос трактовки знаменателя на нынешние commodities требует актуального уточнения TSM. [Market Value](https://support.tradeskillmaster.com/en_US/custom-strings/how-is-auctiondb-market-value-calculated) — обработка цен предложений с фильтрацией и временными весами, не подтверждённая цена исполнения.

**H, проектное следствие.** Использовать эти признаки как prior с отметками возраста/семантики/покрытия; не назначать `P(продажа моей позиции за H часов)=saleRate`. Для калибровки нужны собственные наблюдения с exposure, количеством, длительностью выставления, отменами и режимом рынка. Не выводить подтверждённый региональный turnover из изменения quantity или `soldPerDay`, пока его методология и охват не проверены.

## M07. Ledger/Desktop: что подтверждает внешняя документация

**D/B.** [Официальное описание экспорта](https://support.tradeskillmaster.com/en_US/tsm-desktop-application/how-do-i-read-or-convert-the-time-in-my-accounting-export) подтверждает экспорт Accounting/Ledger Desktop-приложением и Unix timestamps. [Пропущенные покупки/продажи](https://support.tradeskillmaster.com/en_US/addon/why-am-i-missing-sales-or-purchase-data-on-my-items) описывает запись при получении почты и возможную потерю информации о варианте предмета. Это не доказательство полноты нынешней установленной версии; локальные схемы и события проверяются в соседнем отчёте.

Проектное следствие **H**: предпочесть безопасное чтение уже существующей истории и/или штатный CSV; внешний сервис Ledger не нужен как новая зависимость ради копирования учёта. Сохранять `event_time`, `observed_at`, `imported_at`, точность варианта и тип события. Получение денег из почты не обязательно совпадает с моментом продажи. Возврат своего товара не является покупкой; движение между своими персонажами не является выручкой. Нет разрешения включать premium или обращаться к поддержке от имени пользователя.

## M08. Blizzard Game Data: официальные контракты

**D/R(document)/B(data).** HTML-портал перенаправляет с `develop.battle.net` на [community.developer.battle.net](https://community.developer.battle.net/documentation/world-of-warcraft/game-data-apis). Веб-инструмент видит оболочку. Реальное открытое содержимое, используемое сайтом: [JSON справочника](https://community.developer.battle.net/api/pages/content/documentation/world-of-warcraft/game-data-apis.json), получен HTTP 200 и разобран `resources[].methods[]`.

| GET path | Namespace для EU | Назначение |
|---|---|---|
| `/data/wow/realm/index` | `dynamic-eu` | Миры |
| `/data/wow/realm/{realmSlug}` | `dynamic-eu` | Мир |
| `/data/wow/connected-realm/index` | `dynamic-eu` | Группы |
| `/data/wow/connected-realm/{connectedRealmId}` | `dynamic-eu` | Группа |
| `/data/wow/connected-realm/{connectedRealmId}/auctions` | `dynamic-eu` | Серверные предложения |
| `/data/wow/auctions/commodities` | `dynamic-eu` | Региональные предложения |
| `/data/wow/profession/index` | `static-eu` | Профессии |
| `/data/wow/profession/{professionId}` | `static-eu` | Профессия |
| `/data/wow/profession/{professionId}/skill-tier/{skillTierId}` | `static-eu` | Уровень профессии |
| `/data/wow/recipe/{recipeId}` | `static-eu` | Рецепт |
| `/data/wow/item/{itemId}` | `static-eu` | Предмет |

Документация предупреждает: период аукционных данных первоначально часовой, может измениться; ответ способен превысить 10 MB. GET commodities без Bearer дал **404/пустое тело** 25 октября 23:58:33 UTC; это не опровержение документированного endpoint. Авторизованных ответов, полноты каталога, текущих полей recipe и connected ID нет. Исторический endpoint аукциона этим контрактом не установлен.

## M09. Blizzard OAuth и ограничения нагрузки

**D.** Прочитаны HTTP 200 [Using OAuth JSON](https://community.developer.battle.net/api/pages/content/documentation/guides/using-oauth.json) и [Getting started JSON](https://community.developer.battle.net/api/pages/content/documentation/guides/getting-started.json); человекочитаемые [OAuth](https://community.developer.battle.net/documentation/guides/using-oauth), [Getting started](https://community.developer.battle.net/documentation/guides/getting-started).

Для публичных Game Data — OAuth client credentials, token URL `https://oauth.battle.net/token`; client ID/secret выдаёт портал. Данные запрашиваются с Bearer header. Документированы 24 часа жизни токена, но потребитель должен учитывать фактический `expires_in` и отказ авторизации. Для личных защищённых профилей нужен иной пользовательский flow; он не требуется для публичного аукциона. Объявленные ограничения: 36000 запросов/час, 100/сек; превышение секундного ограничения — 429. Квоты не являются целью частоты опроса.

**H.** Адаптер: кеш token в памяти/защищённом хранилище; headers редактируются при логировании; bounded retry с jitter, поддержка `Retry-After`, условные запросы только после реальной проверки ETag/Last-Modified/304; одно обновление token при 401. Секреты во frontend или query string запрещены. Полную загрузку большинства EU рынков по умолчанию не включать: пользователь выбирает scope, все пропуски покрытия видимы.

## M10. Лицензии и противоречие хранения

**D, трактовка B.** [Blizzard Developer API Terms](https://www.blizzard.com/ko-kr/legal/a2989b50-5f16-43b1-abec-2ae17cc09dd6/blizzard-developer-api-terms-of-use) (английский текст доступен в поисковом чтении; прямое открытие некоторых локалей 403), [та же норма, испанская локаль](https://www.blizzard.com/es-es/legal/a2989b50-5f16-43b1-abec-2ae17cc09dd6/condiciones-de-uso-de-las-api-para-desarrolladores-de-blizzard): обязательны конфиденциальность ключа, атрибуция, ограничения использования; нельзя обходить квоты сторонними сервисами. Пункт про TTL буквально охватывает все полученные API Data и ограничивает хранение 30 днями. [Официальный Data Protection FAQ](https://us.forums.blizzard.com/en/blizzard/t/data-protection-notice-and-faq/609) объясняет правило применительно к player data. Эти тексты нельзя молча считать разрешением вечной истории анонимных аукционов.

**H, необходимое решение до реализации долгой истории:** реестр `SourcePolicy` с областью данных, разрешённым использованием, сроком, проверкой/удалением, разрешением производных данных, доказательством условий. Для необратимых удалений сначала политика и backup-процесс, а не очистка в этапе 0. До уточнения исключения нельзя обещать бессрочное raw replay Blizzard. Хеш доказывает идентичность, но не воспроизводит удалённые факторы. Архитектура должна прямо показывать `replay_unavailable_due_to_retention`.

[TSM Terms](https://tradeskillmaster.com/terms) оставляют права на содержимое владельцу и не гарантируют точность/доступность; наличие публичного CSV не даёт найденной явной лицензии на массовую перепубликацию. Документированный импорт в локальные инструменты — основание кандидата для личного проекта, но распространение, облачная передача полного набора и длительное архивирование требуют отдельного уточнения. Для Undermine аналогично различать доступ по API и права на данные; Apache-лицензия Shatari относится к коду.

## M11. Патч и применимость во времени

**D; локальный build B в этом исследовании.** [Blizzard Customer Support: Midnight 12.1.5 — Info & Known Issues](https://us.forums.blizzard.com/en/wow/t/midnight-1215-info-known-issues/2377638), сообщение Vrakthris от 13 октября 2026, подтверждает опубликованный контекст 12.1.5. Это не удостоверяет установленный клиент пользователя, build конкретного снимка или применение каждой механики на EU. Локальный build должен быть установлен по `.build.info`/сохранённому `GetBuildInfo`, а источник механики иметь `valid_from`, `valid_to`, `last_verified_build` и регион. Статическая метка TOC аддона не равна build работающего клиента. Найденная статья hotfixes 12.1 с датой 6 октября не отменяет более поздний 12.1.5.

## M12. Воспроизведение выполненных проб

PowerShell 7, только публичные GET. Для CSV нет ключей. Сетевой доступ обычного shell первоначально отвергался средой до HTTP; после разрешённого сетевого чтения получены результаты M02/M05/M08. Отказ среды не является отказом поставщика.

```powershell
$urls = @(
  'https://public-data.tradeskillmaster.com/retail/eu/commodities.csv',
  'https://public-data.tradeskillmaster.com/retail/eu/realm/soulflayer/items.csv',
  'https://public-data.tradeskillmaster.com/retail/eu/region/items.csv'
)
foreach ($u in $urls) {
  $r = Invoke-WebRequest -Uri $u -TimeoutSec 30 -SkipHttpErrorCheck
  if ([int]$r.StatusCode -ne 200) {
    [pscustomobject]@{ Url=$u; Status=[int]$r.StatusCode }
    continue
  }
  $rows = $r.Content | ConvertFrom-Csv
  [pscustomobject]@{
    Url=$u; Status=[int]$r.StatusCode; Rows=$rows.Count
    Header=($rows[0].PSObject.Properties.Name -join ',')
    ScanTimes=($rows.updatedAt | Sort-Object -Unique) -join ','
    LastModified=$r.Headers['Last-Modified']; ETag=$r.Headers.ETag
    Sha256Utf8=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
      [Text.Encoding]::UTF8.GetBytes($r.Content)))
  }
}
$docsUri = 'https://community.developer.battle.net/api/pages/content/documentation/world-of-warcraft/game-data-apis.json'
$doc = Invoke-RestMethod -Uri $docsUri -TimeoutSec 30
$doc.resources | Where-Object {
  $_.name -match 'Auction House|Connected Realm|Profession|Realm API|Item API'
} | ForEach-Object { $_.methods | Select-Object name,path,httpMethod }
```

Для transport-only проб выполнялись `Invoke-WebRequest -SkipHttpErrorCheck` к URL из M02 и M08 **без Authorization**; фиксировались статус, Content-Type, размер, HTTP Date. Повторять 401/404 бесконечно не нужно. Следующая проверка требует пользовательских credentials, введённых локально в защищённую конфигурацию, а не отправленных в чат.

## Выбор источников и критерии следующей проверки

Это проектные рекомендации, не реализация и не разрешение на этап 1/2.

1. **Blizzard — кандидат первичных снимков и идентичности рынков.** Обоснование: собственный источник, документированный full auction scope; но пока нет авторизованного smoke-test и разрешённой политики долгого хранения. Не заменяет историю и не доказывает сделки.
2. **Undermine — кандидат истории и экономного выборочного depth.** Обоснование: опубликован специальный контракт, но пока проверена только auth boundary. Держать адаптер сменяемым; не парсить внутренние бинарные файлы сайта вместо публичного API без необходимости.
3. **TSM CSV — реально доступный fallback статистик сейчас.** Ограничение: одна скалярная строка не является книгой предложений; fallback обязан снижать capability, а не обещать исполнение. `TSM_LocalAccounting` и `TSM_PublicPrices` — разные адаптеры и права доступа.
4. **Мониторинг отдельно от WoW.** По новым fingerprint/snapshot пересчитывать Python-модель. Опрос старого файла не создаёт нового наблюдения. При включении ПК после перерыва фиксировать gap; история, полученная позже, не становится «известной в прошлом» автоматически.
5. **Первичное наполнение преобразований:** обход документированного profession/skill-tier каталога — проверяемый кандидат, не подтверждение полноты всех дополнений. Текущие response fields, recipe-ID/spell-ID связи, многовариантные выходы, reagents/quality и непрофессиональные механики требуют отдельных источников и cross-check; не выдавать старые JSON-примеры за live data.

| Необходимая проверка | Реальный вход | Ожидаемый результат | Что не заменяет её |
|---|---|---|---|
| UE auth/schema | Локально введённый API key, 1 free static + 1 commodity now + 1 realm now/history | 200, проверка clocks/quantity/depth, права free/paid, quota costs | HTML документация; 401 |
| Blizzard auth/market identity | Локальный client ID/secret | OAuth→realm Soulflayer→connected group→auctions и commodities; сохранены schema fingerprints | TSM slug; hardcoded числовой ID |
| Blizzard static recipe | Один старый и один современный tier/recipe | Реальные входы/выходы/границы качества; mismatch отмечен | Пример из документа 2021 года |
| Temporal test | Повторные ответы в течение нескольких обновлений | Раздельные clocks, дедуп по содержимому, измеренная cadence/gaps | Частый polling одного CSV |
| Source degradation | 401/403/429/5xx, HTML вместо CSV, устаревший файл | Backoff, сохранение последнего валидного, флаг missing capability | Замена цены нулём |
| Cross-source compare | Один scope + вариант + общий upstream time | Отличия explained by semantics; общий upstream не удваивает confidence | Сравнение DBMarket с minBuyout как равных метрик |
| Rights/retention | Подтверждённые действующие условия или ответ владельца | Записанная политика raw/derived/history/cloud/redistribution | Открытый URL, отсутствие robots запрета, Apache кода |

Внешние вопросы для пользователя/следующего этапа: готовность локально предоставить Blizzard credentials и UE key; необходимость paid-вариантов UE; разрешённый набор передаваемых облачному ИИ производных данных; срок истории после уточнения прав. Покупки, регистрацию, переписку с владельцами и платные вызовы этот этап не выполнял.
