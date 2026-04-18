/**
 * api.js — Обёртка над fetch, CSRF, обработка ошибок
 */
var API = (function() {
    'use strict';

    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function request(url, options) {
        options = options || {};
        var headers = options.headers || {};
        headers['X-CSRF-Token'] = getCsrfToken();
        if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
            headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(options.body);
        }
        options.headers = headers;

        return fetch(url, options).then(function(res) {
            if (res.ok) return res;
            if (res.status === 403) {
                return fetch('/api/csrf-token')
                    .then(function(csrfRes) {
                        if (!csrfRes.ok) throw new Error('CSRF refresh failed');
                        return csrfRes.json();
                    })
                    .then(function(data) {
                        var meta = document.querySelector('meta[name="csrf-token"]');
                        if (meta) meta.setAttribute('content', data.csrf_token);
                        var retry = Object.assign({}, options);
                        retry.headers = Object.assign({}, options.headers, { 'X-CSRF-Token': data.csrf_token });
                        return fetch(url, retry);
                    });
            }
            return res;
        });
    }

    function parseJson(res) {
        if (!res.ok) {
            return res.text().then(function(text) {
                var msg = 'HTTP ' + res.status;
                try {
                    var j = JSON.parse(text || '{}');
                    if (j.error) msg = j.error;
                    else if (j.message) msg = j.message;
                } catch (e) {}
                throw new Error(msg);
            });
        }
        return res.text().then(function(text) {
            if (!text || !String(text).trim()) return {};
            return JSON.parse(text);
        });
    }

    function get(url) {
        return request(url, { method: 'GET' }).then(parseJson);
    }

    function post(url, data) {
        return request(url, { method: 'POST', body: data }).then(parseJson);
    }

    function put(url, data) {
        return request(url, { method: 'PUT', body: data }).then(parseJson);
    }

    function del(url) {
        return request(url, { method: 'DELETE' }).then(parseJson);
    }

    function esc(str) {
        if (str === null || str === undefined) return '';
        var div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }

    function formatMoney(num) {
        return Number(num || 0).toLocaleString('ru-RU', {
            style: 'currency',
            currency: 'BYN',
            minimumFractionDigits: 2
        });
    }

    function formatDate(dateStr) {
        if (!dateStr) return '';
        try {
            var d = new Date(dateStr);
            return d.toLocaleDateString('ru-RU');
        } catch (e) {
            return dateStr;
        }
    }

    return {
        get: get,
        post: post,
        put: put,
        del: del,
        request: request,
        getCsrfToken: getCsrfToken,
        esc: esc,
        formatMoney: formatMoney,
        formatDate: formatDate
    };
})();
