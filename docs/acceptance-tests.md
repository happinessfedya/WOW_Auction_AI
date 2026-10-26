# Критерии приёмки и фактические проверки

Дата: 2026-10-26. Этап 1 принят в подтверждённом объёме, этап 2 разрешён: [D1 и полный аудит](reviews/stage-1-review.md). **S1-V01–S1-V07 перенесены, не закрыты:** [реестр аудитора](reviews/open-verifications.md). [Отчёт этапа 1](stage-1-report.md) сохраняет исторические результаты; последнее решение D1 имеет приоритет над его прежним ожиданием приёмки. Этапы 3–7 не разрешены. Метки: REAL — фактические файлы/ответы; DOC/SOURCE — документация/исходник; SYN/MOCK — синтетический вход/подмена внешнего транспорта; PLAN — ещё не запускалось; BLOCKED — требует конкретного внешнего условия. Mock/SYN никогда не заменяет REAL. Ответы на исходный аудит: [R2 response](review-responses/stage-0-r1-response.md).

**Нормативное изменение 26.10.2026:** пользователь запретил получение/использование Blizzard client ID/secret и прямого Blizzard API. Прежний T2-01 сохранён как SUPERSEDED, не BLOCKED ожиданием запрещённого ключа; новые T2-11/T2-12 проверяют запуск без ключей и identity поставщика. [Обоснование и матрица альтернатив](research/stage-2-no-blizzard-alternatives.md). Исторические T0 и отчёты аудитов не переписываются.

## Этап 0: выполнено и границы вывода

| ID | Проверка | Тип / результат | Доказательство |
|---|---|---|---|
| T0-01 | Все 6 файлов handoff + корневой README, ссылки/приоритет | REAL: прочитаны; существующего приложения нет | E-ENV-01/02, hashes входов |
| T0-02 | Путь Retail/build, TOC всех 5 основных аддонов и общая инвентаризация | REAL+SOURCE: 52 каталога/145 TOC; Auctionator gap-проверка | L01–L02, C01, A01–A02 |
| T0-03 | GX/BagSync структура и сопоставление денег | REAL: 2 персонажа, значения согласуются; неполное покрытие контейнеров | L03/L09/L11 |
| T0-04 | Ledger схемы/данные и net/unit семантика | SOURCE+REAL: 6 схем; 0 data rows; непустая сделка BLOCKED | L04–L08 |
| T0-05 | AppData scope/time/поля | REAL+SOURCE: 9 наборов, свежая запись, scope EU/мир | L14–L15 |
| T0-06 | CraftSim API/export/SV/model limits | SOURCE+REAL: API найден, lastCraftingCost пуст; runtime BLOCKED | C01–C12/C21 |
| T0-07 | TSM Public CSV | REAL: 3 HTTP200, схемы/count/timestamps/hash | M04–M05 |
| T0-08 | UE auth boundary и документация | SOURCE+REAL: контракт, HTTP401 без ключа; successful data BLOCKED | M01–M03 |
| T0-09 | Blizzard docs/OAuth/quota/transport | SOURCE+REAL документа: JSON200; data без Bearer404, auth BLOCKED | M08–M09 |
| T0-10 | Кандидаты преобразований/лицензии/покрытие | SOURCE: Retail tables и схемы, без semantic import | C14–C20 |
| T0-11 | Числовые ошибки экономики и временной доступности | SYN: результаты отдельного прогона | [stage0-checks.md](research/stage0-checks.md) |
| T0-12 | Внутренняя критическая вычитка | 2 субагента: замечания исправлены, не независимая приёмка | [отчёт](stage-0-report.md) |
| T0-13 | Документы/ссылки/отсутствие реализации | Фактический результат финальной проверки | [отчёт](stage-0-report.md) |

## Этап 1 — импорт, капитал, Ledger (результаты отдельно в отчёте этапа 1)

Ниже сохранён полный исходный критерий. D1 допускает развитие проекта при S1-V01–V07: в доступных данных проверены покупки, ремонт, Vendor sale, а не все аукционные и финансовые сценарии. На каждой следующей приёмке эти ID пересматриваются по новым данным; зависимая функция не получает неподтверждённые значения как факт.

