const CACHE_NAME = 'accounting-v9';
const OFFLINE_URL = '/offline';

/** Повтор при обрыве сети (холодный старт хостинга, мобильный интернет). */
function sleep(ms) {
  return new Promise(function(resolve) { setTimeout(resolve, ms); });
}
function fetchWithRetry(request, attemptsLeft) {
  attemptsLeft = attemptsLeft || 5;
  return fetch(request).catch(function() {
    if (attemptsLeft <= 1) return Promise.reject(new Error('network'));
    return sleep(900).then(function() {
      return fetchWithRetry(request, attemptsLeft - 1);
    });
  });
}

// Файлы для кэширования при установке — ТОЛЬКО СТАТИКА
const PRECACHE_URLS = [
  '/static/js/utils.js',
  '/static/manifest.json',
  '/static/icons/icon.svg'
];

// Установка — кэшируем основные файлы
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(PRECACHE_URLS).catch(function(err) {
        console.log('Service Worker: не удалось кэшировать', err);
      });
    })
  );
  self.skipWaiting();
});

// Активация — удаляем старые кэши
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(
        names.filter(function(n) { return n !== CACHE_NAME; })
             .map(function(n) { return caches.delete(n); })
      );
    })
  );
  self.clients.claim();
});

// Запросы: API без кэша; навигация — сеть; статика — сеть с запасным кэшем.
self.addEventListener('fetch', function(event) {
  // Пропускаем запросы расширений браузера и внутренние
  if (!event.request.url.startsWith('http://') && !event.request.url.startsWith('https://')) {
    return;
  }
  // API — только сеть, без Cache Storage: иначе при обрыве отдавались устаревшие JSON (неверные суммы / списки).
  if (event.request.url.includes('/api/')) {
    // Каталог материалов/работ — без кэша, чтобы цены (розница/опт) всегда с сервера
    if (/\/estimate\/api\/catalog\//.test(event.request.url) && event.request.method === 'GET') {
      event.respondWith(fetchWithRetry(event.request));
      return;
    }
    event.respondWith(
      (event.request.method === 'GET' ? fetchWithRetry(event.request) : fetch(event.request)).catch(
        function() {
          if (event.request.method === 'GET') {
            return new Response(JSON.stringify({ error: 'Нет соединения с сервером' }), {
              status: 503,
              headers: { 'Content-Type': 'application/json' }
            });
          }
          return new Response(JSON.stringify({ error: 'Нет соединения с сервером' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
          });
        }
      )
    );
    return;
  }

  // Только реальная навигация вкладки (не XHR с Accept: text/html — иначе уходило в «статику» и 503).
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetchWithRetry(event.request).catch(function() {
        return caches.match(OFFLINE_URL).then(function(cached) {
          if (cached) return cached;
          return new Response(
            '<!DOCTYPE html><meta charset="utf-8"><title>Нет сети</title><p>Нет соединения с сервером. Обновите страницу.</p>',
            { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          );
        });
      })
    );
    return;
  }

  // Статика — сначала сеть, потом кэш (холодный старт Railway: кэш-first давал пустой кэш и 503).
  event.respondWith(
    fetchWithRetry(event.request).then(function(response) {
      if (response.ok && event.request.method === 'GET') {
        var clone = response.clone();
        caches.open(CACHE_NAME).then(function(cache) {
          cache.put(event.request, clone);
        });
      }
      return response;
    }).catch(function() {
      return caches.match(event.request).then(function(cached) {
        if (cached) return cached;
        return new Response('/* offline */', {
          status: 503,
          headers: { 'Content-Type': 'text/plain; charset=utf-8' }
        });
      });
    })
  );
});

// Фоновая синхронизация
self.addEventListener('sync', function(event) {
  if (event.tag === 'sync-data') {
    event.waitUntil(syncWithServer());
  }
});

async function syncWithServer() {
  // Синхронизация отложенных действий — используем ту же БД, что и offline.js (AccountingDB)
  const db = await openAccountingDB();
  const tx = db.transaction('syncQueue', 'readonly');
  const store = tx.objectStore('syncQueue');
  const items = await store.getAll();

  for (const item of items) {
    try {
      await fetch(item.url, {
        method: item.method,
        headers: item.headers,
        body: item.body
      });
      // Успешно — удаляем из очереди
      const tx2 = db.transaction('syncQueue', 'readwrite');
      tx2.objectStore('syncQueue').delete(item.id);
    } catch(e) {
      console.log('Sync failed for item:', item.id, e);
    }
  }
}

function openAccountingDB() {
  return new Promise(function(resolve, reject) {
    const req = indexedDB.open('AccountingDB', 3);
    req.onupgradeneeded = function() {
      if (!req.result.objectStoreNames.contains('syncQueue')) {
        req.result.createObjectStore('syncQueue', { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = function() { resolve(req.result); };
    req.onerror = function() { reject(req.error); };
  });
}
