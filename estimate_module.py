"""
Модуль для работы со сметами, материалами и работами.
"""
from flask import Blueprint, request, jsonify, render_template, send_file, session
from flask_login import current_user, login_required
from datetime import datetime
from database import fetch_all, fetch_one, execute, execute_rowcount
import logging
import re
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import io

logger = logging.getLogger(__name__)

# --- Импорт позиций из PDF (parse-pdf): фильтры строк и сопоставление с каталогом ---
_PDF_HEADER_START = re.compile(
    r'^(?:\s*№\s|товар|наимен|наименование|работ|услуг|коли|кол-во|количество|ед\.?\s*изм|'
    r'цена|стоимость|срок|артикул|н/п|№п/п|поз\.?)',
    re.I,
)
_PDF_TOTALS_ROW = re.compile(
    r'\b(?:итого|всего|в\s*том\s*числе|в\s*т\.?\s*ч\.?|ндс|сумма\s*ндс|скидк|к\s*оплате|'
    r'прописью|страниц|листов|документ\s+составлен|без\s+ндс)\b',
    re.I,
)
_PDF_STOPWORDS = frozenset({
    'для', 'без', 'или', 'это', 'все', 'всех', 'тип', 'вид', 'как', 'при', 'под', 'над',
    'размер', 'цвет', 'белым', 'белый', 'черным', 'черный', 'серый', 'коричн',
    'штук', 'упак', 'короб', 'масса', 'вес', 'год', 'мес',
})
# Корзина ИМ (akvabreg.by и аналоги): «цена руб. N шт сумма руб.» + «Артикул …»
_PDF_CART_PRICE_LINE = re.compile(
    r'^(\d+(?:[.,]\d+)?)\s*руб\.\s+(\d+(?:[.,]\d+)?)\s*шт\s+(\d+(?:[.,]\d+)?)\s*руб\.?\s*$',
    re.I,
)
_PDF_CART_ARTICLE_LINE = re.compile(r'^Артикул\s+(\d+)\s*$', re.I)


def _pdf_parse_cart_ready_format(full_text):
    """
    PDF «Готовые к заказу»: наименование (в т.ч. несколько строк), строка с ценой/кол-вом/суммой,
    затем «Артикул». Возвращает строки [name, unit_price, qty, total, article] для api_parse_pdf.
    """
    if not full_text or 'руб.' not in full_text.lower():
        return []

    raw_lines = []
    for raw in full_text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        s = ' '.join(raw.split()).strip()
        if not s:
            continue
        low = s.lower()
        if 'image not found' in low or low == 'type unknown':
            continue
        if re.match(r'^--\s*\d+\s+of\s+\d+\s*--$', s, re.I):
            continue
        if low.startswith('готовые к заказу'):
            continue
        raw_lines.append(s)

    def _footer(line):
        lo = line.lower()
        if re.match(r'^итого\s*:', lo):
            return True
        if lo.startswith('интернет-магазин') and len(line) < 90:
            return True
        if line.startswith('http://') or line.startswith('https://'):
            return True
        if re.search(r'^\+\d', line) and re.search(r'\(\d', line):
            return True
        if 'info@' in lo:
            return True
        if re.search(r'ул\.|минская область|щомыслицк', lo) and len(line) > 35:
            return True
        return False

    lines = raw_lines
    n = len(lines)
    out = []
    i = 0
    while i < n:
        line = lines[i]
        if _footer(line):
            break
        if _PDF_CART_PRICE_LINE.match(line):
            i += 1
            continue
        if _PDF_CART_ARTICLE_LINE.match(line):
            i += 1
            continue
        lo = line.lower()
        if lo.startswith('тип цены') or lo.startswith('наличие'):
            i += 1
            continue

        name_parts = []
        start_i = i
        while i < n:
            s = lines[i]
            if _footer(s):
                return out
            if _PDF_CART_PRICE_LINE.match(s):
                break
            if _PDF_CART_ARTICLE_LINE.match(s):
                i += 1
                continue
            lo2 = s.lower()
            if lo2.startswith('тип цены') or lo2.startswith('наличие'):
                i += 1
                continue
            name_parts.append(s)
            i += 1

        if i >= n:
            break
        m = _PDF_CART_PRICE_LINE.match(lines[i])
        if not m or not name_parts:
            i = start_i + 1
            continue

        unit_p, qty, tot = m.group(1), m.group(2), m.group(3)
        i += 1
        while i < n:
            lo3 = lines[i].lower()
            if lo3.startswith('тип цены') or lo3.startswith('наличие'):
                i += 1
                continue
            break

        art = ''
        if i < n and _PDF_CART_ARTICLE_LINE.match(lines[i]):
            art = _PDF_CART_ARTICLE_LINE.match(lines[i]).group(1)
            i += 1

        name = ' '.join(name_parts).strip()
        if name:
            out.append(
                [
                    name,
                    unit_p.replace(',', '.'),
                    qty.replace(',', '.'),
                    tot.replace(',', '.'),
                    art,
                ]
            )
    return out


def _pdf_row_product_title(raw):
    """Выделить наименование из типичной строки прайса (№, цена в начале, шт/м/пог.м, суммы, страна)."""
    s = ' '.join(str(raw).split()).strip()
    if not s:
        return ''
    s = re.sub(r'^\d+\s+', '', s)
    s = re.sub(r'^\d{1,3}(?:\s+\d{3})+[\.,]\d{2}\s+', '', s)
    s = re.sub(r'^\d+[\.,]\d{2}\s+', '', s)
    s = re.sub(r'\s*В\s*наличии.*$', '', s, flags=re.I)
    s = re.sub(r'\s*Под\s*заказ.*$', '', s, flags=re.I)
    s = re.sub(r'\s*\([^)]*уверенность[^)]*\)\s*$', '', s, flags=re.I)
    s = re.sub(r'\s*\([^)]*неоднознач[^)]*\)\s*$', '', s, flags=re.I)

    def _cut(pattern, min_left=12):
        nonlocal s
        m = re.search(pattern, s, re.I)
        if m and m.start() >= min_left:
            s = s[: m.start()].strip()

    _price_tail = r'(?:\d{1,3}(?:\s\d{3})+[\.,]\d{2}|\d+[\.,]\d{2})'
    _cut(rf'\s+\d+(?:[.,]\d+)?\s+(?:шт\.?|шт)\s+{_price_tail}')
    _cut(rf'\s+\d+(?:[.,]\d+)?\s+пог\.?\s*м\s+{_price_tail}')
    _cut(rf'\s+\d+(?:[.,]\d+)?\s+компл\.?\s+{_price_tail}')
    _cut(rf'\s+\d+(?:[.,]\d+)?\s+м\s+{_price_tail}', min_left=18)
    _cut(rf'\s+\d+(?:[.,]\d+)?\s+м\s+{_price_tail}\s+{_price_tail}', min_left=18)

    s = re.sub(rf'\s+{_price_tail}\s+{_price_tail}\s*$', '', s)
    s = re.sub(
        r'[,]?\s*(Республика\s+Польша|Италия|Китай|Беларусь|БЕЛАРУСЬ|Пр-во\s*РБ|Пр-во\s*Рб)\s*$',
        '',
        s,
        flags=re.I,
    )
    return s.strip()


def _pdf_cell_is_unit(cell):
    """Ячейка «ед. изм.» как в печатной смете (шт / м / компл …)."""
    if cell is None:
        return False
    u = str(cell).strip().lower().replace('\xa0', '')
    u_compact = re.sub(r'\s+', '', u).rstrip('.')
    if u_compact in (
        'шт', 'м', 'компл', 'комплект', 'упак', 'кор', 'т', 'тн', 'кг', 'л', 'м2', 'м²',
        'пог.м', 'погм', 'п.м', 'пм',
    ):
        return True
    if u_compact.startswith('компл'):
        return True
    if 'пог' in u_compact and 'м' in u_compact:
        return True
    return False


def _pdf_parse_money_cell(cell):
    """Число из ячейки цены/суммы (запятая, пробелы, без «%» для колонки маржи)."""
    if cell is None:
        return None
    s = str(cell).strip().replace('\xa0', '').replace(' ', '')
    if not s or '%' in s:
        return None
    s = s.replace(',', '.')
    try:
        v = float(s)
    except ValueError:
        return None
    if v != v or v < 0 or v > 1_000_000:
        return None
    return v