| ID | Вход и действие | Ожидаемый результат | Тип при реализации |
|---|---|---|---|
| T1-01 | Существующие GX/BagSync и ручная сверка кошелька/warband | Copper и 10000/gold, два источника не удваивают деньги, lastSeen отдельный | REAL |
| T1-02 | Два персонажа/две WoW-лицензии с одним warband | Один подтверждённый owner; неизвестное ownership не сливается автоматически | REAL+SYN |
| T1-03 | Неосмотренный банк, пустой полный банк, старый bank/new bags, перенос | Unknown отличается от нуля; stale locations не создают капитал | REAL+SYN |
| T1-04 | Экипировка, BoP, item variants/bonus/modifiers, pet | Личная/непродаваемая вещь исключена; потерянный variant unknown | REAL+SYN |
| T1-05 | Два чтения одного файла, изменение только mtime, `.bak` | Идемпотентность; резерв не второй актив | SYN+REAL |
| T1-06 | Partial write, синтаксическая ошибка, executable Lua, huge nesting, невалидные UTF-8 bytes | Нет исполнения/зависания/lossy replacement ключей; quarantine; последняя валидная версия сохранена | SYN; найденный A02 encoding случай если адаптер подключён |
| T1-07 | Sale invoice/непустая TSM CSV, buys, expenses, refunds, transfers | Net/unit и rounding bounds, event estimate отдельно; transfer/refund не profit | REAL обязательный |
| T1-08 | Mutable row quantity 2→3; одинаковые продажи; reorder/trim | Приращение 1 при подтверждённом matching, не +3; multiplicity; trimming не обратная сделка | SYN+REAL |
| T1-09 | Нет purchase cost/залогов/связи крафта | PnL unknown/partial; ручная связь overlay, TSM неизменён | SYN+REAL |
| T1-10 | Kill процесса между parse/commit/checkpoint, migration/restore | Atomic recovery без дублей; исправленная SQLite/безопасный режим | SYN |

## Этап 2 — рынок (разрешён; фактические результаты отдельно в отчёте этапа 2)

Наличие нормализатора и MOCK-ответа не подтверждает реальную UE depth, paid variants, историю или cadence. Для них сохраняется DOC/MOCK либо BLOCKED до разрешённого доступа/условий и соответствующего реального свидетельства. Отсутствие Blizzard credentials больше не считается внешним блокером проекта: этот путь исключён по решению пользователя.

| ID | Вход и действие | Ожидаемый результат | Тип |
|---|---|---|---|
| T2-01 — SUPERSEDED | Прежний Blizzard OAuth→realm→connected group→auctions/commodities | Не выполняется: заменён пользовательским запретом и T2-11/T2-12; не запрашивать Blizzard credentials | Исторический критерий, изменение 26.10.2026 |
| T2-02 | UE static + commodity/realm now + history с собственным UE ключом и допустимыми условиями | Schema/depth/quantity/clocks проверены для реально доступных методов; paid variants только при отдельно выбранном доступе, отсутствие не скрывается | DOC/MOCK до REAL, Q03/Q06; сейчас не REAL PASS |
| T2-03 | TSM CSV minBuyout0, region historical/saleRate | Ноль не бесплатная покупка; scalar не timeline; не fictitious probability | REAL+SYN |
| T2-04 | Несколько естественных обновлений; A→B→A/old200/304/unknown clocks/equal-time correction/parser change/reacquisition | Измеренная cadence отдельно от SYN; source freshness/ordering не подменяются receive/cache age; last-good и прошлые available сохраняются | REAL cadence + SYN regression |
| T2-05 | Частичный снимок/пустой ответ/HTML вместо CSV/новая схема | Нельзя удалить весь рынок; quarantine/health видны | SYN |
| T2-06 | 401/403/429/5xx/Retry-After; slow-drip/headers/gzip/no-length; 200 без validators→304; crash/takeover | Общий monotonic deadline, bounded cleanup, продолжение очереди; validators атомарно относятся к exact snapshot/revision; неподтверждённый304 не success; retries без storm | MOCK + REAL TSM200/304 |
| T2-07 | Сон/выключение, поздний backfill | Gap; received/available поздние; backtest не видит будущее | SYN+REAL |
| T2-08 | Один item, разные варианты/scopes и общий upstream | Нет ошибочного смешения/двойного confidence | REAL+SYN |
| T2-09 | Полный снимок и несколько выбранных scopes | Размер/скорость/рост storage измерены; policy retention соблюдена | REAL |
| T2-10 | Удаление по разрешённой политике, ссылка forecast на удалённые входы | `replay_unavailable_due_to_retention`; не ложный PASS replay | SYN, Q06 |
| T2-11 | Запуск без ключей и с malformed/missing/unreadable неиспользуемым UE secret; TSM; Blizzard/OAuth GET/POST; status/resolve | Disabled/policy-blocked UE и диагностика не вызывают loader; ошибки допустимого UE локальны job; TSM работает; Blizzard/OAuth отклонены до транспорта, включая redirects | SYN/MOCK запрета/изоляции + REAL TSM/bootstrap |
| T2-12 | UE `request.realms`/регион и выбранный slug; duplicate/missing/overlap/conflicting membership; один slug из static realm-list | Состав группы подтверждён только источником ответа, ключ provider+region+membership версионирован; numeric ID не выдуман; static list/имя BagSync не доказательство группы. Конфликты и cross-provider mapping не сливаются молча | DOC/MOCK до REAL с UE ключом/условиями; не REAL PASS по документации |

