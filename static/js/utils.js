/**
 * Общие JS-утилиты
 */

/**
 * Получить CSRF токен из мета-тега или cookie
 */
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

/**
 * Выполнить fetch с CSRF-токеном
 */
function fetchCsrf(url, options = {}) {
    const headers = { ...options.headers };
    headers['X-CSRF-Token'] = getCsrfToken();
    return fetch(url, { ...options, headers });
}

/**
 * Экранирование HTML для защиты от XSS
 */
function esc(str) {
    if (str === null || str === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

/**
 * Форматирование числа как деньги
 */
function formatMoney(num) {
    return Number(num || 0).toLocaleString('ru-RU', { style: 'currency', currency: 'BYN', minimumFractionDigits: 2 });
}

/**
 * Форматирование даты
 */
function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
        const d = new Date(dateStr);
        return d.toLocaleDateString('ru-RU');
    } catch {
        return dateStr;
    }
}

/**
 * Безопасная вставка текста в элемент
 */
function safeText(el, text) {
    el.textContent = text;
}

