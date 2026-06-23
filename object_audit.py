"""Журнал изменений объектов учёта."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from database import execute, fetch_all

OBJECT_AUDIT_FIELDS: dict[str, str] = {
    "name": "Название",
    "status": "Статус",
    "client": "Клиент",
    "client_id": "Клиент (id)",
    "date_start": "Дата начала",
    "date_end": "Дата окончания",
    "work_dates": "Рабочие дни",
    "sum_work": "Сумма работ",
    "expenses": "Расходы",
    "advance": "Аванс",
    "salary": "Зарплата",
    "notes": "Примечания",
    "is_regular_to": "Регулярное ТО",
    "next_to_date": "Дата след. ТО",
    "next_to_note": "Заметка ТО",
    "salary_allocation_mode": "Распределение зарплаты",
    "settlement_type": "Расчёт",
    "tax_regime": "Налог",
    "integration_source": "Источник интеграции",
}


def _serialize_value(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_object_change(
    user_id: int,
    object_id: int,
    *,
    action: str,
    source: str = "ui",
    changed_by_user_id: int | None = None,
    field_name: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    snapshot_json: str | None = None,
) -> None:
    execute(
        """INSERT INTO object_change_log
        (user_id, object_id, changed_at, changed_by_user_id, source, action,
         field_name, old_value, new_value, snapshot_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            object_id,
            _now_str(),
            changed_by_user_id,
            source,
            action,
            field_name,
            old_value,
            new_value,
            snapshot_json,
        ),
    )


def log_object_create(
    user_id: int,
    object_id: int,
    row: dict[str, Any],
    *,
    source: str = "ui",
    changed_by_user_id: int | None = None,
) -> None:
    log_object_change(
        user_id,
        object_id,
        action="create",
        source=source,
        changed_by_user_id=changed_by_user_id,
        snapshot_json=json.dumps(row, ensure_ascii=False, default=str),
    )


def log_object_delete(
    user_id: int,
    object_id: int,
    row: dict[str, Any],
    *,
    source: str = "ui",
    changed_by_user_id: int | None = None,
) -> None:
    log_object_change(
        user_id,
        object_id,
        action="delete",
        source=source,
        changed_by_user_id=changed_by_user_id,
        snapshot_json=json.dumps(row, ensure_ascii=False, default=str),
    )


def log_object_field_diff(
    user_id: int,
    object_id: int,
    old_row: dict[str, Any],
    new_row: dict[str, Any],
    *,
    source: str = "ui",
    changed_by_user_id: int | None = None,
    fields: tuple[str, ...] | None = None,
) -> None:
    keys = fields or tuple(OBJECT_AUDIT_FIELDS.keys())
    for key in keys:
        old_s = _serialize_value(old_row.get(key))
        new_s = _serialize_value(new_row.get(key))
        if old_s == new_s:
            continue
        log_object_change(
            user_id,
            object_id,
            action="update",
            source=source,
            changed_by_user_id=changed_by_user_id,
            field_name=key,
            old_value=old_s,
            new_value=new_s,
        )


def get_object_change_history(user_id: int, object_id: int, limit: int = 100) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit or 100), 500))
    rows = fetch_all(
        """SELECT l.*, u.username AS changed_by_username
        FROM object_change_log l
        LEFT JOIN users u ON u.id = l.changed_by_user_id
        WHERE l.user_id = ? AND l.object_id = ?
        ORDER BY l.id DESC
        LIMIT ?""",
        (user_id, object_id, lim),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        fn = item.get("field_name")
        if fn:
            item["field_label"] = OBJECT_AUDIT_FIELDS.get(str(fn), str(fn))
        out.append(item)
    return out