def _pdf_table_tail_purchase_and_sum(
    cells, retail_idx, qty_hint=None, list_price=None, for_wholesale=False
):
    """
    После колонки «розница»: закуп, (НДС 1,2 / маржа %), сумма.
    Не считать первое число закупом, если это коэф. НДС или если другое число даёт сумму×кол-во.
    Для опта и сметы «сумма = розница×кол-во»: закуп — не первая ячейка хвоста (часто маржа % без %),
    а число строго меньше розницы (обычно max из подходящих).
    """
    money_vals = []
    for i in range(retail_idx + 1, len(cells)):
        s = str(cells[i] or '')
        if '%' in s:
            continue
        v = _pdf_parse_money_cell(s)
        if v is not None:
            money_vals.append(v)
    if not money_vals:
        return None, None

    s_last = money_vals[-1]
    head = money_vals[:-1] if len(money_vals) >= 2 else []

    try:
        q = float(qty_hint) if qty_hint is not None else 0.0
    except (TypeError, ValueError):
        q = 0.0

    if for_wholesale and list_price is not None and q > 0 and len(money_vals) >= 2:
        try:
            lp = float(list_price)
            sf = float(s_last)
        except (TypeError, ValueError):
            lp = sf = 0.0
        if (
            lp > 0
            and sf > 0
            and _pdf_line_amount_matches_unit_qty(lp, q, sf)
            and head
        ):
            below = []
            for v in head:
                fv = float(v)
                if 1.17 <= fv <= 1.22:
                    continue
                if fv < lp - 1e-5:
                    below.append(fv)
            if below:
                return (max(below), s_last)

    if q > 0 and len(head) >= 1:
        match_vs_sum = []
        for v in head:
            fv = float(v)
            if 1.17 <= fv <= 1.22:
                continue
            if _pdf_line_amount_matches_unit_qty(fv, q, s_last):
                match_vs_sum.append(fv)
        if len(match_vs_sum) == 1:
            return match_vs_sum[0], s_last
        if len(match_vs_sum) > 1:
            if list_price is not None:
                try:
                    lp = float(list_price)
                    below = [x for x in match_vs_sum if x < lp - 1e-6]
                    if len(below) == 1:
                        return below[0], s_last
                    if below:
                        return min(below), s_last
                except (TypeError, ValueError):
                    pass
            return min(match_vs_sum), s_last

    if len(money_vals) >= 2:
        first_f = float(money_vals[0])
        if 1.17 <= first_f <= 1.22:
            if len(money_vals) >= 3:
                return float(money_vals[1]), s_last
            return None, s_last
        return money_vals[0], s_last
    return None, money_vals[0]


def _pdf_line_amount_matches_unit_qty(unit_price, qty, line_sum, rel_tol=0.025):
    """Проверка: цена_за_ед × кол-во ≈ сумма строки (как в счёте/смете)."""
    try:
        u = float(unit_price)
        q = float(qty)
        s = float(line_sum)
    except (TypeError, ValueError):
        return False
    if u <= 0 or q <= 0 or s <= 0:
        return False
    prod = u * q
    tol = max(0.05, rel_tol * s, rel_tol * prod)
    return abs(prod - s) <= tol


def _pdf_resolve_wholesale_unit_table(row, table_layout):
    """
    Цена закупа с НДС за единицу. Сумма в смете часто = qty×розница, поэтому не выбираем закуп
    только по совпадению с суммой. Опираемся на колонку закупа из хвоста (уже без «1,2 НДС»)
    и на то, что закуп < розницы в той же строке.
    """
    qty = _pdf_extract_qty(row, from_cart_pdf=False, table_layout=table_layout)
    if qty is None or qty <= 0:
        qty = 1.0
    ri = table_layout['retail_idx']
    list_col = _pdf_parse_money_cell(row[ri]) if ri < len(row) else None
    p_hint = table_layout.get('purchase_from_tail')
    s_line = table_layout.get('line_sum_from_tail')

    if p_hint and 1.17 <= float(p_hint) <= 1.22:
        p_hint = None

    use_hint = float(p_hint) if p_hint is not None and float(p_hint) > 0 else None
    hint_rejected_low = False

    retail_sum_line = (
        s_line is not None
        and list_col is not None
        and qty > 0
        and _pdf_line_amount_matches_unit_qty(list_col, qty, s_line)
    )

    if use_hint is not None and list_col is not None:
        ph, lp = float(use_hint), float(list_col)
        if ph < lp - 1e-6:
            if lp > 40 and ph < lp * 0.05:
                use_hint = None
                hint_rejected_low = True
            else:
                return round(ph, 4)
        elif ph > lp * 1.35 + 0.01:
            trial = ph / float(qty)
            if 0.01 < trial < lp - 1e-6:
                return round(trial, 4)
            if s_line and s_line > 0:
                per = float(s_line) / float(qty)
                if 0.01 < per < lp - 1e-6:
                    return round(per, 4)
            return round(lp, 4)
    elif use_hint is not None:
        return round(float(use_hint), 4)
    elif list_col is not None and not hint_rejected_low and not retail_sum_line:
        return round(float(list_col), 4)

    if s_line and s_line > 0:
        per = float(s_line) / float(qty)
        if list_col is not None and per < float(list_col) - 1e-6:
            return round(per, 4)
        if list_col is None:
            return round(per, 4)

    if retail_sum_line and list_col is not None:
        lc = float(list_col)
        for j in range(ri + 1, len(row)):
            v = _pdf_parse_money_cell(row[j])
            if v is None:
                continue
            fv = float(v)
            if 1.17 <= fv <= 1.22:
                continue
            if fv < lc - 1e-5:
                return round(fv, 4)
    return None


def _pdf_detect_table_material_row(row, import_mode='retail'):
    """
    Типичная таблица как на скрине сметы: Наименование | Ед. | Кол-во | Розница | Закуп | …
    Либо с ведущим № п/п: № | Наименование | Ед. | Кол-во | Розница | …
    import_mode: для wholesale иначе выбирается колонка закупа при «сумма = розница×qty».
    """
    if not row or len(row) < 4:
        return None
    cells = [str(c or '').strip() for c in row]
    name_idx = unit_idx = qty_idx = retail_idx = None

    if len(cells) >= 5 and re.fullmatch(r'\d{1,4}', cells[0] or '') and _pdf_cell_is_unit(cells[2]):
        name_idx, unit_idx = 1, 2
        qty_idx, retail_idx = 3, 4
    elif _pdf_cell_is_unit(cells[1]):
        name_idx, unit_idx = 0, 1
        qty_idx, retail_idx = 2, 3
    else:
        return None

    name_cell = (cells[name_idx] or '').strip()
    if len(name_cell) < 2:
        return None
    low = name_cell.lower()
    if _PDF_HEADER_START.search(low) or _PDF_TOTALS_ROW.search(low):
        return None

    qty_hint = _pdf_parse_money_cell(cells[qty_idx])
    list_price = _pdf_parse_money_cell(cells[retail_idx])
    purchase_hint, sum_hint = _pdf_table_tail_purchase_and_sum(
        cells,
        retail_idx,
        qty_hint,
        list_price,
        for_wholesale=(str(import_mode).lower() == 'wholesale'),
    )
    return {
        'name_idx': name_idx,
        'unit_idx': unit_idx,
        'qty_idx': qty_idx,
        'retail_idx': retail_idx,
        'purchase_from_tail': purchase_hint,
        'line_sum_from_tail': sum_hint,
    }


def _pdf_unit_from_table_cell(cell):
    """Нормализация ед. изм. для сметы из ячейки таблицы."""
    if not cell:
        return 'шт'
    s = str(cell).strip().lower().replace('\xa0', '')
    if 'пог' in s and 'м' in s:
        return 'м'
    if s.startswith('компл'):
        return 'компл'
    if re.match(r'^м\b', s) or s in ('м.', 'м'):
        return 'м'
    return 'шт'


def _pdf_normalize_for_compare(s):
    """Агрессивная нормализация для сравнения с каталогом (разный пунктуация, дроби)."""
    if not s:
        return ''
    t = str(s).lower().replace('ё', 'е')
    t = re.sub(r'[«»"\'`]', ' ', t)
    t = re.sub(r'[\[\]{}]', ' ', t)
    t = re.sub(r'(\d)\s*\.\s*(\d+)\s*/', r'\1 \2/', t)
    t = re.sub(r'(\d)\s*/\s*(\d+)', r'\1/\2', t)
    t = re.sub(r'(\d)\s*х\s*(\d)', r'\1x\2', t, flags=re.I)
    t = re.sub(r'\s*[-–—]{1,3}\s*', ' ', t)
    t = re.sub(r'\.{2,}', ' ', t)
    t = re.sub(r'[^\w\s/+\-°*]', ' ', t, flags=re.UNICODE)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _pdf_compare_nospace(s):
    """Та же строка без пробелов — «КГ тп» и «КГтп» дают ближе ratio."""
    return re.sub(r'\s+', '', s or '')


