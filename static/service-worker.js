const CACHE_NAME = 'accounting-v7';
const OFFLINE_URL = '/offline';

/** Повтор при обрыве сети (холодный старт хостинга, мобильный интернет). */
function sleep(ms) {
  return new Promise(function(resolve) { setTimeout(resolve, ms); });
}
function fetchWithRetry(request, attemptsLeft) {
  attemptsLeft = attemptsLeft || 3;
  return fetch(request).catch(function() {
    if (attemptsLeft <= 1) return Promise.reject(new Error('network'));
    return sleep(700).then(function() {
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

// Запрос — сначала кэш, потом сеть
self.addEventListener('fetch', function(event) {
  // Пропускаем запросы расширений браузера и внутренние
  if (!event.request.url.startsWith('http://') && !event.request.url.startsWith('https://')) {
    return;
  }
  // API запросы — только сеть (POST/PUT/DELETE не кэшируются)
  if (event.request.url.includes('/api/')) {
    // Каталог материалов/работ — без кэша, чтобы цены (розница/опт) всегда с сервера
    if (/\/estimate\/api\/catalog\//.test(event.request.url) && event.request.method === 'GET') {
      event.respondWith(fetch(event.request));
      return;
    }
    event.respondWith(
      fetch(event.request).then(function(response) {
        // Кэшируем только успешные GET ответы
        if (event.request.method === 'GET' && response.ok) {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function(cache) {
            cache.put(event.request, clone);
          });
        }
        return response;
      }).catch(function() {
        // При ошибке сети — пробуем кэш (только GET)
        if (event.request.method === 'GET') {
          return caches.match(event.request).then(function(cached) {
            if (cached) return cached;
            // Кэша нет — возвращаем Response с ошибкой
            return new Response(JSON.stringify({ error: 'Нет соединения с сервером' }), {
              status: 503,
              headers: { 'Content-Type': 'application/json' }
            });
          });
        }
        // POST/PUT/DELETE — всегда ошибка сети, кэша нет
        return new Response(JSON.stringify({ error: 'Нет соединения с сервером' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' }
        });
      })
    );
    return;
  }

  // HTML — ВСЕГДА сеть (не кэшируем страницы!)
  var accept = event.request.headers.get('Accept') || '';
  if (accept.includes('text/html')) {
    event.respondWith(
      fetchWithRetry(event.request).catch(function() {
        return caches.match('/offline');
      })
    );
    return;
  }

  // Статика — сначала кэш, потом сеть (с повторами: иначе браузер показывал «408 Offline»)
  event.respondWith(
    caches.match(event.request).then(function(cached) {
      if (cached) return cached;
      return fetchWithRetry(event.request).then(function(response) {
        if (response.ok && event.request.method === 'GET') {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function(cache) {
            cache.put(event.request, clone);
          });
        }
        return response;
      });
    }).catch(function() {
      return new Response('', { status: 503, statusText: 'Network Unavailable' });
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
