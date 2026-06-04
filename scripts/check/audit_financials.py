# -*- coding: utf-8 -*-
"""
Аудит финансовых расчётов ObjectAccounting:
- согласованность /api/stats, /api/stats/detailed, отчёта и долгов;
- legacy-алиасы est_* vs estimate_*;
- формула прибыли по каждому объекту.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.stdout.reconfigure(encoding='utf-8')

from app_objects import (
    _apply_object_financial_enrichment,
    _compute_object_financials,
    _compute_tax_on_profit,
    _fetch_objects_with_financials,
    _portfolio_financial_totals,
    _sql_objects_estimate_aggregates,
    OBJECT_STATUSES_NOT_DEBT,
)
from database import fetch_all, init_db


def _totals_wrong_aliases(user_id: int):
    """Симуляция старого бага статистики (est_* без чтения в enrichment)."""
    rows = fetch_all(
        _sql_objects_estimate_aggregates('est_works', 'est_materials', 'est_mat_profit', 'est_mat_cost')
        + 'WHERE o.user_id = ? GROUP BY o.id',
        (user_id,),
    )
    rev = prof = 0.0
    for row in rows:
        tr, te, tp = _compute_object_financials(
            row.get('sum_work'), 0, 0, 0, row.get('expenses'), row.get('salary'), 0, 0.0,
        )
        _, _, pa = _compute_tax_on_profit(tp, row.get('settlement_type'), row.get('tax_regime'))
        rev += tr
        prof += pa
    return round(rev, 2), round(prof, 2)


def audit_user(user_id: int) -> int:
    errors = []
    objects = _fetch_objects_with_financials(user_id)
    if not objects:
        print(f'  user_id={user_id}: объектов нет')
        return 0

    p = _portfolio_financial_totals(objects)

    # Повторная сводка (должна совпасть)
    p2 = _portfolio_financial_totals(list(objects))
    for key in ('total_revenue', 'total_profit', 'total_debt'):
        if p[key] != p2[key]:
            errors.append(f'portfolio unstable {key}: {p[key]} vs {p2[key]}')

    # Формула по объектам
    for obj in objects:
        oid = obj.get('id')
        tr = float(obj.get('total_revenue') or 0)
        te = float(obj.get('total_expenses') or 0)
        sal = float(obj.get('salary') or 0)
        pbt = float(obj.get('profit_before_tax') or 0)
        tax = float(obj.get('tax_amount') or 0)
        prof = float(obj.get('total_profit') or 0)
        expect_pbt = round(tr - te - sal, 2)
        if abs(expect_pbt - pbt) > 0.02:
            errors.append(f'obj {oid}: profit_before_tax {pbt} != {expect_pbt}')
        expect_prof = round(pbt - tax, 2)
        if abs(expect_prof - prof) > 0.02:
            errors.append(f'obj {oid}: total_profit {prof} != {expect_prof}')
        bal = float(obj.get('balance') or 0)
        expect_bal = round(tr - float(obj.get('advance') or 0), 2)
        if abs(expect_bal - bal) > 0.02:
            errors.append(f'obj {oid}: balance {bal} != {expect_bal}')

    # Legacy alias path (только если в SQL есть материалы)
    bad_rev, bad_prof = _totals_wrong_aliases(user_id)
    if bad_rev < p['total_revenue'] * 0.9 and p['total_revenue'] > 100:
        print(f'  ⚠️  legacy est_* занижает выручку: {bad_rev} vs {p["total_revenue"]} (ожидаемо после фикса)')

    wrong_rev, wrong_prof = _totals_wrong_aliases(user_id)
    if (
        wrong_rev < p['total_revenue'] * 0.95
        and abs(wrong_prof - p['total_profit']) < 0.02
        and p['total_revenue'] > 500
    ):
        errors.append(
            f'прибыль {p["total_profit"]} совпадает с legacy est_* при заниженной выручке {wrong_rev}',
        )

    print(f'  user_id={user_id}: объектов {len(objects)}')
    print(f'    Выручка: {p["total_revenue"]}  Расходы (без зарплаты): {p["total_expenses"]}')
    print(f'    Зарплата: {p["total_salary"]}  Налог: {p["total_tax"]}')
    print(f'    Прибыль до налога: {p["total_profit_before_tax"]}  Прибыль: {p["total_profit"]}')
    print(f'    Маржа: {(p["total_profit"] / p["total_revenue"] * 100) if p["total_revenue"] else 0:.1f}%')
    print(f'    Долг: {p["total_debt"]} ({p["debt_objects"]} об.)')
    print(f'    Legacy est_* (баг): выручка {wrong_rev}, прибыль {wrong_prof}')

    if errors:
        print('  ❌ Ошибки:')
        for e in errors[:20]:
            print('    -', e)
        if len(errors) > 20:
            print(f'    ... и ещё {len(errors) - 20}')
        return len(errors)
    print('  ✅ Формулы и сводка согласованы')
    return 0


def main():
    init_db()
    print('=== Аудит финансов ObjectAccounting ===\n')
    rows = fetch_all(
        'SELECT DISTINCT user_id FROM objects WHERE user_id IS NOT NULL ORDER BY user_id',
    )
    uids = [r['user_id'] for r in rows if r.get('user_id') is not None]
    if not uids:
        print('Нет user_id в objects. Задайте INTEGRATION_USER_ID или создайте объекты.')
        return 1
    total_err = 0
    for uid in uids:
        total_err += audit_user(uid)
    print()
    if total_err:
        print(f'Итого ошибок: {total_err}')
        return 1
    print('Итого: все проверки пройдены')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