def _pdf_normalize_name_for_index(name):
    """Нормализация для индекса каталога: не удаляем все цифры (различаем 600×600 и 300×300)."""
    n = (name or '').lower()
    n = re.sub(r'\([^)]*\)', ' ', n)
    n = re.sub(r'\d+[\.,]\d{2}\b', ' ', n)
    n = re.sub(
        r'\b(китай|россия|испания|италия|германия|турция|польша|евросоюз|беларусь|белоруссия|'
        r'республика\s+польша|пр-во\s*рб)\b',
        ' ',
        n,
        flags=re.I,
    )
    n = re.sub(
        r'\b(в\s*наличии|под\s*заказ|срок|доставка|шт\.?|упак|пог\.?\s*м|пог\.?\s*м\.?)\b',
        ' ',
        n,
        flags=re.I,
    )
    n = re.sub(r'(\d{2,4})\s*[xх]\s*(\d{2,4})', r'\1x\2', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def _pdf_significant_words(text_lower):
    words = re.findall(r'[а-яёa-z0-9][а-яёa-z0-9\-]{1,}', text_lower, flags=re.I)
    out = []
    for w in words:
        wl = w.lower()
        if wl in _PDF_STOPWORDS or len(wl) < 2:
            continue
        if len(wl) == 2 and not re.search(r'\d', wl):
            continue
        out.append(wl)
    return out[:14]


def _pdf_floats_in_order_from_text(s):
    """Числа из строки по порядку (в т.ч. «1 040,18»)."""
    if not s:
        return []
    out = []
    for m in re.finditer(r'(?:\d{1,3}(?:\s+\d{3})+|\d+)(?:[.,]\d+)?', str(s)):
        frag = m.group(0).replace(' ', '').replace(',', '.')
        try:
            v = float(frag)
        except ValueError:
            continue
        if 0.0001 <= v <= 1_000_000:
            out.append(v)
    return out


def _pdf_first_matching_triplet(vals):
    """Тройка (кол-во, цена за ед., сумма) где q×p ≈ t."""
    if len(vals) < 3:
        return None
    for i in range(len(vals) - 2):
        a, b, c = vals[i], vals[i + 1], vals[i + 2]
        if a <= 0 or b <= 0 or c <= 0:
            continue
        prod = a * b
        tol = max(0.015 * abs(c), 0.02 * abs(prod), 0.05)
        if abs(prod - c) <= tol:
            return (a, b, c)
    return None


def _pdf_qty_from_triplet(vals):
    """Первое число из тройки q,p,t где q×p ≈ t."""
    t = _pdf_first_matching_triplet(vals)
    return t[0] if t else None


def _pdf_row_numeric_tail_vals(row):
    """Числа из ячеек строки (кроме первой — часто № или длинное наименование)."""
    ordered = []
    for idx, cell in enumerate(row):
        if idx == 0:
            continue
        s = str(cell or '').strip().replace('\xa0', '').replace(' ', '').replace(',', '.')
        if not s:
            continue
        try:
            v = float(s)
        except ValueError:
            continue
        if 0.05 <= v <= 100000:
            ordered.append((idx, v))
    return [v for _, v in ordered]


def _pdf_retail_price_match(catalog_retail, pdf_price, abs_tol=0.02):
    """Совпадение розничной цены каталога с ценой из PDF (2 знака, допуск по копейкам)."""
    if pdf_price is None:
        return False
    try:
        p = float(pdf_price)
    except (TypeError, ValueError):
        return False
    if p <= 0:
        return False
    try:
        c = float(catalog_retail) if catalog_retail is not None else None
    except (TypeError, ValueError):
        c = None
    if c is None or c <= 0:
        return False
    return abs(round(c, 2) - round(p, 2)) <= abs_tol


def _pdf_purchase_for_file_retail(file_retail, cat_item):
    """Закупка для строки сметы при рознице из PDF: сохраняем отношение закуп/розница из каталога."""
    try:
        fr = float(file_retail)
        cr = float(cat_item.get('retail_price') or 0)
        cp = float(cat_item.get('purchase_price') or 0)
    except (TypeError, ValueError):
        return None
    if fr <= 0:
        return None
    if cr > 0 and cp >= 0:
        return round(fr * (cp / cr), 4)
    return round(fr * 0.7, 2)


def _pdf_try_cart_row_qty_unit_price(row, from_cart_pdf=False):
    """
    Только для строк, собранных из PDF корзины (akvabreg и т.п.): [наименование, цена_за_ед, кол-во, сумма, артикул].
    Для старых табличных PDF (ИТП и др.) не вызывать — там порядок колонок другой, работает тройка q×p≈s.
    """
    if not from_cart_pdf:
        return None, None
    if not row or len(row) != 5:
        return None, None
    name = str(row[0] or '').strip()
    if len(name) < 5:
        return None, None
    try:
        unit_p = float(str(row[1]).replace(',', '.').replace(' ', ''))
        qty = float(str(row[2]).replace(',', '.').replace(' ', ''))
        total = float(str(row[3]).replace(',', '.').replace(' ', ''))
    except (ValueError, TypeError):
        return None, None
    if unit_p <= 0 or qty <= 0 or total <= 0:
        return None, None
    tol = max(0.02, 0.015 * abs(total), 0.02 * abs(unit_p * qty))
    if abs(unit_p * qty - total) > tol:
        return None, None
    art = str(row[4] or '').strip().replace(' ', '')
    if art and not re.fullmatch(r'\d{8,14}', art):
        return None, None
    return qty, unit_p


def _pdf_extract_pdf_retail_unit_price(row, from_cart_pdf=False, table_layout=None):
    """Розничная цена за единицу из строки прайса (колонка «цена», не сумма)."""
    _cq, cpu = _pdf_try_cart_row_qty_unit_price(row, from_cart_pdf)
    if cpu is not None:
        return cpu
    if table_layout is not None:
        ri = table_layout['retail_idx']
        if ri < len(row):
            v = _pdf_parse_money_cell(row[ri])
            if v is not None and v > 0:
                return v
    whole = ' '.join(str(c or '') for c in row)
    vals = _pdf_row_numeric_tail_vals(row)
    t = _pdf_first_matching_triplet(vals)
    if t:
        return t[1]
    t = _pdf_first_matching_triplet(_pdf_floats_in_order_from_text(whole))
    if t:
        return t[1]
    if len(vals) == 2:
        v0, v1 = vals[0], vals[1]
        if 0.01 <= v1 <= 1_000_000:
            return v1
    return None


def _pdf_extract_pdf_unit_price_with_vat(row, from_cart_pdf=False, table_layout=None):
    """
    Цена за единицу с НДС (для оптового счёта).
    Для cart-ready PDF без колонки НДС возвращаем обычную цену за единицу.
    """
    if from_cart_pdf:
        return _pdf_extract_pdf_retail_unit_price(row, from_cart_pdf=True, table_layout=None)
    if table_layout is not None:
        w = _pdf_resolve_wholesale_unit_table(row, table_layout)
        if w is not None:
            return w
        ri = table_layout['retail_idx']
        list_top = _pdf_parse_money_cell(row[ri]) if ri < len(row) else None
        for j in range(ri + 1, min(ri + 5, len(row))):
            v = _pdf_parse_money_cell(row[j])
            if v is None:
                continue
            fv = float(v)
            if 1.17 <= fv <= 1.22:
                continue
            if list_top is not None and fv < float(list_top) - 1e-6:
                return round(fv, 4)
    qty = _pdf_extract_qty(row, from_cart_pdf=False, table_layout=table_layout)
    if qty is None or qty <= 0:
        return None
    whole = ' '.join(str(c or '') for c in row)
    vals = _pdf_floats_in_order_from_text(whole)
    money_vals = [v for v in vals if v > 0 and (abs(v - round(v)) > 1e-9 or v >= 1000)]
    if not money_vals:
        return None
    fq = float(qty)
    for v in reversed(money_vals):
        if 1.17 <= v <= 1.22:
            continue
        unit_price = v / fq
        if 0.0001 <= unit_price <= 1_000_000:
            return round(unit_price, 4)
    return None


def _pdf_extract_unit(row, table_layout=None):
    """Ед. изм. из строки PDF (для сметы), не из карточки каталога."""
    if table_layout is not None:
        ui = table_layout['unit_idx']
        if ui < len(row):
            return _pdf_unit_from_table_cell(row[ui])
    whole = ' '.join(str(c or '') for c in row)
    if re.search(r'\d+(?:[.,]\d+)?\s+пог\.?\s*м\b', whole, re.I):
        return 'м'
    if re.search(r'\d+(?:[.,]\d+)?\s+компл', whole, re.I):
        return 'компл'
    if re.search(
        r'(\d+(?:[.,]\d+)?)\s+м\s+(?=(?:\d{1,3}(?:\s+\d{3})+|\d+)(?:[.,]\d+))',
        whole,
        re.I,
    ):
        return 'м'
    return 'шт'


def _pdf_extract_qty(row, from_cart_pdf=False, table_layout=None):
    """Количество: явные единицы, «N м» перед ценой, тройка qty×price≈total, иначе ячейки."""
    cq, _cpu = _pdf_try_cart_row_qty_unit_price(row, from_cart_pdf)
    if cq is not None:
        return cq
    if table_layout is not None:
        qi = table_layout['qty_idx']
        if qi < len(row):
            v = _pdf_parse_money_cell(row[qi])
            if v is not None and 0.01 <= v <= 100_000:
                return v
    whole = ' '.join(str(c or '') for c in row)

    # Смета/счёт: колонка «Кол-во» идёт ПОСЛЕ «шт/м» — «… шт 1 16.36», иначе «25 шт» из «25x25x25 шт» даёт ложное qty.
    for pat in (
        r'(?:шт\.?|шт)\s+(\d+(?:[.,]\d+)?)\b',
        r'(?:компл\.?|комплект)\s+(\d+(?:[.,]\d+)?)\b',
        r'(?:пог\.?\s*м|п\.м\.?)\s+(\d+(?:[.,]\d+)?)\b',
    ):
        m = None
        for m in re.finditer(pat, whole, re.I):
            pass
        if m:
            try:
                v = float(m.group(1).replace(',', '.').replace(' ', ''))
                if 0.01 <= v <= 100_000:
                    return v
            except ValueError:
                pass

    for pat in (
        r'(\d+(?:[.,]\d+)?)\s*(?:шт\.?|шт)\b',
        r'(\d+(?:[.,]\d+)?)\s+пог\.?\s*м\b',
        r'(\d+(?:[.,]\d+)?)\s+компл\.?\b',
    ):
        m = re.search(pat, whole, re.I)
        if m:
            try:
                v = float(m.group(1).replace(',', '.').replace(' ', ''))
                if 0.01 <= v <= 100_000:
                    return v
            except ValueError:
                pass

    m = re.search(
        r'(\d+(?:[.,]\d+)?)\s+м\s+(?=(?:\d{1,3}(?:\s+\d{3})+|\d+)(?:[.,]\d+))',
        whole,
        re.I,
    )
    if m:
        try:
            v = float(m.group(1).replace(',', '.').replace(' ', ''))
            if 0.01 <= v <= 100_000:
                return v
        except ValueError:
            pass

    vals = _pdf_row_numeric_tail_vals(row)
    q3 = _pdf_qty_from_triplet(vals)
    if q3 is not None:
        return q3

    if len(vals) >= 3:
        q3w = _pdf_qty_from_triplet(_pdf_floats_in_order_from_text(whole))
        if q3w is not None:
            return q3w
        return vals[0]

    if not vals:
        q3w = _pdf_qty_from_triplet(_pdf_floats_in_order_from_text(whole))
        if q3w is not None:
            return q3w
        return 1.0

    if len(vals) == 1:
        return vals[0]

    v0, v1 = vals[0], vals[1]
    if v1 > v0 * 50:
        return v0
    if v0 > v1 * 50:
        return v1
    q3w = _pdf_qty_from_triplet(_pdf_floats_in_order_from_text(whole))
    if q3w is not None:
        return q3w
    return v0


def _pdf_sanitize_qty(qty, retail_price, purchase_price=0):
    """Не подставлять цену/РРЦ в количество (иначе сумма = миллионы)."""
    try:
        q = float(qty)
        rp = float(retail_price or 0)
        pp = float(purchase_price or 0)
    except (TypeError, ValueError):
        return 1.0
    if q <= 0 or q != q:
        return 1.0
    if q > 5000:
        return 1.0
    if rp > 0 and q * rp > 400_000:
        return 1.0
    for ref in (rp, pp):
        if ref > 0 and q >= 20 and abs(q - ref) / ref <= 0.12:
            return 1.0
    return q


def _pdf_catalog_article_map(catalog_items):
    """Индекс артикулов: как в каталоге, без пробелов, только цифры, без ведущих нулей, хвост EAN-8."""
    m = {}
    for item in catalog_items:
        raw = (item.get('article') or '').strip()
        if not raw:
            continue
        variants = set()
        for base in (raw, raw.upper(), raw.replace(' ', ''), raw.upper().replace(' ', '')):
            if base:
                variants.add(base)
        digits = re.sub(r'\D', '', raw)
        if digits:
            variants.add(digits)
            stripped = digits.lstrip('0')
            if stripped and stripped != digits:
                variants.add(stripped)
            if len(digits) >= 8:
                variants.add(digits[-8:])
        for v in variants:
            if v and 2 <= len(str(v)) <= 40:
                m[str(v)] = item
    return m


def _pdf_lookup_article_in_map(code, catalog_by_article):
    """Поиск позиции по коду с теми же вариантами, что и при индексации."""
    if not code:
        return None
    c0 = str(code).strip()
    candidates = [c0, c0.upper(), c0.replace(' ', ''), c0.upper().replace(' ', '')]
    digits = re.sub(r'\D', '', c0)
    if digits:
        candidates.append(digits)
        stripped = digits.lstrip('0')
        if stripped and stripped != digits:
            candidates.append(stripped)
        if len(digits) >= 8:
            candidates.append(digits[-8:])
    for c in candidates:
        if c and c in catalog_by_article:
            return catalog_by_article[c]
    return None


def _pdf_match_by_article(row_text, row, catalog_by_article):
    """Совпадение по артикулу: скобки, ячейка 8–14 цифр (EAN), «Артикул» в тексте."""
    seen = []
    m = re.search(r'\(([\dA-Za-zА-Яа-яЁё\-/.]{2,})\)', row_text)
    if m:
        seen.append(m.group(1).strip().upper().strip('.'))
    for cell in row:
        t = str(cell).strip()
        td = re.sub(r'\s', '', t)
        if re.fullmatch(r'\d{8,14}', td):
            seen.append(td)
        t_up = td.upper() if td else ''
        if 2 <= len(t_up) <= 36 and re.fullmatch(r'[\dA-ZА-ЯЁ\-/.]+', t_up) and not re.fullmatch(r'\d{1,7}', t_up):
            if t_up not in seen:
                seen.append(t_up)
    for m2 in re.finditer(r'Артикул[:\s]*(\d{8,14})\b', row_text, re.I):
        seen.append(m2.group(1))
    for m2 in re.finditer(r'\b(\d{12,14})\b', row_text):
        g = m2.group(1)
        if g not in seen:
            seen.append(g)

    for code in seen:
        hit = _pdf_lookup_article_in_map(code, catalog_by_article)
        if hit:
            return hit
    return None


def _pdf_match_confidence_display(base_score, lex_ratio, word_coverage):
    """
    Процент для UI: сочетает внутренний score отбора и реальное сходство строк.
    При верных колонках PDF lex_ratio высокий — пользователь снова видит ~90–100%, как раньше.
    """
    try:
        b = float(base_score)
        lr = float(lex_ratio)
        wc = float(word_coverage)
    except (TypeError, ValueError):
        return 0.0
    wc = max(wc, lr * 0.85)
    blend = 0.30 * b + 0.48 * lr + 0.22 * min(1.0, wc)
    return min(100.0, round(blend * 100, 1))


def _pdf_fuzzy_best_match(clean_name, catalog_search_index, pdf_retail_unit_price=None):
    """
    Сопоставление с каталогом. Если задана pdf_retail_unit_price — только позиции с той же розницей,
    внутри них — частичное совпадение по названию (пороги чуть мягче, т.к. цена уже отсекла лишнее).
    """
    from difflib import SequenceMatcher

    if not catalog_search_index:
        return None, 0.0, 0.0, 0.0, 0.0

    price_slice = catalog_search_index
    price_locked = False
    if pdf_retail_unit_price is not None:
        price_slice = [
            tup
            for tup in catalog_search_index
            if _pdf_retail_price_match(tup[0].get('retail_price'), pdf_retail_unit_price)
        ]
        if not price_slice:
            return None, 0.0, 0.0, 0.0, 0.0
        price_locked = True
        catalog_search_index = price_slice

    clean_lower = (clean_name or '').lower()
    clean_cmp = _pdf_normalize_for_compare(clean_name)
    clean_ns = _pdf_compare_nospace(clean_cmp)
    key_words = _pdf_significant_words(clean_lower)
    scored = []

    for item, norm, name_lower, norm_cmp in catalog_search_index:
        r1 = SequenceMatcher(None, clean_lower, name_lower).ratio()
        r2 = SequenceMatcher(None, clean_lower, norm).ratio()
        r3 = SequenceMatcher(None, clean_cmp, norm_cmp).ratio() if norm_cmp else 0.0
        r4 = (
            SequenceMatcher(None, clean_ns, _pdf_compare_nospace(norm_cmp)).ratio()
            if norm_cmp
            else 0.0
        )
        ratio = max(r1, r2, r3, r4)

        nw = len(key_words)
        match_words = sum(
            1
            for kw in key_words
            if kw in name_lower
            or kw in norm
            or (norm_cmp and kw in norm_cmp)
            or (norm_cmp and kw in _pdf_compare_nospace(norm_cmp))
        )
        word_part = (match_words / float(nw)) if nw else ratio

        base = 0.40 * ratio + 0.34 * word_part + 0.14 * r4
        if item.get('brand') and str(item['brand']).lower() in clean_lower:
            base += 0.11
        if item.get('category') and str(item['category']).lower() in clean_lower:
            base += 0.05
        if clean_lower in name_lower or (norm_cmp and clean_cmp in norm_cmp):
            base += 0.09
        elif name_lower in clean_lower or (norm_cmp and norm_cmp in clean_cmp):
            base += 0.06
        elif clean_ns and norm_cmp and clean_ns in _pdf_compare_nospace(norm_cmp):
            base += 0.07

        base = min(base, 0.995)
        scored.append((base, item, ratio, word_part))

    scored.sort(key=lambda x: (-x[0], -x[2]))
    top_s, top_item, top_lex, top_wpart = scored[0]
    second_s = scored[1][0] if len(scored) > 1 else 0.0
    gap = top_s - second_s

    if price_locked:
        if top_s >= 0.34 and gap >= 0.04:
            return top_item, top_s, second_s, top_lex, top_wpart
        if top_s >= 0.22 and gap >= 0.055:
            return top_item, top_s, second_s, top_lex, top_wpart
        if top_s >= 0.18 and top_lex >= 0.30 and gap >= 0.07:
            return top_item, top_s, second_s, top_lex, top_wpart
        return None, top_s, second_s, top_lex, top_wpart

    if top_s >= 0.40:
        return top_item, top_s, second_s, top_lex, top_wpart
    if top_s >= 0.26 and gap >= 0.055:
        return top_item, top_s, second_s, top_lex, top_wpart
    if top_s >= 0.22 and gap >= 0.095:
        return top_item, top_s, second_s, top_lex, top_wpart
    return None, top_s, second_s, top_lex, top_wpart


estimate_bp = Blueprint('estimate', __name__, url_prefix='/estimate')

# CSRF: session['_csrf_token'] выставляет основное приложение (inject_csrf / _csrf_token)

def _require_csrf(f):
    """Декоратор CSRF для estimate модуля"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
        if not token or token != session.get('_csrf_token'):
            return jsonify({'error': 'CSRF token missing or invalid'}), 403
        return f(*args, **kwargs)
    return decorated_function

def _require_json():
    """Проверка наличия JSON тела запроса"""
    data = request.get_json(silent=True)
    if not data:
        return None, (jsonify({"error": "Требуется JSON в теле запроса"}), 400)
    return data, None

def _safe_float(value, default=0.0):
    """Безопасное преобразование в float"""
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def _norm_item_name(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _backfill_estimate_wholesale_from_catalog(user_id, est_id, items):
    """Подставить опт из каталога по имени: пусто/0, или опт совпал с розницей (старый импорт), а в каталоге уже другое."""
    if not items:
        return
    mats = fetch_all(
        "SELECT name, wholesale_price FROM catalog_materials WHERE user_id = ?",
        (user_id,),
    )
    by_name = {}
    for m in mats:
        key = _norm_item_name(m.get("name"))
        if key:
            by_name[key] = _safe_float(m.get("wholesale_price"))
    eps = 0.02
    for it in items:
        if it.get("section") != "material":
            continue
        key = _norm_item_name(it.get("name"))
        if not key:
            continue
        cw = by_name.get(key)
        if cw is None or cw <= 0:
            continue
        line_w = _safe_float(it.get("wholesale_price"))
        line_p = _safe_float(it.get("price"))
        # Опт явно отличается от розницы — не перезаписываем (ручной ввод или уже из opt2)
        if line_w > 0 and abs(line_w - line_p) >= eps:
            continue
        if abs(cw - line_w) < eps:
            continue
        iid = it.get("id")
        if iid:
            execute(
                """UPDATE estimate_items SET wholesale_price = ?
                   WHERE id = ? AND estimate_id = ?
                   AND EXISTS (SELECT 1 FROM estimates WHERE id = ? AND user_id = ?)""",
                (cw, iid, est_id, est_id, user_id),
            )
        it["wholesale_price"] = cw


# ============================
# HTML СТРАНИЦЫ
# ============================

@estimate_bp.route('/')
@login_required
def estimate_list(): return render_template('estimate/list.html')

@estimate_bp.route('/catalog')
@login_required
def catalog_page(): return render_template('estimate/catalog.html')

@estimate_bp.route('/new')
@login_required
def new_estimate(): return render_template('estimate/editor.html', estimate_id=None)

@estimate_bp.route('/<int:estimate_id>')
@login_required
def edit_estimate(estimate_id): return render_template('estimate/editor.html', estimate_id=estimate_id)

# ============================
# API: КАТЕГОРИИ
# ============================

@estimate_bp.route('/api/catalog/categories', methods=['GET'])
@login_required
def api_get_categories():
    cat_type = request.args.get('type', '')  # 'material' или 'work'
    if cat_type:
        return jsonify(fetch_all("SELECT * FROM categories WHERE user_id = ? AND category_type = ? ORDER BY name", (current_user.id, cat_type)))
    return jsonify(fetch_all("SELECT * FROM categories WHERE user_id = ? ORDER BY name", (current_user.id,)))

@estimate_bp.route('/api/catalog/categories/tree', methods=['GET'])
@login_required
def api_get_categories_tree():
    """Получить древовидную структуру: Категория → Тип → Бренд"""
    cat_type = request.args.get('type', 'material')

    if cat_type != 'material':
        # Для работ — плоский список категорий
        cats = fetch_all(
            "SELECT * FROM categories WHERE user_id = ? AND category_type = ? ORDER BY name",
            (current_user.id, cat_type)
        )
        return jsonify([{'id': c['id'], 'name': c['name'], 'children': [], 'all_descendants': []} for c in cats])

    # Строим дерево из самих материалов: Категория → Тип(item_type) → Бренд
    items = fetch_all(
        "SELECT category, item_type, brand, name, id FROM catalog_materials WHERE user_id = ?",
        (current_user.id,)
    )

    # Строим иерархическое дерево
    tree_map = {}  # category -> { types -> { brands -> [] } }

    for item in items:
        cat = item['category'] or 'Без категории'
        itype = item['item_type'] or 'Другое'
        brand = item['brand'] or 'Без бренда'

        if cat not in tree_map:
            tree_map[cat] = {}
        if itype not in tree_map[cat]:
            tree_map[cat][itype] = {}
        if brand not in tree_map[cat][itype]:
            tree_map[cat][itype][brand] = []
        tree_map[cat][itype][brand].append(item)

    # Преобразуем в JSON-совместимое дерево
    tree = []
    for cat_name in sorted(tree_map.keys()):
        type_branches = tree_map[cat_name]
        type_nodes = []
        cat_total = 0

        # Проверяем: если только 1 тип и он совпадает с категорией — пропускаем уровень типа
        skip_type_level = False
        if len(type_branches) == 1:
            only_type = list(type_branches.keys())[0]
            # Сравниваем: нормализуем оба названия
            norm_cat = cat_name.lower().replace(' ', '').replace('-', '')
            norm_type = only_type.lower().replace(' ', '').replace('-', '')
            if norm_cat == norm_type or norm_type in norm_cat or norm_cat in norm_type:
                skip_type_level = True

        for type_name in sorted(type_branches.keys()):
            brand_branches = type_branches[type_name]
            brand_nodes = []
            type_total = 0

            for brand_name in sorted(brand_branches.keys()):
                item_list = brand_branches[brand_name]
                type_total += len(item_list)
                brand_nodes.append({
                    'id': None,
                    'name': brand_name,
                    'count': len(item_list),
                    'children': [],
                    'all_descendants': [],
                    'is_brand_leaf': True  # Флаг: это бренд, а не тип
                })

            cat_total += type_total
            type_nodes.append({
                'id': None,
                'name': type_name,
                'count': type_total,
                'children': brand_nodes,
                'all_descendants': [b['name'] for b in brand_nodes]
            })

        # Если пропускаем уровень типа — бренды становятся прямыми детьми категории
        if skip_type_level and type_nodes:
            # Все бренды из единственного типа
            all_brands = type_nodes[0]['children']
            final_children = all_brands
            all_descs = [b['name'] for b in all_brands]
        else:
            final_children = type_nodes
            all_descs = []
            for tn in type_nodes:
                all_descs.append(tn['name'])
                all_descs.extend(tn['all_descendants'])

        tree.append({
            'id': None,
            'name': cat_name,
            'count': cat_total,
            'children': final_children,
            'all_descendants': all_descs,
            'skip_type_level': skip_type_level  # Флаг для фронтенда
        })

    return jsonify(tree)

@estimate_bp.route('/api/catalog/categories', methods=['POST'])
@login_required
@_require_csrf
def api_add_category():
    data, err = _require_json()
    if err: return err
    try:
        parent_id = data.get('parent_id')
        if not parent_id or parent_id == 'null': parent_id = None
        cat_type = data.get('type', 'material')  # 'material' или 'work'
        execute("INSERT INTO categories (user_id, name, parent_id, category_type) VALUES (?, ?, ?, ?)",
                (current_user.id, data['name'], parent_id, cat_type))
        return jsonify({"status": "ok"}), 201
    except Exception as e:
        logger.error(f"Add category error: {e}")
        return jsonify({"error": "Ошибка при создании категории"}), 400

@estimate_bp.route('/api/parse-pdf', methods=['POST'])
@login_required
def api_parse_pdf():
    """Загрузить PDF, извлечь таблицы, сопоставить с каталогом"""
    try:
        import pdfplumber
        from difflib import SequenceMatcher
        import io, re

        if 'file' not in request.files:
            return jsonify({"error": "Нет файла"}), 400

        file = request.files['file']
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({"error": "Только PDF файлы"}), 400

        # Извлекаем текст и таблицы из PDF
        pdf_file = pdfplumber.open(io.BytesIO(file.read()))

        full_text_chunks = []
        all_rows = []
        for page in pdf_file.pages:
            text = page.extract_text()
            if text:
                full_text_chunks.append(text)
            # Пробуем извлечь таблицы
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        if row and any(cell for cell in row if cell):
                            all_rows.append([str(c).strip() if c else '' for c in row])
            else:
                # Если таблиц нет — извлекаем текст построчно
                if text:
                    for line in text.split('\n'):
                        line = line.strip()
                        if line:
                            # Разбиваем по табуляции или нескольким пробелам
                            parts = re.split(r'\t|\s{3,}', line)
                            all_rows.append([p.strip() for p in parts if p.strip()])

        pdf_file.close()

        full_text = '\n'.join(full_text_chunks)
        cart_rows = _pdf_parse_cart_ready_format(full_text)
        from_cart_pdf = len(cart_rows) >= 1
        if from_cart_pdf:
            all_rows = cart_rows

        mode_raw = (request.form.get('mode') or request.args.get('mode') or 'retail').strip().lower()
        import_mode = 'wholesale' if mode_raw in ('wholesale', 'opt', 'purchase', 'cost') else 'retail'

        # Получаем каталог пользователя
        catalog_items = fetch_all(
            "SELECT id, name, article, brand, category, retail_price, purchase_price, wholesale_price, item_type FROM catalog_materials WHERE user_id = ?",
            (current_user.id,)
        )

        catalog_by_article = _pdf_catalog_article_map(catalog_items)
        catalog_search_index = []
        for item in catalog_items:
            norm = _pdf_normalize_name_for_index(item['name'])
            name_lower = (item['name'] or '').lower()
            if not (norm or '').strip():
                norm = name_lower
            blob = ' '.join(
                filter(
                    None,
                    [item.get('name'), item.get('brand'), item.get('article'), item.get('category')],
                )
            )
            norm_cmp = _pdf_normalize_for_compare(blob)
            catalog_search_index.append((item, norm, name_lower, norm_cmp))

        matched = []
        unmatched = []
        MIN_MATCH_SCORE = 0.22

        for row in all_rows:
            if not row or all(not cell for cell in row):
                continue

            row_text = ' '.join(str(cell) for cell in row if cell).strip()
            if len(row_text) < 3:
                continue

            if _PDF_HEADER_START.search(row_text):
                continue
            if _PDF_TOTALS_ROW.search(row_text):
                continue

            table_layout = None if from_cart_pdf else _pdf_detect_table_material_row(row, import_mode)

            pdf_retail_unit = _pdf_extract_pdf_retail_unit_price(row, from_cart_pdf, table_layout)
            pdf_wholesale_unit = _pdf_extract_pdf_unit_price_with_vat(row, from_cart_pdf, table_layout)
            file_unit_price = pdf_wholesale_unit if import_mode == 'wholesale' else pdf_retail_unit

            article_match_item = _pdf_match_by_article(row_text, row, catalog_by_article)
            best_match = article_match_item
            # Совпадение по артикулу не отменяем из‑за расхождения цены PDF и каталога.
            best_score = 1.0 if best_match else 0.0
            second_score = 0.0
            from_article = bool(article_match_item)
            lex_ratio = 1.0
            word_cov = 1.0

            if table_layout:
                clean_name = str(row[table_layout['name_idx']] or '').strip()
            else:
                clean_name = _pdf_row_product_title(row_text)
            if len(clean_name) < 4:
                clean_name = row_text
                clean_name = re.sub(r'^\d+\s+', '', clean_name)
                clean_name = re.sub(r'^\d+[\.,]\d{2}\s+', '', clean_name)
                clean_name = re.sub(r'\s*В\s*наличии.*$', '', clean_name, flags=re.I)
                clean_name = clean_name.strip()

            if not best_match and clean_name:
                best_match, best_score, second_score, lex_ratio, word_cov = _pdf_fuzzy_best_match(
                    clean_name,
                    catalog_search_index,
                    (pdf_retail_unit if import_mode == 'retail' else None),
                )

            reach_match = (
                from_article
                or best_score >= MIN_MATCH_SCORE
                or (pdf_retail_unit is not None and best_score >= 0.17)
            )
            if best_match and reach_match:
                pdf_unit = _pdf_extract_unit(row, table_layout)
                qty = _pdf_sanitize_qty(
                    _pdf_extract_qty(row, from_cart_pdf, table_layout),
                    best_match.get('retail_price'),
                    best_match.get('purchase_price'),
                )
                if from_article:
                    conf_pct = 100.0
                else:
                    conf_pct = _pdf_match_confidence_display(best_score, lex_ratio, word_cov)
                ambiguous = (
                    not from_article
                    and second_score >= best_score - 0.035
                    and second_score >= 0.32
                )
                if ambiguous:
                    conf_pct = round(min(conf_pct * 0.88, 100), 1)
                file_purchase = (
                    _pdf_purchase_for_file_retail(pdf_retail_unit, best_match)
                    if pdf_retail_unit is not None
                    else None
                )
                matched.append({
                    'row': row,
                    'row_text': row_text,
                    'clean_name': clean_name,
                    'item': {
                        'id': best_match['id'],
                        'name': best_match['name'],
                        'article': best_match['article'],
                        'brand': best_match['brand'],
                        'category': best_match['category'],
                        'retail_price': best_match['retail_price'],
                        'purchase_price': best_match['purchase_price'],
                        'wholesale_price': best_match.get('wholesale_price'),
                        'item_type': best_match['item_type'],
                    },
                    'qty': qty,
                    'unit': pdf_unit,
                    'confidence': conf_pct,
                    'ambiguous': ambiguous,
                    'file_retail_unit': round(float(pdf_retail_unit), 4)
                    if pdf_retail_unit is not None
                    else None,
                    'file_wholesale_unit': round(float(pdf_wholesale_unit), 4)
                    if pdf_wholesale_unit is not None
                    else None,
                    'file_unit_price': round(float(file_unit_price), 4)
                    if file_unit_price is not None
                    else None,
                    'file_purchase_unit': file_purchase,
                })
            else:
                if (
                    import_mode == 'retail'
                    and
                    not best_match
                    and pdf_retail_unit is not None
                    and catalog_items
                    and not any(
                        _pdf_retail_price_match(it.get('retail_price'), pdf_retail_unit)
                        for it in catalog_items
                    )
                ):
                    rej = 'price_not_in_catalog'
                elif not best_match and best_score >= 0.35 and second_score >= best_score - 0.035:
                    rej = 'ambiguous'
                elif not best_match and best_score > 0:
                    rej = 'low_score'
                else:
                    rej = 'no_match' if catalog_items else 'no_catalog'
                qty_u = _pdf_sanitize_qty(
                    _pdf_extract_qty(row, from_cart_pdf, table_layout),
                    (file_unit_price if file_unit_price is not None else 0),
                    0,
                )
                unit_u = _pdf_extract_unit(row, table_layout)
                unmatched.append({
                    'row': row,
                    'row_text': row_text,
                    'clean_name': clean_name,
                    'reject_reason': rej,
                    'qty': qty_u,
                    'unit': unit_u,
                    'file_unit_price': round(float(file_unit_price), 4)
                    if file_unit_price is not None
                    else None,
                })

        return jsonify({
            "import_mode": import_mode,
            "matched": matched,
            "unmatched": unmatched,
            "total_rows": len(all_rows),
            "matched_count": len(matched),
            "unmatched_count": len(unmatched),
            "catalog_count": len(catalog_items),
            "catalog_empty": len(catalog_items) == 0,
        })

    except Exception as e:
        import logging, traceback
        tb = traceback.format_exc()
        logging.error(f"parse_pdf error: {e}\n{tb}")
        return jsonify({"error": str(e)}), 500

def _opt_config_path():
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '.opt_config.json')


@estimate_bp.route('/api/price-sync/checkpoint', methods=['GET'])
@login_required
def price_sync_checkpoint():
    """Чекпоинт: что сохранено для opt-akvabreg (логин и длина/маска пароля, без раскрытия)."""
    import json, os
    path = _opt_config_path()
    if not os.path.exists(path):
        return jsonify({
            "configured": False,
            "login": "",
            "password_saved": False,
            "password_length": 0,
            "password_mask": "",
        })
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        return jsonify({"error": str(e), "configured": False}), 500
    login_v = (cfg.get('login') or '').strip()
    pwd = cfg.get('password') or ''
    if not isinstance(pwd, str):
        pwd = str(pwd)
    n = len(pwd)
    mask = ('\u2022' * min(n, 40) + ('\u2026' if n > 40 else '')) if n else ''
    return jsonify({
        "configured": bool(login_v or pwd),
        "login": login_v,
        "password_saved": bool(pwd),
        "password_length": n,
        "password_mask": mask,
    })


@estimate_bp.route('/api/price-sync/reveal-password', methods=['POST'])
@login_required
@_require_csrf
def price_sync_reveal_password():
    """Показать сохранённый пароль в интерфейсе (только для текущего пользователя приложения)."""
    import json, os
    path = _opt_config_path()
    if not os.path.exists(path):
        return jsonify({"error": "Конфиг не найден", "password": ""}), 404
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"password": cfg.get('password') or ""})


@estimate_bp.route('/api/price-sync/config', methods=['POST'])
@login_required
@_require_csrf
def price_sync_config():
    """Сохранить логин/пароль от opt-akvabreg.by (пустой пароль не затирает уже сохранённый)."""
    data, err = _require_json()
    if err: return err
    try:
        import json, os
        config_path = _opt_config_path()
        existing = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
        login = (data.get('login') if 'login' in data else None)
        if login is None:
            login = existing.get('login', '')
        login = (login or '').strip()
        password = data.get('password', '')
        if password is None:
            password = ''
        if isinstance(password, str) and password.strip() == '':
            password = existing.get('password', '')
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({'login': login, 'password': password}, f, ensure_ascii=False)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"price_sync_config error: {e}")
        return jsonify({"error": str(e)}), 500

@estimate_bp.route('/api/price-sync/run', methods=['POST'])
@login_required
@_require_csrf
def price_sync_run():
    """Запустить синхронизацию цен"""
    try:
        from price_sync import login, compare_prices, download_price_list, parse_price_list_dataframe
        import pandas as pd, os

        session, msg = login()
        if not session:
            return jsonify({"error": msg, "status": "auth_failed"}), 401

        filepath, filename, msg = download_price_list(session)
        if not filepath:
            return jsonify({"error": msg, "status": "download_failed"}), 500

        # Загрузка локальных цен
        local_items = fetch_all(
            "SELECT id, name, article, retail_price, purchase_price FROM catalog_materials WHERE user_id = ?",
            (current_user.id,)
        )

        df = pd.read_excel(filepath)
        new_items = parse_price_list_dataframe(df)

        results = compare_prices(local_items, new_items)
        results['filepath'] = filepath
        results['filename'] = filename

        return jsonify(results)

    except Exception as e:
        import logging, traceback
        tb = traceback.format_exc()
        logging.error(f"price_sync_run error: {e}\n{tb}")
        return jsonify({"error": str(e)}), 500

@estimate_bp.route('/api/price-sync/apply', methods=['POST'])
@login_required
@_require_csrf
def price_sync_apply():
    """Применить новые цены из результата синхронизации"""
    data, err = _require_json()
    if err: return err
    try:
        updates = data.get('updates', [])
        updated = 0
        for item in updates:
            article = item.get('article', '').strip().upper()
            new_price = float(item.get('new_price', 0))
            if article and new_price > 0:
                execute(
                    "UPDATE catalog_materials SET retail_price = ? WHERE user_id = ? AND UPPER(article) = ?",
                    (new_price, current_user.id, article)
                )
                updated += 1
        return jsonify({"status": "ok", "updated": updated})
    except Exception as e:
        logger.error(f"price_sync_apply error: {e}")
        return jsonify({"error": str(e)}), 500

@estimate_bp.route('/api/catalog/categories/<int:cat_id>', methods=['DELETE'])
@login_required
@_require_csrf
def api_delete_category(cat_id):
    execute("DELETE FROM categories WHERE id = ? AND user_id = ?", (cat_id, current_user.id))
    return jsonify({"ok": True})

# ============================
# API: СМЕТЫ
# ============================

def _next_estimate_number(user_id):
    """Следующий номер С-NNNN по максимуму среди существующих (данные не трогаем)."""
    rows = fetch_all("SELECT number FROM estimates WHERE user_id = ?", (user_id,))
    max_n = 0
    for r in rows:
        num = (r.get('number') or '').strip()
        m = re.match(r'^С-(\d+)$', num, re.IGNORECASE)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"С-{max_n + 1:04d}"


def _resolve_object_id_for_user(raw, user_id):
    """
    object_id из тела запроса: 0 / отсутствует — без привязки к объекту.
    Иначе только если объект существует и принадлежит user_id.
    """
    if raw is None or raw == '':
        return 0, None
    try:
        oid = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Некорректный object_id"}), 400)
    if oid <= 0:
        return 0, None
    if not fetch_one("SELECT 1 AS x FROM objects WHERE id = ? AND user_id = ?", (oid, user_id)):
        return None, (jsonify({"error": "Объект не найден или не принадлежит вам"}), 400)
    return oid, None


@estimate_bp.route('/api/estimates', methods=['GET'])
@login_required
def api_get_estimates():
    estimates = fetch_all("SELECT * FROM estimates WHERE user_id = ? ORDER BY date DESC", (current_user.id,))
    return jsonify(estimates)

@estimate_bp.route('/api/estimates', methods=['POST'])
@login_required
@_require_csrf
def api_create_estimate():
    data, err = _require_json()
    if err: return err
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    number = _next_estimate_number(current_user.id)

    object_id, bad = _resolve_object_id_for_user(data.get('object_id'), current_user.id)
    if bad:
        return bad

    eid = execute("""INSERT INTO estimates
        (user_id, number, date, object_id, object_name, client, status, vat_percent, markup_percent, discount_percent, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (current_user.id, number, data.get('date', datetime.now().strftime('%Y-%m-%d')),
         object_id, data.get('object_name', ''), data.get('client', ''),
         data.get('status', 'Черновик'), _safe_float(data.get('vat_percent')), _safe_float(data.get('markup_percent')),
         _safe_float(data.get('discount_percent')), data.get('notes', ''), now, now), return_id=True)
    return jsonify({"id": eid, "number": number}), 201

@estimate_bp.route('/api/estimates/<int:est_id>', methods=['GET'])
@login_required
def api_get_estimate(est_id):
    est = fetch_one("SELECT * FROM estimates WHERE id = ? AND user_id = ?", (est_id, current_user.id))
    if not est: return jsonify({"error": "Not found"}), 404
    items = fetch_all("SELECT * FROM estimate_items WHERE estimate_id = ?", (est_id,))
    _backfill_estimate_wholesale_from_catalog(current_user.id, est_id, items)
    return jsonify({**est, 'items': items})

@estimate_bp.route('/api/estimates/<int:est_id>', methods=['PUT'])
@login_required
@_require_csrf
def api_update_estimate(est_id):
    data, err = _require_json()
    if err: return err
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prev = fetch_one(
        "SELECT object_id FROM estimates WHERE id = ? AND user_id = ?",
        (est_id, current_user.id),
    )
    if not prev:
        return jsonify({"error": "Not found"}), 404
    object_id = prev.get('object_id') or 0
    if 'object_id' in data:
        object_id, bad = _resolve_object_id_for_user(data.get('object_id'), current_user.id)
        if bad:
            return bad
    n = execute_rowcount("""UPDATE estimates SET date=?, object_name=?, client=?, status=?,
           vat_percent=?, markup_percent=?, discount_percent=?, notes=?, object_id=?, updated_at=?
           WHERE id=? AND user_id=?""",
        (data.get('date'), data.get('object_name'), data.get('client'), data.get('status'),
         _safe_float(data.get('vat_percent')), _safe_float(data.get('markup_percent')),
         _safe_float(data.get('discount_percent')), data.get('notes', ''), object_id, now, est_id, current_user.id))
    if n == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})

