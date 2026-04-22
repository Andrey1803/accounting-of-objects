# -*- coding: utf-8 -*-
"""
Синхронизация цен с сайтом opt-akvabreg.by
Авторизуется на сайте, скачивает прайс-лист, сравнивает с нашей базой.
"""
import sqlite3, json, os, sys, io
import requests
from bs4 import BeautifulSoup
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_PATH = 'app_data.db'
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.opt_config.json')
BASE_URL = 'https://opt-akvabreg.by'

def load_config():
    """Загрузить логин/пароль из конфига (без пробелов по краям — частая причина отказа Bitrix)."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        for k in ('login', 'password'):
            v = cfg.get(k)
            if isinstance(v, str):
                cfg[k] = v.strip()
        return cfg
    return {}

def save_config(login, password):
    """Сохранить логин/пароль"""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump({'login': login, 'password': password}, f)

def _bitrix_alert_error_text(soup):
    """Текст ошибки из Bitrix (.alert-danger)."""
    el = soup.select_one('div.alert.alert-danger, .alert-danger')
    if el:
        tx = el.get_text(separator=' ', strip=True)
        if tx:
            return tx[:600]
    return None


def login():
    """Авторизоваться на сайте (Bitrix: форма с /auth/?login=yes)."""
    cfg = load_config()
    if not cfg.get('login') or not cfg.get('password'):
        return None, "Не настроены логин/пароль. Укажите в интерфейсе «Синхронизация цен» или POST /estimate/api/price-sync/config"
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,be;q=0.8,en;q=0.7',
    })
    
    try:
        login_form = None
        resp = None
        for url in (f'{BASE_URL}/auth/?login=yes', f'{BASE_URL}/help/payment/'):
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            for form in soup.find_all('form'):
                if form.find('input', {'type': 'password'}):
                    login_form = form
                    break
            if login_form:
                break

        if not login_form:
            return None, "Не найдена форма авторизации на сайте"

        csrf_token = None
        csrf_input = soup.find('input', {'name': re.compile(r'csrf|token|_token', re.I)})
        if csrf_input:
            csrf_token = csrf_input.get('value')
        
        action = login_form.get('action', '')
        if action.startswith('/'):
            action = BASE_URL + action
        elif action:
            action = f'{BASE_URL}/{action}'
        else:
            action = resp.url
        
        # Собираем данные формы (Bitrix /auth/?login=yes: USER_LOGIN, USER_PASSWORD, скрытый Login=Y, кнопка Login1)
        form_data = {}
        for inp in login_form.find_all('input'):
            name = inp.get('name')
            if not name:
                continue
            itype = (inp.get('type') or 'text').lower()
            if itype == 'hidden':
                form_data[name] = inp.get('value', '')
            elif itype == 'checkbox':
                if inp.has_attr('checked'):
                    form_data[name] = inp.get('value', 'Y')
            elif itype in ('text', 'email', 'tel'):
                form_data[name] = inp.get('value', '')
            elif itype == 'password':
                form_data[name] = ''

        for btn in login_form.find_all('button'):
            nm = btn.get('name')
            if nm and (btn.get('type') or '').lower() == 'submit':
                form_data[nm] = btn.get('value', 'Y')

        if 'Login1' not in form_data:
            sb = login_form.find('button', attrs={'name': 'Login1'})
            if sb is not None:
                form_data['Login1'] = sb.get('value', 'Y')

        if csrf_token:
            for k in list(form_data.keys()):
                if 'csrf' in k.lower() or 'token' in k.lower():
                    form_data[k] = csrf_token
                    break

        login_field = None
        pass_field = None
        for inp in login_form.find_all('input'):
            name = inp.get('name')
            if not name:
                continue
            itype = (inp.get('type') or 'text').lower()
            if itype == 'password':
                pass_field = name
            elif itype in ('text', 'email'):
                nl = name.lower()
                if nl == 'user_login' or nl.endswith('_login') or nl in ('login', 'email', 'user_email'):
                    login_field = name

        if pass_field:
            form_data[pass_field] = cfg['password']
        else:
            return None, "На странице входа не найдено поле пароля"

        if login_field:
            form_data[login_field] = cfg['login']
        else:
            text_inputs = [
                inp for inp in login_form.find_all('input')
                if (inp.get('type') or 'text').lower() == 'text' and inp.get('name')
            ]
            if len(text_inputs) == 1:
                form_data[text_inputs[0]['name']] = cfg['login']
            else:
                return None, "На странице входа не найдено поле логина"

        if login_form.find('input', {'name': 'USER_REMEMBER'}):
            form_data['USER_REMEMBER'] = 'Y'

        post_headers = {
            'Referer': resp.url,
            'Origin': BASE_URL,
        }
        resp2 = session.post(action, data=form_data, headers=post_headers, timeout=30, allow_redirects=True)
        
        if resp2.status_code != 200:
            return None, f"Ошибка HTTP {resp2.status_code}"

        soup2 = BeautifulSoup(resp2.text, 'html.parser')
        text_low = resp2.text.lower()
        url_low = (resp2.url or '').lower()

        site_err = _bitrix_alert_error_text(soup2)
        if site_err:
            hint = f"Сайт ответил: {site_err}"
            if re.search(r'капч|captcha', site_err, re.I):
                hint += " — вход с капчей программно не поддерживается; войдите в браузере или отключите капчу для опта."
            return None, hint

        if re.search(
            r'неверн\w*\s+(логин|пароль)|неверный\s+(логин|пароль)|ошибк\w*\s+авторизац'
            r'|login\s+failed|authentication\s+failed|wrong\s+password',
            text_low,
            re.I,
        ):
            return None, "Авторизация не удалась — проверьте логин/пароль"

        def _looks_logged_in():
            for a in soup2.find_all('a', href=True):
                h = (a.get('href') or '').lower()
                tx = (a.get_text() or '').strip().lower()
                if 'logout' in h or 'log-out' in h or 'action=logout' in h:
                    return True
                if tx in ('выход', 'выйти'):
                    return True
            if re.search(r'(cabinet|personal|profile|/lk/|/account/|/b2b/|/opt/)', url_low):
                return True
            # Редирект с страницы оплаты/логина на другой путь — часто признак входа
            if resp2.history and 'help/payment' in (resp2.history[0].url or '').lower():
                if 'help/payment' not in url_low and 'login' not in url_low:
                    return True
            pwd = soup2.find('input', {'type': 'password'})
            if not pwd:
                return True
            return False

        if _looks_logged_in():
            return session, "Авторизация успешна"
        return None, "Авторизация не удалась — проверьте логин/пароль"
            
    except requests.exceptions.RequestException as e:
        return None, f"Ошибка соединения: {e}"
    except Exception as e:
        return None, f"Ошибка: {e}"

def _absolute_url(href):
    if not href or href.startswith('#') or href.lower().startswith('javascript:'):
        return None
    href = href.strip()
    if href.startswith('//'):
        return 'https:' + href
    if href.startswith('/'):
        return BASE_URL.rstrip('/') + href
    if href.startswith('http'):
        return href
    return f'{BASE_URL.rstrip("/")}/{href.lstrip("/")}'


def _href_looks_like_price_file(href):
    if not href:
        return False
    h = href.lower()
    if re.search(r'\.(xlsx?|csv)(\?|#|$)', h, re.I):
        return True
    if re.search(r'(прайс|price|excel|xls|export|download|скачать|файл|персональн)', h, re.I):
        return True
    return False


def _link_looks_like_price(a_tag):
    href = a_tag.get('href') or ''
    blob = ' '.join([
        href,
        (a_tag.get_text() or ''),
        (a_tag.get('title') or ''),
        (a_tag.get('download') or ''),
    ]).lower()
    if _href_looks_like_price_file(href):
        return True
    if re.search(
        r'прайс|price\s*list|прайс-лист|excel|скачать\s+прайс|экспорт|выгрузк|xlsx|\.xls',
        blob,
        re.I,
    ):
        return True
    return False


def _collect_price_hrefs_from_soup(soup):
    seen = set()
    out = []
    for a in soup.find_all('a', href=True):
        if not _link_looks_like_price(a):
            continue
        abs_u = _absolute_url(a['href'])
        if abs_u and abs_u not in seen:
            seen.add(abs_u)
            out.append(abs_u)
    return out


def download_price_list(session):
    """Скачать прайс-лист (Excel/CSV): обход типичных страниц кабинета и ссылок."""
    try:
        pages_to_scan = [
            f'{BASE_URL}/',
            f'{BASE_URL}/catalog/',
            f'{BASE_URL}/cabinet/',
            f'{BASE_URL}/personal/',
            f'{BASE_URL}/personal/order/',
            f'{BASE_URL}/lk/',
            f'{BASE_URL}/profile/',
            f'{BASE_URL}/opt/',
            f'{BASE_URL}/help/payment/',
            f'{BASE_URL}/help/',
            f'{BASE_URL}/upload/',
            f'{BASE_URL}/info/',
            f'{BASE_URL}/company/',
        ]

        candidate_urls = []
        for page in pages_to_scan:
            try:
                resp = session.get(page, timeout=30)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, 'html.parser')
                candidate_urls.extend(_collect_price_hrefs_from_soup(soup))
            except requests.exceptions.RequestException:
                continue

        # Уникальные, с приоритетом прямых файлов
        seen = set()
        ordered = []
        for u in candidate_urls:
            if u in seen:
                continue
            seen.add(u)
            ordered.append(u)
        ordered.sort(key=lambda u: (0 if re.search(r'\.(xlsx?|csv)(\?|$)', u, re.I) else 1, u))

        for href in ordered:
            print(f"  Пробуем: {href}")
            try:
                resp2 = session.get(href, timeout=90, allow_redirects=True)
            except requests.exceptions.RequestException:
                continue
            if resp2.status_code != 200:
                continue
            ctype = (resp2.headers.get('Content-Type') or '').lower()
            data = resp2.content
            if len(data) < 500:
                continue
            # HTML вместо файла — пропускаем
            if 'text/html' in ctype and not re.search(r'\.(xlsx?|csv)(\?|$)', href, re.I):
                continue
            if 'text/html' in ctype and data[:200].strip().startswith(b'<'):
                continue

            filename = href.split('?')[0].rstrip('/').split('/')[-1] or 'price.xlsx'
            if not re.search(r'\.(xlsx?|csv)$', filename, re.I):
                cd = resp2.headers.get('Content-Disposition') or ''
                m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\n]+)', cd, re.I)
                if m:
                    filename = m.group(1).strip()
                elif 'spreadsheet' in ctype or 'excel' in ctype:
                    filename = 'price.xlsx'

            filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
            with open(filepath, 'wb') as f:
                f.write(data)
            print(f"  Сохранено: {filepath} ({len(data)} байт)")
            return filepath, filename, ""

        return None, None, (
            "Ссылка на прайс-лист не найдена на типичных страницах сайта. "
            "Войдите в кабинет в браузере и проверьте, где именно кнопка «Скачать прайс» — "
            "пришлите URL страницы, добавим его в список."
        )

    except Exception as e:
        return None, None, f"Ошибка скачивания: {e}"


def parse_price_list_dataframe(df):
    """
    Прайс Excel: находим колонки по заголовкам (наименование, артикул, розничная цена).
    Раньше брались только iloc[0:3] — при другом порядке колонок цены и артикулы оказывались перепутаны.
    """
    import pandas as pd
    import re

    def norm_cell(x):
        if pd.isna(x):
            return ''
        return str(x).strip()

    def norm_article_val(x):
        s = norm_cell(x)
        if re.fullmatch(r'\d+\.0', s):
            return s[:-2]
        return s

    def to_float(x):
        if pd.isna(x):
            return 0.0
        try:
            return float(str(x).replace(',', '.').replace(' ', '').replace('\xa0', ''))
        except ValueError:
            return 0.0

    cols = [norm_cell(c).lower() for c in df.columns]

    def col_find(pred):
        for i, h in enumerate(cols):
            if h and pred(h):
                return i
        return None

    def is_name_col(h):
        return any(
            k in h
            for k in (
                'наимен', 'аименование', 'именование', 'назв', 'nazwa', 'name',
                'товар', 'продукт', 'номенклат',
            )
        )

    def is_art_col(h):
        return (
            ('ртикул' in h)
            or ('артикул' in h)
            or h in ('арт.', 'код')
            or ('штрих' in h)
            or h == 'ean'
        )

    def is_price_col(h):
        if not h:
            return False
        if any(k in h for k in ('ррц', 'рознич', 'retail')):
            return True
        if any(k in h for k in ('опт', 'закуп', 'purchase', 'дилер', 'скид')):
            return False
        if any(k in h for k in ('цена', 'price', 'стоим')):
            return True
        return False

    i_name = col_find(is_name_col)
    i_art = col_find(is_art_col)
    i_price = col_find(is_price_col)

    if i_name is None or i_price is None:
        if len(df) > 0:
            row0 = [norm_cell(x).lower() for x in df.iloc[0].tolist()]

            def rfinder(pred):
                for j, h in enumerate(row0):
                    if h and pred(h):
                        return j
                return None

            r_name = rfinder(is_name_col)
            r_art = rfinder(is_art_col)
            r_price = rfinder(is_price_col)
            if r_name is not None and r_price is not None:
                i_name, i_art, i_price = r_name, r_art, r_price
                df = df.iloc[1:].reset_index(drop=True)

    out = []
    if i_name is not None and i_price is not None:
        for _, row in df.iterrows():
            vals = row.values
            n = len(vals)
            name = norm_cell(vals[i_name]) if i_name < n else ''
            article = (
                norm_article_val(vals[i_art])
                if i_art is not None and i_art >= 0 and i_art < n
                else ''
            )
            price = to_float(vals[i_price] if i_price < n else 0)
            if not name or len(name) < 3:
                continue
            ln = name.lower()
            if ln in ('наименование', 'название', 'итого', 'всего', 'nan') or ln.startswith('итого'):
                continue
            if price <= 0:
                continue
            art_u = article.upper().replace(' ', '') if article else ''
            out.append({'name': name, 'article': art_u, 'price': price})
        return out

    for _, row in df.iterrows():
        name = norm_cell(row.iloc[0]) if len(row) > 0 else ''
        article = norm_article_val(row.iloc[1]) if len(row) > 1 else ''
        price = to_float(row.iloc[2]) if len(row) > 2 else 0
        if not name or len(name) < 3:
            continue
        if price <= 0:
            continue
        art_u = article.upper().replace(' ', '') if article else ''
        out.append({'name': name, 'article': art_u, 'price': price})
    return out


def compare_prices(local_prices, new_prices):
    """Сравнить цены и вернуть разницу"""
    results = {
        'price_increased': [],  # Цена выросла
        'price_decreased': [],  # Цена упала
        'new_items': [],        # Новые товары
        'removed_items': [],    # Удалённые товары
        'total_compared': 0
    }
    
    # Создаём индексы по артикулу
    local_by_art = {}
    for item in local_prices:
        art = str(item.get('article', '')).strip().upper()
        if art:
            local_by_art[art] = item
    
    new_by_art = {}
    for item in new_prices:
        art = str(item.get('article', '')).strip().upper()
        if art:
            new_by_art[art] = item
    
    # Сравниваем
    for art, new_item in new_by_art.items():
        if art in local_by_art:
            local_item = local_by_art[art]
            old_price = float(local_item.get('retail_price', 0) or 0)
            new_price = float(new_item.get('price', 0) or 0)
            
            if old_price > 0 and new_price > 0 and abs(old_price - new_price) > 0.01:
                diff = new_price - old_price
                pct = (diff / old_price * 100) if old_price > 0 else 0
                entry = {
                    'article': art,
                    'name': new_item.get('name', local_item.get('name', '')),
                    'old_price': old_price,
                    'new_price': new_price,
                    'diff': round(diff, 2),
                    'pct': round(pct, 1)
                }
                if diff > 0:
                    results['price_increased'].append(entry)
                else:
                    results['price_decreased'].append(entry)
            results['total_compared'] += 1
        else:
            results['new_items'].append({
                'article': art,
                'name': new_item.get('name', ''),
                'price': new_item.get('price', 0)
            })
    
    # Находим удалённые
    for art, local_item in local_by_art.items():
        if art not in new_by_art:
            results['removed_items'].append({
                'article': art,
                'name': local_item.get('name', '')
            })
    
    # Сортируем по величине изменения
    results['price_increased'].sort(key=lambda x: x['pct'], reverse=True)
    results['price_decreased'].sort(key=lambda x: x['pct'])
    
    return results

def main():
    print("=" * 60)
    print("  Синхронизация цен с opt-akvabreg.by")
    print("=" * 60)
    
    # Авторизация
    print("\n[1] Авторизация...")
    session, msg = login()
    if not session:
        print(f"  ❌ {msg}")
        return
    print(f"  ✅ {msg}")
    
    # Скачивание прайса
    print("\n[2] Скачивание прайс-листа...")
    filepath, filename, msg = download_price_list(session)
    if not filepath:
        print(f"  ❌ {msg}")
        return
    print(f"  ✅ Прайс скачан: {filepath}")
    
    # Загрузка локальных цен
    print("\n[3] Загрузка локальных цен...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, name, article, retail_price, purchase_price FROM catalog_materials WHERE user_id = 1")
    local_items = [dict(r) for r in c.fetchall()]
    print(f"  Найдено {len(local_items)} товаров")
    conn.close()
    
    # Парсинг нового прайса
    print("\n[4] Парсинг нового прайса...")
    try:
        import pandas as pd
        df = pd.read_excel(filepath)
        new_items = parse_price_list_dataframe(df)
        print(f"  Распознано {len(new_items)} позиций")
    except Exception as e:
        print(f"  ⚠️ Ошибка парсинга Excel: {e}")
        print("  Попробуйте вручную импортировать через import_prices.py")
        return
    
    # Сравнение
    print("\n[5] Сравнение цен...")
    results = compare_prices(local_items, new_items)
    
    print(f"\n  Сравнено: {results['total_compared']} товаров")
    print(f"  ⬆️ Подорожало: {len(results['price_increased'])}")
    print(f"  ⬇️ Подешевело: {len(results['price_decreased'])}")
    print(f"  🆕 Новые: {len(results['new_items'])}")
    print(f"  🗑️ Удалённые: {len(results['removed_items'])}")
    
    if results['price_increased']:
        print(f"\n  === Подорожало (топ-10): ===")
        for item in results['price_increased'][:10]:
            print(f"    {item['name'][:40]}: {item['old_price']} → {item['new_price']} ({item['pct']}%)")
    
    if results['price_decreased']:
        print(f"\n  === Подешевело (топ-10): ===")
        for item in results['price_decreased'][:10]:
            print(f"    {item['name'][:40]}: {item['old_price']} → {item['new_price']} ({item['pct']}%)")
    
    print("\n  ✅ Готово!")

if __name__ == '__main__':
    main()
