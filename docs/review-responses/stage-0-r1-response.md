# Ответ разработчика на аудит этапа 0, R1

Дата: 26.10.2026, Europe/Moscow. **Ревизия разработчика R2 подготовлена к повторной проверке.** Исходный вердикт аудитора — **НЕ ПРИНЯТ**; разработчик не меняет его на «принят» и не закрывает замечания от имени аудитора. Все пять недостатков спецификации признаны. Этап1 не начат; разрешение пользователя на этот запуск — исправить этап0.

## 1. Идентичность и сохранение истории

- Аудит: [docs/reviews/stage-0-review.md](../reviews/stage-0-review.md), SHA-256 `0d014ecac0612cf3d99951eb5bb5e8d4fcc1e8e3939cb32d8652c4466272cc7b`. Прочитан целиком; файл не редактировался.
- HEAD остаётся `13be8666b92e9dd17ee11f614025a04662e9ed68`; untracked документы идентифицируются manifest, а не одним HEAD. Новый коммит не создавался.
- До исправлений проверены22/22 исходных hashes. [Архив R1](../research/baselines/stage-0-r1.zip) содержит эти22 файла и исходный manifest, без игровых SV/секретов. SHA-256 архива `e1ebea9524cbca41746bdc35db12c20f0fa16d034642c9f601a657d0947b8bba`.
- Исходный manifest внутри архива: SHA-256 `e713ab3d96ea24a1796fe52e2e21491e10f1ca67e8f5737a80613f33ce4d8cfe`. Текущий [package-manifest.sha256](../research/package-manifest.sha256) относится к **R2**, поэтому старые hashes R1 проверяются по архиву, а не по изменённым текущим файлам.
- Аудиторские файлы `docs/reviews/` не включены в изменяемый manifest разработчика; входной аудит закреплён отдельным hash выше. Повторный отчёт аудитора может обновить его собственный файл без ложного изменения пакета разработчика.

## 2. Ответ по каждому замечанию

| ID / исходный приоритет | Что исправлено и почему | Доказательство / место | Будущий различающий тест | Статус разработчика |
|---|---|---|---|---|
| S0-01 high | Вместо общего обещания curated records задан полный обход конкретных конечных корпусов: pinned ATT standard+dependencies и реальные ItemXItemEffect/ItemEffect; источники/поля/права/границы/fallback по каждой семье и всем12eras. Sample не завершение импорта; metadata enumeration, semantic validation и current-game coverage разделены | [D2-01–16, P1–P10, §§3–6](../research/transformation-discovery-r2.md); [обновлённый CraftSim/coverage план](../research/craft-transformations.md); sources/data-model/plan | T3-02 требует terminal disposition каждой entry/страницы, totals, полный scope и resume. T3-09/отрицательные D2 случаи ловят пропуски старых eras, truncated tree, _cost/sym, removed season, комментарии и filtered DB2 | Исправление представлено; внешние gaps названы; ожидает повторной проверки |
| S0-02 high | Неизменяемая версия отделена от ActivationEpisode; post-promotion probation/monitor/stop, authority/recovery и current revalidation. Откат не отменяет портфель; open lots/origin/deposits/committed events сохраняются, unknown in-flight не освобождается. Нет безопасной версии→no_new_risk | [SL2-01–09](../research/strategy-lifecycle-r2.md); architecture §7, data-model новые сущности, economics §5, Q11 | T6-06/10/11 и LC2-01–12: успех истории+shadow, затем ухудшение; stale fallback; epoch race/crash; позиции/резервы; нет выдуманной ликвидации | Исправление представлено; runtime не реализован; ожидает проверки |
| S0-03 high | Пять evaluation modes. Новая современная LLM на прошлом — exploratory/unverified и исключена из валидного promotion evidence/metrics. Exact stored replay не вызывает модель и наследует grade. Predictor/auditor memory/RAG/tools изолированы; сохраняется visible IO/provenance | [T2-C01/C05–07, T2-S01](../research/temporal-evaluation-r2.md); architecture §6, data-model, economics §4 | T5-09–11 и T2-A06–11/A13: leaky stub при чистых explicit inputs, prompt не помогает, заражённый audit cache, alias drift и commit после outcome | Исправление представлено; фактическая чистота/эффективность модели не утверждается |
| S0-04 medium | Необъяснённое отклонение→новые наблюдения/измеримая связь→FactorCandidate/novelty→temporal eligibility→held-out/future experiment. Registry расширяемый. Слепой oracle не раскрыт исследователю; positive и withheld arms, раздельная оценка | [F2-01–04](../research/factor-discovery-r2.md); architecture §7, data-model новые сущности, economics §5 | T6-09: закрытый enum/переименование/always-unknown не проходят evidence-sufficient arm; отсутствие evidence требует unknown без main change | Исправление представлено; слепой агентный runtime тест PLAN |
| S0-05 medium | Фактическое available_at неизменно; публичная реконструкция основана на отдельном claim точной ревизии, upper bounds/latency/dependencies/конце агрегата. Unknown не event time и не нулевой лаг. Daily UE не доступен утром | [T2-C02–04, T2-S02/03](../research/temporal-evaluation-r2.md); data-model времена/PublicationAvailabilityClaim, sources, architecture §5 | T5-02/08, T2-A01–05/A12/A14: actual300/public105/decision110; неизвестная публикация; daily max/лаг; late revision; dependency time | Исправление представлено; source publication gaps отдельно; ожидает проверки |