@estimate_bp.route('/api/estimates/<int:est_id>', methods=['DELETE'])
@login_required
@_require_csrf
def api_delete_estimate(est_id):
    # Только позиции сметы текущего пользователя (иначе IDOR: чужой est_id удалил бы чужие строки)
    execute(
        "DELETE FROM estimate_items WHERE estimate_id IN (SELECT id FROM estimates WHERE id = ? AND user_id = ?)",
        (est_id, current_user.id),
    )
    execute("DELETE FROM estimates WHERE id = ? AND user_id = ?", (est_id, current_user.id))
    return jsonify({"ok": True})

# ============================
# API: СТРОКИ СМЕТЫ
# ============================

@estimate_bp.route('/api/estimates/<int:est_id>/items', methods=['POST'])
@login_required
@_require_csrf
def api_add_item(est_id):
    data, err = _require_json()
    if err: return err
    est = fetch_one("SELECT id FROM estimates WHERE id = ? AND user_id = ?", (est_id, current_user.id))
    if not est:
        logger.warning(f"api_add_item: estimate {est_id} not found for user {current_user.id}")
        return jsonify({"error": "Forbidden"}), 403

    price = _safe_float(data.get('price'))
    purchase = _safe_float(data.get('purchase_price'))
    wholesale = _safe_float(data.get('wholesale_price')) if data.get('section') == 'material' else 0
    qty = _safe_float(data.get('quantity'), default=1.0)
    total = price * qty
    profit = (price - purchase) * qty if data.get('section') == 'material' else 0

    logger.info(f"api_add_item: estimate_id={est_id}, section={data.get('section')}, name={data.get('name')}, qty={qty}, price={price}")

    _add_to_catalog(data.get('section'), data.get('name'), data.get('unit'), price, purchase)

    item_id = execute("""INSERT INTO estimate_items
        (estimate_id, section, name, unit, quantity, price_type, price, purchase_price, wholesale_price, total, material_profit, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM estimate_items WHERE estimate_id=?))""",
        (est_id, data.get('section'), data.get('name'), data.get('unit'), qty, 'retail', price, purchase, wholesale, total, profit, est_id), return_id=True)
    return jsonify({"id": item_id, "total": total, "material_profit": profit}), 201

