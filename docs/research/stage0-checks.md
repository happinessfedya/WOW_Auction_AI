# Одноразовые проверки проекта расчётов

26.10.2026. Эти проверки выполняют только Python standard library над синтетическими числами. Они демонстрируют контрольные ответы и опасные допущения, **не тестируют несуществующее приложение, парсер, ИИ или исполнение WoW**. Комиссия 5% — параметр примера. Реальные интеграционные проверки перечислены отдельно в [acceptance-tests](../acceptance-tests.md).

Воспроизводимый код (никакой сети/записи файлов):

```python
from decimal import Decimal as D
checks = []
def check(name, condition):
    assert condition, name
    checks.append(name)

check('gold_copper_units', 40000 * 10000 == 400000000)
buy = D(50) + D(9) * D(100)
sell_net = D(10) * D(95) * D('0.95')
check('depth_negative_profit', buy == 950 and sell_net - buy == D('-47.50'))
check('no_double_reagent_cost', D(150) - D(100) - D(10) == 40)
check('initial_stock_no_profit', D(100) - D(100) == 0)
check('contribution_not_profit', D(140) - D(100) - D(40) == 0)
wallet, deposit = D(100), D(0)
wallet, deposit = wallet - 10, deposit + 10
check('deposit_not_deducted_twice', wallet == 90 and wallet + deposit == 100)
wallet, deposit = wallet + deposit, D(0)
check('deposit_refund_not_profit', wallet + deposit - D(100) == 0)
check('exclusive_outcomes_not_coproducts', D('0.5') * 60 + D('0.5') * 20 == 40)
check('resourcefulness_balance', 10 - 7 == 3 and 7 + 3 == 10)
check('portfolio_budget_conflict', 25000 + 20000 > 40000)
check('ledger_net_preserved', D(100) * D('0.95') == 95 and D(95) * D('0.95') != 95)
check('ledger_revision_delta', 3 - 2 == 1)
decision_at = 100
observations = [dict(source_at=90, available_at=95), dict(source_at=80, available_at=110)]
eligible = [o for o in observations if o['available_at'] <= decision_at]
check('late_backfill_excluded', len(eligible) == 1 and eligible[0]['source_at'] == 90)
print(f'PASS {len(checks)}/{len(checks)} synthetic arithmetic/contract checks')
for name in checks:
    print('PASS', name)
```

Фактический результат запуска записывается в [итоговом отчёте](../stage-0-report.md). Успех этих проверок не даёт оснований утверждать подтверждение причинности, вероятностей, реальной доходности или готовность интеграций. В частности, проверка backfill — лишь демонстрация контракта available_at; настоящая защита replay проверяется после реализации этапа 5.