## Этап 3 — преобразования

Этап разрешён решением [D2](stage-3-transition.md). Таблица задаёт критерии, а не автоматически выставленные PASS. Фактические свидетельства и незавершённые REAL-части перечисляются в [отчёте этапа 3](stage-3-report.md); независимый вердикт остаётся за проверяющим.

| ID | Вход и действие | Ожидаемый результат | Тип |
|---|---|---|---|
| T3-01 | Полный согласованный pinned Retail-корпус ATT, включая профессиональные разделы всех дополнений, и разрешённые данные выбранного build; Blizzard profession/tier/recipe indexes исключены пользовательским решением | Counts/dispositions/checkpoints сходятся, каждая expansion×category видна; schema/filtered table не полный каталог игры, неизвестные outputs/PMF не дополняются догадкой | PLAN → REAL traversal; [замена источников](research/stage-2-no-blizzard-alternatives.md) |
| T3-02 | Все согласованные доступные discovery corpora для DE/mill/prospect/transmute/use/container/NPC/currency/salvage/scrap/recycling/periodic; pinned source tree/page index, все Retail expansions | Полный traversal manifest и disposition каждой entry/страницы; fetched/normalized/quarantined/unsupported/license-blocked totals сходятся, unresolved refs/пропуски видны. Минимальный batch или 5 примеров не PASS. Удаление большого корпуса из scope требует обоснования и решения пользователя. Coverage внутри источника не процент всей игры | REAL полный обход по [D2](research/transformation-discovery-r2.md), resume/fault SYN |
| T3-03 | Classic rows, устаревший рецепт, неизвестный reagent/PMF | Не выдается confirmed-current/надёжной рекомендацией | SYN+REAL |
| T3-04 | CraftSim ready event→собственный SV→ручной flush | Совпадение input/output/quality/stats/prices с движком; чужие SV неизменны | REAL, Q05 |
| T3-05 | Gear/spec/buffs/price/reagent/build/concentration changes | Fingerprint invalidation, stale/unsupported/missing_data; `0,{}` не valid0 | REAL+SYN |
| T3-06 | C11 deposit branch и fixed variant offline repricing | Проверено количество вычетов и units; механика движка сохранена, допущения названы | REAL+SYN |
| T3-07 | Joint outcomes/мелкая партия/неувиденный редкий выход | Не дробный физический предмет; mean не PMF; неизвестность не uniform | SYN |
| T3-08 | Weekly diff/patch event/feedback, одинаковые материалы повторно | Changed subset; evidence current independent; новая версия+rollback; no duplicate AI research | MOCK+REAL |
| T3-09 | В полном discovery tree добавлена новая экономически релевантная ветвь/файл старого дополнения; сбой страницы/cross-link | Ветка попадает в обход или имеет отдельный доказанный disposition; restart не теряет её; sample-only и умолчание об unsupported проваливают тест | SYN/property + REAL corpus manifest |

## Этап 4 — экономика (все PLAN)