@estimate_bp.route('/api/items/<int:item_id>', methods=['PUT'])
@login_required
@_require_csrf
def api_update_item(item_id):
    data, err = _require_json()
    if err: return err
    price = _safe_float(data.get('price'))
    purchase = _safe_float(data.get('purchase_price'))
    wholesale = _safe_float(data.get('wholesale_price')) if data.get('section', 'material') == 'material' else 0
    qty = _safe_float(data.get('quantity'), default=1.0)
    total = price * qty
    profit = (price - purchase) * qty if data.get('section', 'material') == 'material' else 0

    n = execute_rowcount("""UPDATE estimate_items SET name=?, unit=?, quantity=?, price=?, purchase_price=?, wholesale_price=?, total=?, material_profit=?
               WHERE id=? AND estimate_id IN (SELECT id FROM estimates WHERE user_id=?)""",
            (data.get('name'), data.get('unit'), qty, price, purchase, wholesale, total, profit, item_id, current_user.id))
    if n == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True, "total": total, "material_profit": profit})

@estimate_bp.route('/api/items/<int:item_id>', methods=['DELETE'])
@login_required
@_require_csrf
def api_delete_item(item_id):
    n = execute_rowcount(
        "DELETE FROM estimate_items WHERE id=? AND estimate_id IN (SELECT id FROM estimates WHERE user_id=?)",
        (item_id, current_user.id),
    )
    if n == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})

