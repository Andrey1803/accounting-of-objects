"""
Автозаполнение паспорта обследования артезианской скважины (DOCX) и экспорт в PDF.
Шаблон: static/templates/well_passport_template.docx
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from docx import Document

from well_inspection_act import (
    PdfConversionError,
    _debit_from_measure,
    _debit_from_pump,
    _num,
    _set_paragraph_text,
    convert_docx_to_pdf,
    fmt_num_comma,
    format_duration_ru,
    parse_measured_at,
    survey_row_for_act,
)

_BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = _BASE_DIR / 'static' / 'templates' / 'well_passport_template.docx'

DEFAULT_EXECUTOR = os.environ.get(
    'WELL_PASSPORT_EXECUTOR',
    os.environ.get('WELL_ACT_EXECUTOR', 'Индивидуальный предприниматель Емельянов А.Н.'),
)


def _field_line(label: str, value: str, min_pad: int = 44) -> str:
    v = (value or '').strip()
    if v:
        return f'{label}{v}'
    pad = max(12, min_pad - len(label))
    return f'{label}{"_" * pad}'


def parse_address_parts(address: str) -> dict[str, str]:
    """Разбить адрес по запятым: область, район, н.п., улица, дом."""
    keys = ('region', 'district', 'settlement', 'street', 'house')
    out = {k: '' for k in keys}
    if not address or not str(address).strip():
        return out
    chunks = [c.strip() for c in re.split(r'[,;]', str(address)) if c.strip()]
    for i, ch in enumerate(chunks[:5]):
        out[keys[i]] = ch
    if len(chunks) == 1:
        out['settlement'] = chunks[0]
        out['region'] = ''
    return out


def _split_recommendations(text: str, lines: int = 3) -> list[str]:
    raw = (text or '').strip()
    if not raw:
        return [''] * lines
    parts = [p.strip() for p in re.split(r'[\r\n]+', raw) if p.strip()]
    if len(parts) == 1 and len(parts[0]) > 120:
        words = parts[0].split()
        parts = []
        chunk: list[str] = []
        for w in words:
            chunk.append(w)
            if len(' '.join(chunk)) > 70:
                parts.append(' '.join(chunk))
                chunk = []
        if chunk:
            parts.append(' '.join(chunk))
    while len(parts) < lines:
        parts.append('')
    return parts[:lines]


def build_passport_context(
    *,
    object_row: dict,
    client_row: Optional[dict],
    survey_row: Optional[dict],
    overrides: Optional[dict] = None,
    inline_inputs: Optional[dict] = None,
    inline_computed: Optional[dict] = None,
    inline_conclusion: Optional[str] = None,
) -> dict[str, Any]:
    overrides = overrides or {}
    inputs: dict = {}
    computed: dict = {}
    conclusion = ''

    if survey_row:
        inputs = dict(survey_row.get('inputs') or {})
        computed = dict(survey_row.get('computed') or {})
        conclusion = (survey_row.get('conclusion') or '').strip()
    if inline_inputs:
        inputs.update(inline_inputs)
    if inline_computed:
        computed.update(inline_computed)
    if inline_conclusion is not None:
        conclusion = inline_conclusion.strip()

    address = (overrides.get('address') or '').strip()
    if not address and client_row:
        address = (client_row.get('address') or '').strip()
    if not address:
        address = str(object_row.get('name') or '').strip()

    addr_parts = parse_address_parts(address)
    for key in ('region', 'district', 'settlement', 'street', 'house'):
        if overrides.get(key):
            addr_parts[key] = str(overrides[key]).strip()

    static_m = _num(inputs.get('static_m'))
    dynamic_m = _num(inputs.get('dynamic_m'))
    depth_m = _num(inputs.get('well_depth_m'))
    calc_depth_m = _num(inputs.get('calc_depth_m'))
    measure_sec = _num(inputs.get('measure_seconds'))

    debit_dyn = _num(computed.get('debit_m3h_dynamic'))
    if debit_dyn is None:
        debit_dyn = _debit_from_measure(inputs)

    pump_mark = (overrides.get('pump_mark') or '').strip()
    pipe_text = (overrides.get('pipe') or '').strip()
    casing = (overrides.get('casing_diameter') or '').strip()
    if not casing and pipe_text:
        m = re.search(r'ду\s*(\d+)', pipe_text, re.I)
        if m:
            casing = f'{m.group(1)} мм'
        elif '89' in pipe_text:
            casing = '89 мм'

    material = (overrides.get('pipe_material') or '').strip() or 'сталь'
    filter_iv = (overrides.get('filter_interval') or '').strip()
    sump_iv = (overrides.get('sump_interval') or '').strip()

    executor = (overrides.get('executor') or '').strip() or DEFAULT_EXECUTOR
    rec_lines = _split_recommendations(
        (overrides.get('conclusion') or '').strip() or conclusion, 3
    )

    depth_s = f'{fmt_num_comma(depth_m)}м' if depth_m is not None else ''
    static_s = f'{fmt_num_comma(static_m)}м' if static_m is not None else ''
    dynamic_s = f'{fmt_num_comma(dynamic_m)}м' if dynamic_m is not None else ''
    calc_s = f'{fmt_num_comma(calc_depth_m, 0)}м' if calc_depth_m is not None else ''
    debit_s = f'{fmt_num_comma(debit_dyn, 1)} м3/ч' if debit_dyn is not None else ''
    duration = format_duration_ru(measure_sec)

    pump_display = pump_mark
    if not pump_display:
        p_m3h = _num(inputs.get('pump_m3h'))
        if p_m3h is not None:
            pump_display = f'{fmt_num_comma(p_m3h)} м³/ч'

    return {
        'lines': {
            4: _field_line('Область:', addr_parts['region']),
            5: _field_line('Район:', addr_parts['district']),
            6: _field_line('Нас. Пункт:', addr_parts['settlement']),
            7: _field_line('Улица:', addr_parts['street']),
            8: _field_line('Дом (номер участка):', addr_parts['house']),
            10: _field_line('Диаметр обсадной трубы:', casing),
            11: _field_line('Труба изготовлена из материала:', material),
            12: _field_line('Общая глубина скважины:', depth_s),
            13: _field_line('Интервал установки фильтра:', filter_iv),
            14: _field_line('Интервал установки отстойника:', sump_iv),
            16: _field_line('Статический уровень:', static_s),
            17: _field_line('Динамический уровень:', dynamic_s),
            18: _field_line('Откачка производилась насосам:', pump_display),
            19: _field_line('в течении:', duration),
            20: _field_line('с глубины:', calc_s),
            21: _field_line('с производительностью:', debit_s),
            23: pump_display or '_' * 52,
            25: calc_s or '_' * 52,
            27: rec_lines[0] or '_' * 52,
            28: rec_lines[1] or '_' * 52,
            29: rec_lines[2] or '_' * 52,
            32: executor,
        },
    }


def fill_passport_docx(context: dict[str, Any], dest_path: Path) -> Path:
    if not TEMPLATE_PATH.is_file():
        raise FileNotFoundError(f'Шаблон паспорта не найден: {TEMPLATE_PATH}')
    doc = Document(str(TEMPLATE_PATH))
    paras = doc.paragraphs
    for idx, text in context.get('lines', {}).items():
        if idx < len(paras):
            _set_paragraph_text(paras[idx], text)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest_path))
    return dest_path


def generate_passport_files(
    context: dict[str, Any],
    *,
    want_pdf: bool = True,
    basename: str = 'pasport_skvazhiny',
) -> tuple[Path, Optional[Path]]:
    tmp = Path(tempfile.mkdtemp(prefix='well_passport_'))
    safe = re.sub(r'[^\w\-]+', '_', basename, flags=re.UNICODE)[:80].strip('_') or 'pasport'
    docx_path = tmp / f'{safe}.docx'
    fill_passport_docx(context, docx_path)
    pdf_path = None
    if want_pdf:
        pdf_path = tmp / f'{safe}.pdf'
        try:
            convert_docx_to_pdf(docx_path, pdf_path)
        except PdfConversionError:
            pdf_path = None
    return docx_path, pdf_path
