/**
 * workers.js — страница рабочих (API + UI + подотчёт)
 */
var WorkersPage = (function(API, UI) {
    'use strict';

    var workers = [];
    var balanceByWorker = {};
    var cashbookWorkerId = null;
    var objectsCache = null;

    var KIND_LABELS = {
        client_payment: 'Получено от клиента',
        expense: 'Расход',
        handover: 'Сдано вам'
    };

    var EXP_LABELS = {
        lunch: 'Обед',
        fuel: 'Топливо',
        repair: 'Ремонт',
        other: 'Прочее'
    };

    function isActive(w) {
        return w.is_active === 1 || w.is_active === true;
    }

    function opLabel(entry) {
        var base = KIND_LABELS[entry.entry_kind] || entry.entry_kind;
        if (entry.entry_kind === 'expense' && entry.expense_category) {
            base += ' · ' + (EXP_LABELS[entry.expense_category] || entry.expense_category);
        }
        return base;
    }

    function render() {
        var tbody = document.getElementById('workersTable');
        if (!tbody) return;

        var total = workers.length;
        var active = workers.filter(isActive).length;

        var statTotal = document.getElementById('statTotal');
        var statActive = document.getElementById('statActive');
        if (statTotal) statTotal.textContent = String(total);
        if (statActive) statActive.textContent = String(active);

        if (workers.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#999;padding:30px;">\u041d\u0435\u0442 \u0440\u0430\u0431\u043e\u0447\u0438\u0445. \u041d\u0430\u0436\u043c\u0438\u0442\u0435 &quot;\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0440\u0430\u0431\u043e\u0447\u0435\u0433\u043e&quot;</td></tr>';
            return;
        }

        tbody.innerHTML = workers.map(function(w) {
            var bal = balanceByWorker[w.id];
            if (bal === undefined || bal === null) bal = 0;
            var balHtml = '<span style="font-weight:600;color:' + (bal > 0 ? '#2E75B6' : (bal < 0 ? '#c62828' : '#888')) + ';">' + API.formatMoney(bal) + '</span>';
            return '<tr>' +
                '<td><strong>' + API.esc(w.full_name) + '</strong></td>' +
                '<td>' + (API.esc(w.phone) || '\u2014') + '</td>' +
                '<td><strong>' + API.formatMoney(w.daily_rate) + '</strong></td>' +
                '<td>' + (w.hire_date || '\u2014') + '</td>' +
                '<td>' + balHtml + '</td>' +
                '<td>' + (isActive(w)
                    ? '<span class="active-badge">\u0410\u043a\u0442\u0438\u0432\u0435\u043d</span>'
                    : '<span class="inactive-badge">\u041d\u0435 \u0430\u043a\u0442\u0438\u0432\u0435\u043d</span>') + '</td>' +
                '<td>' +
                    '<button type="button" class="btn" onclick="openCashbook(' + w.id + ')" style="padding:4px 8px;font-size:11px;background:#E3F2FD;color:#1565C0;" title="\u041a\u043d\u0438\u0433\u0430 \u043f\u043e\u0434\u043e\u0442\u0447\u0451\u0442\u0430">\ud83d\udcd2</button> ' +
                    '<button class="btn btn-edit" onclick="editWorker(' + w.id + ')" style="padding:4px 8px;font-size:11px;">\u270f\ufe0f</button> ' +
                    '<button class="btn btn-del" onclick="deleteWorker(' + w.id + ')" style="padding:4px 8px;font-size:11px;">\ud83d\uddd1\ufe0f</button>' +
                '</td>' +
            '</tr>';
        }).join('');
    }

    function loadBalances() {
        return API.get('/api/workers/cashbook-balances')
            .then(function(data) {
                balanceByWorker = {};
                if (Array.isArray(data)) {
                    data.forEach(function(b) {
                        balanceByWorker[b.worker_id] = b.balance;
                    });
                }
            })
            .catch(function() {
                balanceByWorker = {};
            });
    }

    function loadWorkers() {
        Promise.all([
            API.get('/api/workers'),
            loadBalances()
        ])
            .then(function(results) {
                workers = Array.isArray(results[0]) ? results[0] : [];
                render();
            })
            .catch(function() {
                var tbody = document.getElementById('workersTable');
                if (tbody) {
                    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#999;padding:30px;">\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438</td></tr>';
                }
            });
    }

    function syncCbCategoryVisibility() {
        var kind = document.getElementById('cb-kind');
        var row = document.getElementById('cb-cat-row');
        if (!kind || !row) return;
        row.style.display = kind.value === 'expense' ? 'block' : 'none';
    }

    function loadObjectsSelect() {
        var sel = document.getElementById('cb-object');
        if (!sel) return;
        if (objectsCache) {
            fillObjectSelect(sel, objectsCache);
            return Promise.resolve();
        }
        return API.get('/api/objects')
            .then(function(list) {
                objectsCache = Array.isArray(list) ? list : [];
                fillObjectSelect(sel, objectsCache);
            })
            .catch(function() {
                objectsCache = [];
                fillObjectSelect(sel, []);
            });
    }

    function fillObjectSelect(sel, list) {
        var cur = sel.value;
        sel.innerHTML = '<option value="">\u2014 \u043d\u0435 \u043f\u0440\u0438\u0432\u044f\u0437\u0430\u043d\u043e \u2014</option>';
        list.forEach(function(o) {
            var opt = document.createElement('option');
            opt.value = String(o.id);
            opt.textContent = (o.name || ('#' + o.id)) + (o.client ? ' — ' + o.client : '');
            sel.appendChild(opt);
        });
        if (cur) sel.value = cur;
    }

    function openCashbook(workerId) {
        cashbookWorkerId = workerId;
        var w = workers.find(function(x) { return x.id === workerId; });
        var title = document.getElementById('cashbookTitle');
        if (title) title.textContent = w ? ('\u041f\u043e\u0434\u043e\u0442\u0447\u0451\u0442: ' + w.full_name) : '\u041f\u043e\u0434\u043e\u0442\u0447\u0451\u0442';

        var today = new Date().toISOString().slice(0, 10);
        var dEl = document.getElementById('cb-date');
        if (dEl) dEl.value = today;
        var amt = document.getElementById('cb-amount');
        if (amt) amt.value = '';
        var note = document.getElementById('cb-note');
        if (note) note.value = '';
        var kind = document.getElementById('cb-kind');
        if (kind) kind.value = 'client_payment';
        syncCbCategoryVisibility();

        var modal = document.getElementById('cashbookModal');
        if (modal) modal.classList.add('active');

        loadObjectsSelect().then(function() { return refreshCashbook(); });
    }

    function closeCashbook() {
        var modal = document.getElementById('cashbookModal');
        if (modal) modal.classList.remove('active');
        cashbookWorkerId = null;
    }

    function refreshCashbook() {
        if (!cashbookWorkerId) return Promise.resolve();
        return API.get('/api/workers/' + cashbookWorkerId + '/cashbook')
            .then(function(data) {
                var balEl = document.getElementById('cashbookBalance');
                if (balEl) {
                    balEl.textContent = API.formatMoney(data.balance || 0);
                    balEl.style.color = (data.balance || 0) < 0 ? '#c62828' : '#2E75B6';
                }
                var tb = document.getElementById('cashbookTable');
                if (!tb) return;
                var entries = data.entries || [];
                if (entries.length === 0) {
                    tb.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#999;padding:16px;">\u0417\u0430\u043f\u0438\u0441\u0435\u0439 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442</td></tr>';
                    return;
                }
                tb.innerHTML = entries.map(function(e) {
                    var sign = e.entry_kind === 'client_payment' ? '+' : '\u2212';
                    var objName = e.object_name ? API.esc(e.object_name) : '\u2014';
                    return '<tr>' +
                        '<td>' + API.esc(e.entry_date || '') + '</td>' +
                        '<td>' + API.esc(opLabel(e)) + (e.note ? '<div style="font-size:11px;color:#888;">' + API.esc(e.note) + '</div>' : '') + '</td>' +
                        '<td>' + sign + API.formatMoney(e.amount) + '</td>' +
                        '<td>' + objName + '</td>' +
                        '<td><button type="button" class="btn btn-del" style="padding:2px 6px;font-size:10px;" onclick="deleteCashbookEntry(' + e.id + ')">\u00d7</button></td>' +
                    '</tr>';
                }).join('');
            })
            .catch(function(err) {
                UI.showToast('\u041e\u0448\u0438\u0431\u043a\u0430 \u0436\u0443\u0440\u043d\u0430\u043b\u0430: ' + (err.message || ''), 'error');
            });
    }

    function addCashbookEntry() {
        if (!cashbookWorkerId) return;
        var kindEl = document.getElementById('cb-kind');
        var amountEl = document.getElementById('cb-amount');
        var dateEl = document.getElementById('cb-date');
        var catEl = document.getElementById('cb-category');
        var objEl = document.getElementById('cb-object');
        var noteEl = document.getElementById('cb-note');
        var kind = kindEl ? kindEl.value : 'client_payment';
        var amount = parseFloat(amountEl && amountEl.value);
        if (!amount || amount <= 0) {
            UI.showToast('\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0441\u0443\u043c\u043c\u0443', 'error');
            return;
        }
        var body = {
            entry_kind: kind,
            amount: amount,
            entry_date: dateEl ? dateEl.value : '',
            note: noteEl ? noteEl.value.trim() : ''
        };
        if (kind === 'expense' && catEl) body.expense_category = catEl.value;
        if (objEl && objEl.value) body.object_id = parseInt(objEl.value, 10);

        API.post('/api/workers/' + cashbookWorkerId + '/cashbook', body)
            .then(function() {
                if (amountEl) amountEl.value = '';
                if (noteEl) noteEl.value = '';
                return refreshCashbook();
            })
            .then(function() { return loadBalances(); })
            .then(function() { render(); })
            .then(function() {
                UI.showToast('\u0417\u0430\u043f\u0438\u0441\u044c \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u0430', 'success');
            })
            .catch(function(e) {
                UI.showToast('\u041e\u0448\u0438\u0431\u043a\u0430: ' + (e.message || ''), 'error');
            });
    }

    function deleteCashbookEntry(entryId) {
        if (!cashbookWorkerId) return;
        if (!UI.confirm('\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0437\u0430\u043f\u0438\u0441\u044c \u0438\u0437 \u0436\u0443\u0440\u043d\u0430\u043b\u0430?')) return;
        API.del('/api/workers/' + cashbookWorkerId + '/cashbook/' + entryId)
            .then(function() { return refreshCashbook(); })
            .then(function() { return loadBalances(); })
            .then(function() { render(); })
            .then(function() {
                UI.showToast('\u0423\u0434\u0430\u043b\u0435\u043d\u043e', 'success');
            })
            .catch(function(e) {
                UI.showToast('\u041e\u0448\u0438\u0431\u043a\u0430: ' + (e.message || ''), 'error');
            });
    }

    function openModal(id) {
        var modalTitle = document.getElementById('modalTitle');
        var wId = document.getElementById('w-id');
        var wName = document.getElementById('w-name');
        var wPhone = document.getElementById('w-phone');
        var wRate = document.getElementById('w-rate');
        var wHireDate = document.getElementById('w-hire-date');
        var wNotes = document.getElementById('w-notes');
        var wActive = document.getElementById('w-active');

        if (modalTitle) modalTitle.textContent = id
            ? '\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0440\u0430\u0431\u043e\u0447\u0435\u0433\u043e'
            : '\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0440\u0430\u0431\u043e\u0447\u0435\u0433\u043e';
        if (wId) wId.value = id || '';

        if (id) {
            var w = workers.find(function(x) { return x.id === id; });
            if (!w) return;
            if (wName) wName.value = w.full_name || '';
            if (wPhone) wPhone.value = w.phone || '';
            if (wRate) wRate.value = w.daily_rate || 150;
            if (wHireDate) wHireDate.value = w.hire_date || '';
            if (wNotes) wNotes.value = w.notes || '';
            if (wActive) wActive.checked = isActive(w);
        } else {
            if (wName) wName.value = '';
            if (wPhone) wPhone.value = '';
            if (wRate) wRate.value = 150;
            if (wHireDate) wHireDate.value = '';
            if (wNotes) wNotes.value = '';
            if (wActive) wActive.checked = true;
        }

        var modal = document.getElementById('workerModal');
        if (modal) modal.classList.add('active');
        if (wName) wName.focus();
    }

    function closeModal() {
        var modal = document.getElementById('workerModal');
        if (modal) modal.classList.remove('active');
    }

    function editWorker(id) {
        openModal(id);
    }

    function saveWorker() {
        var wId = document.getElementById('w-id');
        var id = wId ? wId.value : '';
        var wName = document.getElementById('w-name');
        var name = wName ? wName.value.trim() : '';
        if (!name) {
            UI.showToast('\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0424\u0418\u041e', 'error');
            return;
        }

        var data = {
            full_name: name,
            phone: (document.getElementById('w-phone') || {}).value.trim() || '',
            daily_rate: parseFloat((document.getElementById('w-rate') || {}).value) || 150,
            hire_date: (document.getElementById('w-hire-date') || {}).value || '',
            notes: (document.getElementById('w-notes') || {}).value.trim() || '',
            is_active: (document.getElementById('w-active') || {}).checked ? 1 : 0
        };

        var req = id
            ? API.put('/api/workers/' + id, data)
            : API.post('/api/workers', data);

        req.then(function() {
            closeModal();
            loadWorkers();
            UI.showToast(id ? '\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e' : '\u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e', 'success');
        }).catch(function(e) {
            UI.showToast('\u041e\u0448\u0438\u0431\u043a\u0430: ' + (e.message || ''), 'error');
        });
    }

    function deleteWorker(id) {
        if (!UI.confirm('\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0440\u0430\u0431\u043e\u0447\u0435\u0433\u043e? \u0411\u0443\u0434\u0443\u0442 \u0443\u0434\u0430\u043b\u0435\u043d\u044b \u043f\u0440\u0438\u0432\u044f\u0437\u043a\u0438 \u043a \u043e\u0431\u044a\u0435\u043a\u0442\u0430\u043c \u0438 \u0432\u0441\u044f \u043a\u043d\u0438\u0433\u0430 \u043f\u043e\u0434\u043e\u0442\u0447\u0451\u0442\u0430.')) return;

        API.del('/api/workers/' + id)
            .then(function() {
                loadWorkers();
                UI.showToast('\u0423\u0434\u0430\u043b\u0435\u043d\u043e', 'success');
            })
            .catch(function(e) {
                UI.showToast('\u041e\u0448\u0438\u0431\u043a\u0430: ' + (e.message || ''), 'error');
            });
    }

    document.addEventListener('DOMContentLoaded', function() {
        var modal = document.getElementById('workerModal');
        if (modal) {
            modal.addEventListener('click', function(e) {
                if (e.target.id === 'workerModal') closeModal();
            });
        }
        var cbModal = document.getElementById('cashbookModal');
        if (cbModal) {
            cbModal.addEventListener('click', function(e) {
                if (e.target.id === 'cashbookModal') closeCashbook();
            });
        }
        var kindEl = document.getElementById('cb-kind');
        if (kindEl) kindEl.addEventListener('change', syncCbCategoryVisibility);
        loadWorkers();
    });

    return {
        loadWorkers: loadWorkers,
        render: render,
        openModal: openModal,
        closeModal: closeModal,
        editWorker: editWorker,
        saveWorker: saveWorker,
        deleteWorker: deleteWorker,
        openCashbook: openCashbook,
        closeCashbook: closeCashbook,
        addCashbookEntry: addCashbookEntry,
        deleteCashbookEntry: deleteCashbookEntry
    };
})(API, UI);

window.openModal = function(id) { WorkersPage.openModal(id); };
window.closeModal = function() { WorkersPage.closeModal(); };
window.saveWorker = function() { WorkersPage.saveWorker(); };
window.editWorker = function(id) { WorkersPage.editWorker(id); };
window.deleteWorker = function(id) { WorkersPage.deleteWorker(id); };
window.openCashbook = function(id) { WorkersPage.openCashbook(id); };
window.closeCashbook = function() { WorkersPage.closeCashbook(); };
window.addCashbookEntry = function() { WorkersPage.addCashbookEntry(); };
window.deleteCashbookEntry = function(eid) { WorkersPage.deleteCashbookEntry(eid); };
