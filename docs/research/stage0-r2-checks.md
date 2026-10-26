# Диагностика контрактов R2 — без приложения

26.10.2026. Одноразовая проверка контрпримеров S0-01–S0-05 и арифметических инвариантов. Код ниже — **иллюстрация правил спецификации**, не реализация импортёра, replay engine, supervisor, агента или модели исполнения. Он не вызывает сеть/LLM/WoW, не меняет файлы. Реальные будущие тесты LC2-*, T2-A* и слепой F2-03 остаются PLAN; эта диагностика их не заменяет.

Отличаемые неверные трактовки: sample=batch completion; event time=public time; перенос actual available назад; final daily max известен утром; prompt очищает веса LLM; восстановление баланса при rollback; освобождение неизвестного обязательства; закрытый список факторов/always unknown достаточны. Числа — синтетические, не утверждённые лимиты пользователя.

```python
from copy import deepcopy
checks = []
def check(name, value):
    assert value, name
    checks.append(name)

# Source traversal: metadata enumeration is not semantic import.
corpus = {'old-container', 'new-vendor', 'special-recycling'}
smoke_seen = {'old-container'}
dispositions = {'old-container': 'candidate', 'new-vendor': 'license_blocked', 'special-recycling': 'unsupported'}
check('sample_does_not_complete_corpus', smoke_seen != corpus)
check('full_disposition_does_not_mean_validated', set(dispositions) == corpus and 'validated' not in dispositions.values())

# Formula T2-C03: an isolated arithmetic illustration, not application code.
def ready_upper(public_upper, dependency_upper, latency_upper, *, final=False, period_end=None):
    if None in (public_upper, dependency_upper, latency_upper):
        return None
    if final and period_end is None:
        return None
    bounds = [public_upper, dependency_upper]
    if final:
        bounds.append(period_end)
    return max(bounds) + latency_upper
row = {'event_at': 100, 'public_upper': 105, 'available_at': 300}
before = deepcopy(row)
check('actual_late_backfill_excluded', not row['available_at'] <= 110)
check('public_reconstruction_separate', ready_upper(row['public_upper'], 105, 0) <= 110)
check('actual_timestamp_not_rewritten', row == before and row['available_at'] == 300)
check('event_time_cannot_supply_missing_publication', ready_upper(None, 100, 0) is None)
check('unknown_daily_boundary_excluded', ready_upper(87000, 43200, 30, final=True) is None)
daily = ready_upper(87000, 43200, 30, final=True, period_end=86400)
check('daily_not_known_intraday', 43200 <= 60000 and not daily <= 60000)
check('publication_processing_lag_matters', not daily <= 87020 and daily <= 87030)
check('derived_factor_waits_for_last_dependency', ready_upper(105, 125, 2) == 127)
check('request_start_not_prospective_commit', 100 < 105 < 111 and not 111 < 105)

# A mode/claim fixture cannot detect actual LLM memorisation.
run = {'mode': 'llm_historical_exploration', 'explicit_inputs_eligible': True,
       'model_knowledge_status': 'unverified', 'apparent_accuracy': 1.0}
check('high_accuracy_does_not_clear_llm_knowledge',
      run['explicit_inputs_eligible'] and run['model_knowledge_status'] == 'unverified'
      and run['mode'] != 'prospective_evaluation')
stored = {'decision': 'original-output', 'temporal_grade': 'unverified'}
replayed = deepcopy(stored)
check('stored_replay_inherits_grade', replayed == stored and replayed['temporal_grade'] != 'clean')

# Switching strategy is not a cashflow or deletion of an open position.
ledger = {'wallet': 90, 'cost_lot': 100, 'deposit': 10, 'pending_unused': 20, 'inflight_unknown': 30}
nav_before = ledger['wallet'] + ledger['cost_lot'] + ledger['deposit']
original_lot = ledger['cost_lot']
ledger['pending_unused'] = 0  # release only the proven unexecuted reservation
nav_after = ledger['wallet'] + ledger['cost_lot'] + ledger['deposit']
check('activation_change_no_nav_gain', nav_before == nav_after == 200 and ledger['cost_lot'] == original_lot)
check('unknown_commitment_not_freed', ledger['wallet'] - ledger['inflight_unknown'] == 60 and ledger['inflight_unknown'] == 30)
old_worker_epoch, current_epoch = 7, 8
check('late_worker_epoch_rejected', old_worker_epoch != current_epoch)
fallback = {'was_successful': True, 'current_validation_passed': False}
check('past_success_not_rollback_permission', fallback['was_successful'] and not fallback['current_validation_passed'])

# Scoring dimensions for the FUTURE blind test, not a simulated discovery.
always_unknown = {'positive_new_factor': False, 'withheld_abstention': True}
closed_list = {'positive_new_factor': False, 'withheld_abstention': False}
check('blind_test_requires_both_arms', not all(always_unknown.values()) and not all(closed_list.values()))
print(f'PASS {len(checks)}/{len(checks)} R2 synthetic contract counterexamples')
for name in checks:
    print('PASS', name)
```

Повторение из корня проекта:

```powershell
$r2Text = Get-Content -LiteralPath 'docs/research/stage0-r2-checks.md' -Raw
$r2Block = [regex]::Match($r2Text, '(?s)```python\r?\n(.*?)\r?\n```')
if (-not $r2Block.Success) { throw 'R2 diagnostic block not found' }
$r2Block.Groups[1].Value | python -
```

Фактический результат отдельного запуска — в [ответе разработчика R2](../review-responses/stage-0-r1-response.md). Проверка не подтверждает, что какая-либо современная LLM лишена утечки или уже способна открыть скрытый фактор, и не проверяет атомарность будущей БД. Контрпримеры нужны для проверки смысла спецификации до её реализации.