Ни одна правка не создаёт автоматическую приёмку S0-01–05: независимый проверяющий должен оценить новые контракты и повторить доступные пробы.

## 3. Дополнительное исследование S0-01

Реально получены: полный индекс закреплённого ATT standard tree (`truncated=false`,2722 entries,1874 Lua blobs вне конфигурации), небольшие тематические Lua-файлы и две CSV с60644/62149 строками. Десять адресных проб охватывают старый и современный контент, отрицательные случаи и точный scrapping gap. Это не чтение всего корпуса и не импорт готовых игровых преобразований.

Существенный найденный предел: ATT-файл SpellEffect строится через авторский фильтр типов эффектов, поэтому не является полным каталогом. Попытка чтения самого большого CSV завершилась Transport closed; повторный доступ сам по себе не исправил бы фильтрацию. Полные server loot/совместные PMF/BFA scrapper/Obliterum yields не подтверждены. Pinned GitHub MIT и ARR другой версии на CurseForge не объединены в универсальную лицензию; upstream права проверяются отдельно. Все эти ограничения входят в manifest/blocked branches будущего импорта, не скрываются под PASS.

## 4. Выполненные проверки R2

| Проверка | Фактический результат | Чего не доказывает |
|---|---|---|
| Чтение аудита, исходный manifest, архив R1 | Полный аудит прочитан;22/22 hashes исходного пакета совпали до правок; архив создан и проверяется отдельно | HEAD сам по себе не фиксирует untracked документы |
| Исходный `stage0-checks.md` | Повторный PASS13/13 | Не тест приложения/игры |
| Новый [stage0-r2-checks.md](../research/stage0-r2-checks.md) | PASS18/18 синтетических контрпримеров контрактов | Не работающие temporal broker/lifecycle/agent; LC2/T2-A/F2 tests остаются PLAN |
| Ledger diagnostic | csvExpired/csvCancelled/csvIncome/csvSales/csvBuys/csvExpense — data_rows0 | Нет подтверждения непустых сделок или полной чистой прибыли |
| Auctionator diagnostic | Блок28bytes, price/quantity/time/bidPrice counts0 | Нет подтверждения истории залогов/продаж |
| Новые SV metadata/hash | GX553/BagSync28208/CraftSim269302/TSM123026bytes; hashes совпали со снимками аудитора | Не новый runtime/полный semantic parser;3 персонажа — отдельный вывод аудитора |
| D2 source probes | Tree, реальные CSV counts/joins и адресные исходники проверены; явные B/unknown | Не подтверждены текущие игровые выходы/вероятности/права массового reuse |
| T2 источники | Первичный Look-Ahead-Bench и UE прочитаны | Не воспроизведён benchmark и не исследованы веса конкретной WoW-модели |
| Внутренняя критическая вычитка | Проверены lifecycle/F2/temporal и общая согласованность. Уточнены new_relation без обязательного нового поставщика и полный запрет contaminated promotion metrics | Не независимая внешняя повторная приёмка |
| Итоговая целостность пакета | R2 manifest: 29 файлов — 21 Markdown разработчика, 6 handoff, README и архив R1. Исходные 22 файла и manifest внутри архива проверены по исходным hashes; аудит, handoff и README не изменены. Локальные ссылки разрешаются; проверки пробелов и отсутствия изменений tracked-файлов пройдены | Контрольные суммы подтверждают состав пакета, а не правильность проектных решений |

