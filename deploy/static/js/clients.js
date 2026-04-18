/**
 * clients.js — страница клиентов (API + offline-кэш)
 */
var ClientsPage = (function(API, UI) {
    'use strict';

    var clients = [];
    var objects = [];

    function renderTable() {
        var searchEl = document.getElementById('search');
        var search = (searchEl && searchEl.value || '').toLowerCase();
        var filtered = clients.filter(function(c) {
            return String(c.id).indexOf(search) !== -1 ||
                (c.name || '').toLowerCase().indexOf(search) !== -1 ||
                (c.phone && String(c.phone).indexOf(search) !== -1) ||
                (c.email && (c.email || '').toLowerCase().indexOf(search) !== -1);
        });
        var tbody = document.getElementById('table-body');
        if (!tbody) return;

        var statTotal = document.getElementById('stat-total');
        var statObjects = document.getElementById('stat-objects');
        if (statTotal) statTotal.textContent = String(clients.length);
        if (statObjects) statObjects.textContent = String(objects.length);

        if (filtered.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="padding:30px;text-align:center;color:#999;">Нет клиентов</td></tr>';
            return;
        }

        tbody.innerHTML = filtered.map(function(client) {
            return '<tr>' +
                '<td>' + API.esc(String(client.id)) + '</td>' +
                '<td style="text-align:left;font-weight:600;">' + API.esc(client.name) + '</td>' +
                '<td>' + API.esc(client.phone || '-') + '</td>' +
                '<td>' + API.esc(client.email || '-') + '</td>' +
                '<td style="text-align:left;">' + API.esc(client.address || '-') + '</td>' +
                '<td>' +
                    '<button class="btn btn-edit" onclick="ClientsPage.editClient(' + client.id + ')">\u270f\ufe0f</button> ' +
                    '<button class="btn btn-danger" onclick="ClientsPage.deleteClient(' + client.id + ')">\ud83d\uddd1\ufe0f</button>' +
                '</td>' +
            '</tr>';
        }).join('');
    }

    function loadData() {
        API.get('/api/clients')
            .then(function(data) {
                clients = Array.isArray(data) ? data : [];
                try {
                    if (typeof OfflineDB !== 'undefined') OfflineDB.bulkPut('clients', clients);
                } catch (e) {}
                return API.get('/api/objects-with-estimates').catch(function() { return { objects: [] }; });
            })
            .then(function(oData) {
                objects = (oData && oData.objects) ? oData.objects : [];
                renderTable();
            })
            .catch(function(e) {
                console.error('ClientsPage loadData:', e);
                if (typeof OfflineDB === 'undefined') {
                    var tb = document.getElementById('table-body');
                    if (tb) tb.innerHTML = '<tr><td colspan="6" style="padding:30px;text-align:center;color:red;">\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438</td></tr>';
                    return;
                }
                OfflineDB.getAll('clients')
                    .then(function(c) {
                        clients = c || [];
                        return OfflineDB.getAll('objects');
                    })
                    .then(function(o) {
                        objects = o || [];
                        renderTable();
                    })
                    .catch(function() {
                        var tb = document.getElementById('table-body');
                        if (tb) tb.innerHTML = '<tr><td colspan="6" style="padding:30px;text-align:center;color:red;">\u041d\u0435\u0442 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u044f</td></tr>';
                    });
            });
    }

    function openModal(id) {
        var modal = document.getElementById('modal');
        if (modal) modal.classList.add('active');
        var title = document.getElementById('modal-title');
        if (title) title.textContent = id ? '\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c' : '\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c';
        var cid = document.getElementById('client-id');
        if (cid) cid.value = '';
        var form = document.getElementById('client-form');
        if (form) form.reset();
        if (id) {
            var c = clients.find(function(x) { return x.id === id; });
            if (c && cid) {
                cid.value = c.id;
                var n = document.getElementById('name');
                var p = document.getElementById('phone');
                var em = document.getElementById('email');
                var ad = document.getElementById('address');
                if (n) n.value = c.name || '';
                if (p) p.value = c.phone || '';
                if (em) em.value = c.email || '';
                if (ad) ad.value = c.address || '';
            }
        }
    }

    function closeModal() {
        var modal = document.getElementById('modal');
        if (modal) modal.classList.remove('active');
    }

    function editClient(id) {
        openModal(id);
    }

    function deleteClient(id) {
        if (!UI.confirm('\u0423\u0434\u0430\u043b\u0438\u0442\u044c?')) return;

        API.del('/api/clients/' + id)
            .then(function() {
                try {
                    if (typeof OfflineDB !== 'undefined') OfflineDB.delete('clients', id);
                } catch (e) {}
                loadData();
            })
            .catch(function() {
                if (typeof OfflineDB === 'undefined') {
                    UI.showToast('\u041e\u0448\u0438\u0431\u043a\u0430 \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f', 'error');
                    return;
                }
                OfflineDB.addToQueue({ method: 'DELETE', url: '/api/clients/' + id })
                    .then(function() { return OfflineDB.delete('clients', id); })
                    .then(function() { loadData(); })
                    .catch(function() { UI.showToast('\u041e\u0448\u0438\u0431\u043a\u0430 \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f', 'error'); });
            });
    }

    function saveClient(e) {
        if (e) e.preventDefault();
        var idEl = document.getElementById('client-id');
        var id = idEl ? idEl.value : '';
        var data = {
            name: (document.getElementById('name') || {}).value || '',
            phone: (document.getElementById('phone') || {}).value || '',
            email: (document.getElementById('email') || {}).value || '',
            address: (document.getElementById('address') || {}).value || ''
        };
        if (!String(data.name).trim()) {
            UI.showToast('\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0438\u043c\u044f', 'error');
            return;
        }

        var req = id
            ? API.put('/api/clients/' + id, data)
            : API.post('/api/clients', data);

        req.then(function(saved) {
            try {
                if (typeof OfflineDB !== 'undefined' && saved && saved.id) OfflineDB.put('clients', saved);
            } catch (err) {}
            closeModal();
            loadData();
        }).catch(function() {
            if (typeof OfflineDB === 'undefined') {
                UI.showToast('\u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f', 'error');
                return;
            }
            var action = { method: id ? 'PUT' : 'POST', url: id ? '/api/clients/' + id : '/api/clients', body: data };
            OfflineDB.addToQueue(action)
                .then(function() {
                    if (!id) return null;
                    return OfflineDB.getById('clients', parseInt(id, 10));
                })
                .then(function(existing) {
                    if (existing && id) return OfflineDB.put('clients', Object.assign({}, existing, data));
                })
                .then(function() {
                    closeModal();
                    loadData();
                    UI.showToast('\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u043e. \u0421\u0438\u043d\u0445\u0440\u043e\u043d\u0438\u0437\u0438\u0440\u0443\u0439\u0442\u0435 \u043f\u0440\u0438 \u0441\u0435\u0442\u0438.', 'info');
                })
                .catch(function() { UI.showToast('\u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f', 'error'); });
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        var form = document.getElementById('client-form');
        if (form) form.addEventListener('submit', saveClient);
        var search = document.getElementById('search');
        if (search) search.addEventListener('input', renderTable);
        var modal = document.getElementById('modal');
        if (modal) {
            modal.addEventListener('click', function(e) {
                if (e.target.id === 'modal') closeModal();
            });
        }
        loadData();
    });

    return {
        loadData: loadData,
        renderTable: renderTable,
        openModal: openModal,
        closeModal: closeModal,
        editClient: editClient,
        deleteClient: deleteClient,
        saveClient: saveClient
    };
})(API, UI);

window.openModal = function(id) { ClientsPage.openModal(id); };
window.closeModal = function() { ClientsPage.closeModal(); };