# ============================
# КАТАЛОГ
# ============================

def _add_to_catalog(section, name, unit, price, purchase):
    if not name: return
    try:
        if section == 'material':
            exists = fetch_one("SELECT id FROM catalog_materials WHERE name = ? AND user_id = ?", (name, current_user.id))
            if not exists:
                execute("""INSERT INTO catalog_materials 
                    (user_id, name, unit, category, purchase_price, retail_price, wholesale_price, min_wholesale_qty, description, use_count) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""", 
                    (current_user.id, name, unit, '', purchase, price, price, 10, ''))
            else:
                execute("UPDATE catalog_materials SET use_count = use_count + 1 WHERE name = ? AND user_id = ?", (name, current_user.id))
        else:
            exists = fetch_one("SELECT id FROM catalog_works WHERE name = ? AND user_id = ?", (name, current_user.id))
            if not exists:
                execute("""INSERT INTO catalog_works 
                    (user_id, name, unit, price, description, use_count) 
                    VALUES (?, ?, ?, ?, ?, 1)""", 
                    (current_user.id, name, unit, price, ''))
            else:
                execute("UPDATE catalog_works SET use_count = use_count + 1 WHERE name = ? AND user_id = ?", (name, current_user.id))
    except Exception as e:
        print(f"Catalog Error: {e}")

