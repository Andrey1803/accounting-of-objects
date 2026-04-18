/**
 * Offline Storage v4 — Полный офлайн-режим для всех страниц
 * Кэширует объекты, клиентов, сметы, каталог и синхронизирует с сервером
 * v4: Объединение с offline-db.js — добавлена совместимость с EstimateDB
 *     и миграция данных из старой БД.
 */

var OfflineDB = (function() {
    var DB_NAME = 'AccountingDB';
    var DB_VERSION = 4;  // v4: добавлена миграция из EstimateDB
    var db = null;

    // ================================================================
    // Миграция из EstimateDB (old offline-db.js) → AccountingDB
    // ================================================================
    function migrateFromEstimateDB() {
        return new Promise(function(resolve) {
            try {
                var req = indexedDB.open('EstimateDB', 2);
                req.onsuccess = function() {
                    var oldDB = req.result;
                    var stores = oldDB.objectStoreNames;

                    // Мигрируем estimates
                    if (stores.contains('estimates')) {
                        var tx = oldDB.transaction('estimates', 'readonly');
                        var store = tx.objectStore('estimates');
                        var getAll = store.getAll();
                        getAll.onsuccess = function() {
                            var items = getAll.result || [];
                            if (items.length > 0) {
                                console.log('Migrating ' + items.length + ' estimates from EstimateDB...');
                                open().then(function(newDB) {
                                    var newTx = newDB.transaction('estimates', 'readwrite');
                                    var newStore = newTx.objectStore('estimates');
                                    items.forEach(function(item) { newStore.put(item); });
                                    newTx.oncomplete = function() {
                                        console.log('✅ Estimates migrated');
                                    };
                                });
                            }
                        };
                    }

                    // Мигрируем sync_queue → syncQueue
                    if (stores.contains('sync_queue')) {
                        var tx2 = oldDB.transaction('sync_queue', 'readonly');
                        var store2 = tx2.objectStore('sync_queue');
                        var getAll2 = store2.getAll();
                        getAll2.onsuccess = function() {
                            var items = getAll2.result || [];
                            if (items.length > 0) {
                                console.log('Migrating ' + items.length + ' sync items from EstimateDB...');
                                open().then(function(newDB) {
                                    var newTx = newDB.transaction('syncQueue', 'readwrite');
                                    var newStore = newTx.objectStore('syncQueue');
                                    items.forEach(function(item) {
                                        // Приводим формат: status → status (совместим)
                                        delete item.id; // autoIncrement пересоздастся
                                        newStore.add(item);
                                    });
                                    newTx.oncomplete = function() {
                                        console.log('✅ Sync queue migrated');
                                    };
                                });
                            }
                        };
                    }

                    oldDB.close();
                    // Удаляем старую БД
                    setTimeout(function() {
                        indexedDB.deleteDatabase('EstimateDB');
                        console.log('✅ EstimateDB deleted');
                    }, 1000);
                    resolve();
                };
                req.onerror = function() {
                    // EstimateDB нет или ошибка — просто продолжаем
                    resolve();
                };
                req.onblocked = function() {
                    resolve();
                };
            } catch(e) {
                // EstimateDB не существует
                resolve();
            }
        });
    }

    function open() {
        return new Promise(function(resolve, reject) {
            if (db) { resolve(db); return; }
            var req = indexedDB.open(DB_NAME, DB_VERSION);
            req.onupgradeneeded = function(e) {
                var d = e.target.result;
                if (!d.objectStoreNames.contains('objects'))
                    d.createObjectStore('objects', { keyPath: 'id' });
                if (!d.objectStoreNames.contains('clients'))
                    d.createObjectStore('clients', { keyPath: 'id' });
                if (!d.objectStoreNames.contains('estimates'))
                    d.createObjectStore('estimates', { keyPath: 'id' });
                if (!d.objectStoreNames.contains('catalog_materials'))
                    d.createObjectStore('catalog_materials', { keyPath: 'id' });
                if (!d.objectStoreNames.contains('catalog_works'))
                    d.createObjectStore('catalog_works', { keyPath: 'id' });
                if (!d.objectStoreNames.contains('syncQueue'))
                    d.createObjectStore('syncQueue', { keyPath: 'id', autoIncrement: true });
                if (!d.objectStoreNames.contains('settings'))
                    d.createObjectStore('settings', { keyPath: 'key' });
            };
            req.onsuccess = function() { db = req.result; resolve(db); };
            req.onerror = function() { reject(req.error); };
        });
    }

    // ================================================================
    // Публичный API (совместимый с offline.js v3 и offline-db.js)
    // ================================================================

    /**
     * init() — алиас для open(), совместимость с offline-db.js
     */
    function init() {
        return open();
    }

    // === CRUD ===
    function getAll(storeName) {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction(storeName, 'readonly');
                var req = tx.objectStore(storeName).getAll();
                req.onsuccess = function() { resolve(req.result || []); };
                req.onerror = function() { reject(req.error); };
            });
        });
    }

    function getById(storeName, id) {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction(storeName, 'readonly');
                var req = tx.objectStore(storeName).get(id);
                req.onsuccess = function() { resolve(req.result); };
                req.onerror = function() { reject(req.error); };
            });
        });
    }

    function put(storeName, data) {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction(storeName, 'readwrite');
                tx.objectStore(storeName).put(data);
                tx.oncomplete = function() { resolve(data); };
                tx.onerror = function() { reject(tx.error); };
            });
        });
    }

    function bulkPut(storeName, items) {
        items = items || [];
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction(storeName, 'readwrite');
                var store = tx.objectStore(storeName);
                store.clear();
                items.forEach(function(item) { store.put(item); });
                tx.oncomplete = function() { resolve(items.length); };
                tx.onerror = function() { reject(tx.error); };
            });
        });
    }

    function deleteItem(storeName, id) {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction(storeName, 'readwrite');
                tx.objectStore(storeName).delete(id);
                tx.oncomplete = function() { resolve(); };
                tx.onerror = function() { reject(tx.error); };
            });
        });
    }

    function clear(storeName) {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction(storeName, 'readwrite');
                tx.objectStore(storeName).clear();
                tx.oncomplete = function() { resolve(); };
                tx.onerror = function() { reject(tx.error); };
            });
        });
    }

    // === Очередь синхронизации ===
    function addToQueue(action) {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction('syncQueue', 'readwrite');
                var item = {
                    action: action,
                    timestamp: Date.now(),
                    status: 'pending',
                    retries: 0
                };
                tx.objectStore('syncQueue').add(item);
                tx.oncomplete = function() {
                    console.log('Queued:', action.method, action.url);
                    resolve(item);
                    if (navigator.onLine) trySync();
                };
                tx.onerror = function() { reject(tx.error); };
            });
        });
    }

    function getPendingQueue() {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction('syncQueue', 'readonly');
                var req = tx.objectStore('syncQueue').getAll();
                req.onsuccess = function() {
                    var items = (req.result || []).filter(function(i) { return i.status === 'pending'; });
                    resolve(items);
                };
                req.onerror = function() { reject(req.error); };
            });
        });
    }

    function markSynced(id) {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction('syncQueue', 'readwrite');
                tx.objectStore('syncQueue').delete(id);
                tx.oncomplete = function() { resolve(); };
                tx.onerror = function() { reject(tx.error); };
            });
        });
    }

    function getQueueCount() {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction('syncQueue', 'readonly');
                var req = tx.objectStore('syncQueue').count();
                req.onsuccess = function() { resolve(req.result || 0); };
                req.onerror = function() { reject(req.error); };
            });
        });
    }

    // === Синхронизация ===
    function trySync() {
        if (!navigator.onLine) return;

        getPendingQueue().then(function(items) {
            if (items.length === 0) return;

            var csrfMeta = document.querySelector('meta[name="csrf-token"]');
            var csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';
            var idx = 0;

            function syncNext() {
                if (idx >= items.length) return;
                var item = items[idx];
                idx++;

                var fetchOpts = {
                    method: item.action.method,
                    headers: { 'Content-Type': 'application/json' }
                };
                if (csrfToken) fetchOpts.headers['X-CSRF-Token'] = csrfToken;
                if (item.action.body) fetchOpts.body = JSON.stringify(item.action.body);

                fetch(item.action.url, fetchOpts)
                    .then(function(res) {
                        if (res.ok) {
                            markSynced(item.id).then(syncNext);
                        } else {
                            console.log('Sync failed:', item.action.url, res.status);
                            syncNext();
                        }
                    })
                    .catch(function(err) {
                        console.log('Sync error:', err);
                        syncNext();
                    });
            }
            syncNext();
        }).catch(function(err) {
            console.log('Queue error:', err);
        });
    }

    // === Утилиты ===
    function isOnline() { return navigator.onLine; }

    function saveSetting(key, value) {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction('settings', 'readwrite');
                tx.objectStore('settings').put({ key: key, value: value });
                tx.oncomplete = function() { resolve(); };
                tx.onerror = function() { reject(tx.error); };
            });
        });
    }

    function getSetting(key) {
        return open().then(function(d) {
            return new Promise(function(resolve, reject) {
                var tx = d.transaction('settings', 'readonly');
                var req = tx.objectStore('settings').get(key);
                req.onsuccess = function() { resolve(req.result ? req.result.value : null); };
                req.onerror = function() { reject(req.error); };
            });
        });
    }

    return {
        // Основной API (offline.js v3)
        open: open,
        getAll: getAll,
        getById: getById,
        put: put,
        bulkPut: bulkPut,
        delete: deleteItem,
        clear: clear,
        addToQueue: addToQueue,
        getQueueCount: getQueueCount,
        trySync: trySync,
        isOnline: isOnline,
        saveSetting: saveSetting,
        getSetting: getSetting,
        // Совместимость с offline-db.js
        init: init,
        migrateFromEstimateDB: migrateFromEstimateDB
    };
})();

// Инициализация при загрузке + миграция
OfflineDB.open().then(function() {
    console.log('✅ AccountingDB initialized');
    // Проверяем наличие EstimateDB и мигрируем данные
    OfflineDB.migrateFromEstimateDB ? OfflineDB.migrateFromEstimateDB() : Promise.resolve();
}).catch(function(e) {
    console.error('OfflineDB init failed:', e);
});

// Мониторинг подключения к сети
window.addEventListener('online', () => {
    console.log('🟢 Online - syncing...');
    OfflineDB.trySync().then(() => {
        console.log('✅ Sync complete');
        if (window.onSyncComplete) window.onSyncComplete();
    });
});

window.addEventListener('offline', () => {
    console.log('🔴 Offline - changes will be queued');
});