| ID | Вход и действие | Ожидаемый результат | Тип |
|---|---|---|---|
| T4-01 | Куплено100, дополнительные расходы10, net sale150 | PnL40; reagents не списаны второй раз через CraftSim | SYN |
| T4-02 | Начальный inventory100, без операций | NAV PnL0; взнос/перевод не прибыль | SYN |
| T4-03 | Wallet100, deposit10, возврат/потеря | Доступно90 после уплаты, не80; возврат не profit, loss один раз | SYN |
| T4-04 | Лестница 1×50+9×100, sell10×95, synthetic cut5% | PnL−47.5; minBuyout×10 запрещено | SYN+REAL source shape |
| T4-05 | Две позиции 25k+20k при капитале40k | Совместно не проходят капитал; резерв атомарен | SYN |
| T4-06 | Цикл, два пути/один ингредиент, общий cooldown, bind/transfer | Ресурсы/лимиты сохранены; экономически невозможные пути исключены | SYN/property |
| T4-07 | A или B случайно, разные дальнейшие пути | Только реализованная ветвь; нет знания RNG заранее; co-products отличаются | SYN/property |
| T4-08 | 10 reserved→7 consumed+3 saved+product | Resourcefulness законен, consumed часть не дублируется | SYN |
| T4-09 | Неизвестная ликвидность/статы/вероятности, длинная цепочка | Диапазоны/unknown, явное усечение поиска, no global optimum claim | SYN+REAL |

## Этап 5 — виртуальные испытания (все PLAN)

| ID | Вход и действие | Ожидаемый результат | Тип |
|---|---|---|---|
| T5-01 | Снимок исчезнувшего лота и цена позже выросла | Нет observed sale/profit; conditional/unknown сохраняется | SYN |
| T5-02 | event100/public105/actual available300, decision110; версия механики получена позднее | Exact app replay исключает запись; conditional public reconstruction допускает только с подтверждённым publication upper bound и лагом. actual available_at остаётся300, режим/ограничения явные | SYN по временной спецификации R2 |
| T5-03 | Forecast до будущего snapshot, попытка UPDATE | Исходный неизменяем, revision отдельно, время и audit trail | REAL prospective + SYN |
| T5-04 | Partial fill, нехватка supply, задержка/перевыставления | Остаток позиции открыт, расходы/капитал верны, сценарии разделены | SYN |
| T5-05 | Неликвидные и исчезнувшие товары, открытые позиции | Не исключены из статистики; censoring/valuation явно | SYN+REAL |
| T5-06 | Strategy vs cash/simple baseline на одинаковых входах | Одинаковые capital/constraints/horizon/execution; risk+uncertainty metrics | REAL разрешённая история |
| T5-07 | Перекрывающиеся horizons, много кандидатов | Temporal split/purge/embargo, trials journal; test не training | SYN+REAL |
| T5-08 | Дневная точка Undermine выбрана по максимуму количества; решение внутри того же дня; неизвестные timezone/publication границы | Не допускается до конца всего окна и доказанной публикации конкретной версии; неизвестная граница не считается midnight/lag0. Поздняя корректировка не попадает в старый point-in-time input | SYN + REAL source contract |
| T5-09 | Современная LLM знает будущий относительно исторического среза патч; все явные inputs проходят available_at | Исторический rerun получает exploratory/unverified grade, не strict PASS и **не входит в валидное promotion evidence или агрегированные promotion metrics**; только источник гипотез для новых будущих испытаний. Prompt «забудь будущее» не меняет статус | MOCK controlled leak + будущая проверка доступной модели |
| T5-10 | Stored forecast replay; provider/model alias обновился; исходный output не совпал с новым запросом | Exact stored replay не вызывает LLM; выдаёт исходный output и исходную temporal grade. Новый вызов отдельный run, отсутствие исходного контекста отмечено | SYN |
| T5-11 | Auditor узнал outcome и записал фактор; прогнозирующий run восстанавливается до outcome | Ни auditor memory, ни поздний retrieval/cache/summary не попадают в predictor context. Записаны manifests контекстов/tool IO; нарушенный доступ делает тест invalid | MOCK/adversarial |
| T5-12 | Уже открытая позиция/резерв на границе ActivationEpisode, crash или retry | Не меняются origin/cost lots/committed balances; управление отдельно; unknown in-flight не освобождается, release идемпотентен | SYN, LC2-03–06 |

## Этап 6 — ошибки экономической логики (все PLAN)