TSM public market CSV, UE/Blizzard auth boundary из R1 не выдаются за новые R2 пробы. Новые платные API вызовы, игровые действия, реализация приложения/моста и изменение сторонних аддонов не выполнялись.

Повторимые диагностические команды находятся в [старом](../research/stage0-checks.md) и [новом](../research/stage0-r2-checks.md) документах, source probes — в D2 §7, Ledger/Auctionator — в их исследовательских приложениях. Эти Python-блоки перед исполнением можно сверить по R2 manifest; они используют только безопасное чтение/арифметику, не Lua execution.

## 5. Изменённые документы и согласование

- `architecture.md`: открытый поиск факторов, временные режимы/контекстная граница, supervisory lifecycle/пауза.
- `data-model.md`: DiscoveryCorpus/Traversal, PublicationAvailabilityClaim/EvaluationRun/context manifests, ActivationEpisode/MonitoringPolicy/transitions/position management, FactorRegistry/ObservationRequest.
- `sources.md`, `research/craft-transformations.md`: конкретный discovery corpus, source bounds, усиленный полный traversal; публикация daily.
- `research/economics-and-model-audit.md`: исторические режимы, запрет contaminated promotion, новые факторы и поведение после продвижения.
- `implementation-plan.md`, `acceptance-tests.md`: этапы остаются прежними; T3-02 усилен, T3-09/T5-08–12/T6-09–11/T7-08 добавлены, T5-02/T6-06 уточнены.
- `risks.md`, `open-questions.md`: неизвестные права/выходы/время, ухудшение после продвижения, выбор чисел/полномочий отделён от уже определённого механизма.
- `research-evidence.md`, `stage-0-report.md`: R2 evidence/status/history и точки повторной проверки.
- `research/local-addons.md`: отдельная новая фиксация metadata/hash; первоначальные2 персонажа не исправлены задним числом на3.
- Новые приложения: `transformation-discovery-r2.md`, `strategy-lifecycle-r2.md`, `temporal-evaluation-r2.md`, `factor-discovery-r2.md`, `stage0-r2-checks.md`; этот ответ и архив исходного пакета.

Manifest перечисляет точные текущие bytes всех документов разработчика, исходного handoff/README и архива R1. Произвольные новые файлы в `docs/reviews/` принадлежат аудитору и не редактируются разработчиком.

## 6. Нерешённое и передача на повторную проверку

Остались внешние зависимости будущих этапов: авторизованные UE/Blizzard данные/IDs; непустая история Ledger; реальные CraftSim расчёты; полные механики loot/scrap/variant/PMF; права raw/derived/history/cloud/redistribution; публикационные границы источников. Они не объявлены исправленными интеграциями. Выбор чисел MonitoringPolicy, budget и конкретных полномочий фиксируется **до** будущего эксперимента, как Q08–Q13, а не после его результата.

Проверяющему: сверить входной audit hash и неизменность `docs/reviews/`; проверить R2 manifest и исходный архив; оценить все пять строк таблицы ответа и отличимость отрицательных тестов; отдельно проверить source enumeration vs semantic completeness, post-promotion pause/open lots, исключение LLM исторических scores из promotion, новый фактор и границы daily. Затем внести собственный повторный вердикт в `docs/reviews/`, сохранив R1.

**Остановка на этапе0. Этап1 не начинается без отдельного разрешения пользователя.**