@estimate_bp.route('/api/catalog/materials', methods=['GET'])
@login_required
def api_get_materials():
    return jsonify(fetch_all("SELECT * FROM catalog_materials WHERE user_id = ? ORDER BY use_count DESC", (current_user.id,)))

@estimate_bp.route('/api/catalog/materials', methods=['POST'])
@login_required
@_require_csrf
def api_add_catalog_material():
    data, err = _require_json()
    if err: return err
    try:
        retail = _safe_float(data.get('retail_price'))
        purchase = _safe_float(data.get('purchase_price'))
        wholesale = _safe_float(data.get('wholesale_price'), default=retail)
        category = data.get('category', '')
        min_qty = _safe_float(data.get('min_wholesale_qty'), default=10)
        desc = data.get('description', '')
        execute("""INSERT INTO catalog_materials
            (user_id, name, unit, category, article, brand, item_type, purchase_price, retail_price, wholesale_price, min_wholesale_qty, description, use_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (current_user.id, data.get('name', ''), data.get('unit', 'шт'), category,
             (data.get('article') or '').strip(), (data.get('brand') or '').strip(), (data.get('item_type') or '').strip(),
             purchase, retail, wholesale, min_qty, desc))
        return jsonify({"status": "ok"}), 201
    except Exception as e:
        logger.error(f"Add material error: {e}")
        return jsonify({"error": "Ошибка при создании материала"}), 400

@estimate_bp.route('/api/catalog/materials/<int:item_id>', methods=['PUT'])
@login_required
@_require_csrf
def api_update_catalog_material(item_id):
    data, err = _require_json()
    if err: return err
    try:
        execute("""UPDATE catalog_materials SET name=?, unit=?, category=?, article=?, brand=?, item_type=?,
                   purchase_price=?, retail_price=?, wholesale_price=?, min_wholesale_qty=?, description=?
                   WHERE id=? AND user_id=?""",
            (data.get('name'), data.get('unit'), data.get('category', ''),
             (data.get('article') or '').strip(), (data.get('brand') or '').strip(), (data.get('item_type') or '').strip(),
             _safe_float(data.get('purchase_price')), _safe_float(data.get('retail_price')),
             _safe_float(data.get('wholesale_price')), _safe_float(data.get('min_wholesale_qty'), default=10),
             data.get('description', ''), item_id, current_user.id))
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Update material error: {e}")
        return jsonify({"error": "Ошибка при обновлении материала"}), 400

@estimate_bp.route('/api/catalog/materials/<int:item_id>', methods=['DELETE'])
@login_required
@_require_csrf
def api_delete_catalog_material(item_id):
    execute("DELETE FROM catalog_materials WHERE id = ? AND user_id = ?", (item_id, current_user.id))
    return jsonify({"ok": True})

@estimate_bp.route('/api/catalog/works', methods=['GET'])
@login_required
def api_get_works():
    return jsonify(fetch_all("SELECT * FROM catalog_works WHERE user_id = ? ORDER BY use_count DESC", (current_user.id,)))

@estimate_bp.route('/api/catalog/works', methods=['POST'])
@login_required
@_require_csrf
def api_add_catalog_work():
    data, err = _require_json()
    if err: return err
    try:
        price = _safe_float(data.get('price'))
        desc = data.get('description', '')
        execute("""INSERT INTO catalog_works
            (user_id, name, unit, price, description, use_count)
            VALUES (?, ?, ?, ?, ?, 1)""",
            (current_user.id, data.get('name', ''), data.get('unit', 'шт'), price, desc))
        return jsonify({"status": "ok"}), 201
    except Exception as e:
        logger.error(f"Add work error: {e}")
        return jsonify({"error": "Ошибка при создании работы"}), 400

@estimate_bp.route('/api/catalog/works/<int:item_id>', methods=['PUT'])
@login_required
@_require_csrf
def api_update_catalog_work(item_id):
    data, err = _require_json()
    if err: return err
    try:
        execute("""UPDATE catalog_works SET name=?, unit=?, price=?, description=?
                   WHERE id=? AND user_id=?""",
            (data.get('name'), data.get('unit'), _safe_float(data.get('price')), data.get('description', ''), item_id, current_user.id))
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Update work error: {e}")
        return jsonify({"error": "Ошибка при обновлении работы"}), 400

@estimate_bp.route('/api/catalog/works/<int:item_id>', methods=['DELETE'])
@login_required
@_require_csrf
def api_delete_catalog_work(item_id):
    execute("DELETE FROM catalog_works WHERE id = ? AND user_id = ?", (item_id, current_user.id))
    return jsonify({"ok": True})

# ============================
# ЭКСПОРТ И СВЯЗИ
# ============================

@estimate_bp.route('/api/estimates/by-object/<int:object_id>', methods=['GET'])
@login_required
def api_get_estimates_by_object(object_id):
    estimates = fetch_all("SELECT * FROM estimates WHERE object_id = ? AND user_id = ?", (object_id, current_user.id))
    if not estimates:
        return jsonify({
            'estimates': [],
            'total_works': 0, 'total_materials': 0,
            'total_material_profit': 0
        })

    # Один запрос вместо N+1: загружаем все строки всех смет
    est_ids = [e['id'] for e in estimates]
    placeholders = ','.join('?' for _ in est_ids)
    all_items = fetch_all(
        f"SELECT * FROM estimate_items WHERE estimate_id IN ({placeholders})",
        tuple(est_ids)
    )
    # Группируем по estimate_id
    items_by_est = {}
    for item in all_items:
        items_by_est.setdefault(item['estimate_id'], []).append(item)

    total_works = 0; total_materials = 0; total_mat_profit = 0
    result_estimates = []
    for est in estimates:
        items = items_by_est.get(est['id'], [])
        est_works = sum(i['total'] for i in items if i['section'] == 'work')
        est_mats = sum(i['total'] for i in items if i['section'] == 'material')
        est_profit = sum(i.get('material_profit', 0) for i in items if i['section'] == 'material')
        total_works += est_works; total_materials += est_mats; total_mat_profit += est_profit
        sub = est_works + est_mats
        markup = sub * _safe_float(est.get('markup_percent')) / 100
        vat = (sub + markup) * _safe_float(est.get('vat_percent')) / 100
        disc = (sub + markup + vat) * _safe_float(est.get('discount_percent')) / 100
        result_estimates.append({
            'id': est['id'], 'number': est['number'], 'date': est['date'], 'status': est['status'],
            'total_works': est_works, 'total_materials': est_mats, 'total': sub + markup + vat - disc
        })
    return jsonify({
        'estimates': result_estimates,
        'total_works': total_works, 'total_materials': total_materials,
        'total_material_profit': total_mat_profit
    })

def _sanitize_excel(val):
    """Защита от формульных инъекций в Excel (CVE-2014-6352)"""
    if val is None:
        return ''
    s = str(val)
    if s.startswith(('=', '+', '-', '@')):
        s = "'" + s
    return s

@estimate_bp.route('/api/estimates/<int:est_id>/export', methods=['GET'])
@login_required
def api_export_excel(est_id):
    est = fetch_one("SELECT * FROM estimates WHERE id = ? AND user_id = ?", (est_id, current_user.id))
    if not est:
        return jsonify({"error": "Not found"}), 404

    items = fetch_all("SELECT * FROM estimate_items WHERE estimate_id = ? ORDER BY sort_order", (est_id,))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Смета {est['number']}"

    font_title = Font(name='Arial', size=14, bold=True, color="2E75B6")
    font_hdr = Font(name='Arial', size=11, bold=True, color="FFFFFF")
    fill_hdr = PatternFill(start_color="2E75B6", end_color="2E75B6", fill_type='solid')
    fill_sec = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type='solid')
    thin = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    ws.merge_cells('A1:F1')
    ws['A1'].value = f"Смета №{est['number']} от {est['date']}"
    ws['A1'].font = font_title

    ws['A3'].value = f"Объект: {_sanitize_excel(est['object_name'])}"
    ws['A4'].value = f"Клиент: {_sanitize_excel(est['client'])}"

    row = 6
    headers = ["№", "Наименование", "Ед.изм", "Кол-во", "Цена", "Сумма"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = font_hdr
        c.fill = fill_hdr
        c.alignment = Alignment(horizontal='center')
        c.border = thin

    mats = [x for x in items if x['section'] == 'material']
    works = [x for x in items if x['section'] == 'work']
    row = 7
    total_all = 0

    if mats:
        ws.merge_cells(f'A{row}:F{row}')
        c = ws.cell(row=row, column=1, value="МАТЕРИАЛЫ")
        c.font = font_hdr
        c.fill = fill_sec
        c.border = thin
        row += 1
        for i, it in enumerate(mats, 1):
            ws.cell(row=row, column=1, value=i).border = thin
            ws.cell(row=row, column=2, value=_sanitize_excel(it['name'])).border = thin
            ws.cell(row=row, column=3, value=it['unit']).border = thin
            ws.cell(row=row, column=4, value=it['quantity']).border = thin
            ws.cell(row=row, column=5, value=it['price']).border = thin
            ws.cell(row=row, column=5).number_format = '#,##0.00'
            ws.cell(row=row, column=6, value=it['total']).border = thin
            ws.cell(row=row, column=6).number_format = '#,##0.00'
            total_all += it['total']
            row += 1

    if works:
        ws.merge_cells(f'A{row}:F{row}')
        c = ws.cell(row=row, column=1, value="РАБОТЫ")
        c.font = font_hdr
        c.fill = fill_sec
        c.border = thin
        row += 1
        for i, it in enumerate(works, 1):
            ws.cell(row=row, column=1, value=i).border = thin
            ws.cell(row=row, column=2, value=_sanitize_excel(it['name'])).border = thin
            ws.cell(row=row, column=3, value=it['unit']).border = thin
            ws.cell(row=row, column=4, value=it['quantity']).border = thin
            ws.cell(row=row, column=5, value=it['price']).border = thin
            ws.cell(row=row, column=5).number_format = '#,##0.00'
            ws.cell(row=row, column=6, value=it['total']).border = thin
            ws.cell(row=row, column=6).number_format = '#,##0.00'
            total_all += it['total']
            row += 1

    ws.cell(row=row + 1, column=5, value="ИТОГО:").font = Font(bold=True, size=12)
    ws.cell(row=row + 1, column=6, value=total_all).font = Font(bold=True, size=12)
    ws.cell(row=row + 1, column=6).number_format = '#,##0.00'

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"Smeta_{est['number']}.xlsx"
    )
