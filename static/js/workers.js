/**
 * workers.js — страница рабочих (API + UI)
 */
var WorkersPage = (function(API, UI) {
    'use strict';

    var workers = [];

    function isActive(w) {
        return w.is_active === 1 || w.is_active === true;
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
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#999;padding:30px;">\u041d\u0435\u0442 \u0440\u0430\u0431\u043e\u0447\u0438\u0445. \u041d\u0430\u0436\u043c\u0438\u0442\u0435 &quot;\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0440\u0430\u0431\u043e\u0447\u0435\u0433\u043e&quot;</td></tr>';
            return;
        }

        tbody.innerHTML = workers.map(function(w) {
            return '<tr>' +
                '<td><strong>' + API.esc(w.full_name) + '</strong></td>' +
                '<td>' + (API.esc(w.phone) || '\u2014') + '</td>' +
                '<td><strong>' + API.formatMoney(w.daily_rate) + '</strong></td>' +
                '<td>' + (w.hire_date || '\u2014') + '</td>' +
                '<td>' + (isActive(w)
                    ? '<span class="active-badge">\u0410\u043a\u0442\u0438\u0432\u0435\u043d</span>'
                    : '<span class="inactive-badge">\u041d\u0435 \u0430\u043a\u0442\u0438\u0432\u0435\u043d</span>') + '</td>' +
                '<td>' +
                    '<button class="btn btn-edit" onclick="editWorker(' + w.id + ')" style="padding:4px 8px;font-size:11px;">\u270f\ufe0f</button> ' +
                    '<button class="btn btn-del" onclick="deleteWorker(' + w.id + ')" style="padding:4px 8px;font-size:11px;">\ud83d\uddd1\ufe0f</button>' +
                '</td>' +
            '</tr>';
        }).join('');
    }

    function loadWorkers() {
        API.get('/api/workers')
            .then(function(data) {
                workers = Array.isArray(data) ? data : [];
                render();
            })
            .catch(function() {
                var tbody = document.getElementById('workersTable');
                if (tbody) {
                    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#999;padding:30px;">\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438</td></tr>';
                }
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
        if (!UI.confirm('\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0440\u0430\u0431\u043e\u0447\u0435\u0433\u043e? \u0412\u0441\u0435 \u043f\u0440\u0438\u0432\u044f\u0437\u043a\u0438 \u043a \u043e\u0431\u044a\u0435\u043a\u0442\u0430\u043c \u0442\u043e\u0436\u0435 \u0431\u0443\u0434\u0443\u0442 \u0443\u0434\u0430\u043b\u0435\u043d\u044b.')) return;

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
        loadWorkers();
    });

    return {
        loadWorkers: loadWorkers,
        render: render,
        openModal: openModal,
        closeModal: closeModal,
        editWorker: editWorker,
        saveWorker: saveWorker,
        deleteWorker: deleteWorker
    };
})(API, UI);

window.openModal = function(id) { WorkersPage.openModal(id); };
window.closeModal = function() { WorkersPage.closeModal(); };
window.saveWorker = function() { WorkersPage.saveWorker(); };
window.editWorker = function(id) { WorkersPage.editWorker(id); };
window.deleteWorker = function(id) { WorkersPage.deleteWorker(id); };