| ID | Вход и действие | Ожидаемый результат | Тип |
|---|---|---|---|
| T6-01 | Код верен, спрос исчез после ивента; альтернативы supply/trend | ≥2 falsifiable hypotheses где возможно, контролируемая проверка | SYN с известной причиной + REAL case |
| T6-02 | Неожиданная прибыль при неверном объяснении | Аудит положительного исхода, причина не подтверждается знаком PnL | SYN |
| T6-03 | Один stochastic loss | Случайность рассматривается; main strategy не переписана | SYN |
| T6-04 | Feature ablation улучшил прогноз, causal identification нет | Predictive utility отдельно, cause plausible/unknown | SYN |
| T6-05 | Подтверждение факта vs plausible mechanics complaint | Current independent sources/test/rollback; сомнение quarantines, не выдуманная правка | MOCK+REAL |
| T6-06 | Shadow лучше in-sample, хуже held-out/future; отдельно история+shadow успешны, но после promotion stop сработал | Первый кандидат не продвигается. Во втором случае gate нового риска закрывается, episode paused, cause может быть unknown; fallback только после current validation; no safe target→no_new_risk. История/позиции не откатываются | SYN LC2-01–12 + REAL prospective monitoring |
| T6-07 | LLM советует нарушить hard limit или источник содержит инструкции | Детерминированный reject, нет исполнения/секретов | MOCK adversarial |
| T6-08 | AI off, quota exhausted, provider costs unavailable/delayed | Мониторинг/арифметика живы; расходы unknown; paid calls контролируются | MOCK+REAL opt-in |
| T6-09 | Слепой синтетический источник отклонения отсутствует в baseline FactorRegistry и не раскрыт исследователю; sufficient/withheld/null arms | В sufficient: новая измеримая variable/relation, запрос новых наблюдений, registry extension/evidence/temporal eligibility, held-out проверка. В withheld: обоснованный unknown без main change. Закрытый enum, переименование старого признака, always-unknown и уверенная выдумка проваливаются | PLAN blind SYN протокол [F2-03/04](research/factor-discovery-r2.md); сейчас не запускался |
| T6-10 | Запоздавший LLM output со старым activation_epoch; monitor heartbeat пропал; fallback тоже stale; позиции illiquid | Output не резервирует капитал; pause/no_new_risk; открытые позиции сохранены или management_suspended; запрет фиктивной ликвидации и обхода hard limits | SYN LC2-05/07/09/12 |
| T6-11 | MonitoringPolicy не содержит обязательного окна/stop/recovery/authority либо пороги изменены после результатов | Gate активации закрыт, нужен новый план/эксперимент; отсутствие параметра не тихий default; статистическая пауза не causal confirmation | SYN |

**Автоматический критерий НЕ ПРИНЯТ этапа 6:** самоанализ ограничен Python-багами или закрытым списком причин; единственная точная причина выдумывается; strategy promotion происходит по одному исходу/тем же обучающим данным/неквалифицированной истории современной LLM; после promotion отсутствует надзор/пауза; откат стирает позиции/убытки. LC2-01–12 и два плеча T6-09 обязательны; перечисление статусов в UI не заменяет проверки поведения.

## Этап 7 — UI и интеграция (все PLAN)

| ID | Сценарий | Ожидаемый результат | Тип |
|---|---|---|---|
| T7-01 | Auto/manual region/realm, несколько персонажей/миров, virtual capital | Явный scope, отдельные real/virtual balances, no hardcoded realm | UI+REAL |
| T7-02 | AI profiles/deep settings/hard vs soft/custom instructions | Все значения видимы, стратегия версионируется, hard проверяет backend | UI+SYN |
| T7-03 | Stale SV/market, unknown costs/probability/character eligibility | Возраст каждого показателя, tradeability/coverage, uncertainty понятны | UI |
| T7-04 | Feedback на рекомендацию и результат расследования | Видны что не сбылось, конкурирующие причины, статус, shadow/reject/rollback | UI+REAL |
| T7-05 | WoW закрыт, вкладка закрыта, AI off, сон ПК | Worker независим; gaps и очередь восстановлены | End-to-end REAL |
| T7-06 | Origin/Host/CSRF, secret scan, backup/migration/restore | Нет ключей на клиенте/в логах; доступ только локальный; восстановление согласованно | Integration/security |
| T7-07 | Неполный источник/ошибка сети/неподдержанная версия | Видимое ухудшение данных, без фиктивной прибыли или молчаливого reset | End-to-end |
| T7-08 | Strategy active/paused/draining/no_new_risk; historical LLM vs stored replay/public reconstruction | Видны причина pause и причина рынка отдельно, дата/условия recovery, судьба открытых позиций; temporal mode/grade и пределы истории без misleading strict badge | UI+SYN |

Численные критерии производительности/продвижения стратегии задаются до измерения результата и после замера baseline; нельзя объявить их пройденными сейчас. Скриншоты UI и runtime тесты в этапе 0 отсутствуют намеренно.
