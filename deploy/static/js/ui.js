/**
 * ui.js — Рендеринг модалок, уведомлений, валидация форм
 */

var UI = (function() {
    'use strict';

    /**
     * Показать модальное окно
     */
    function showModal(id) {
        var modal = document.getElementById(id);
        if (modal) {
            modal.style.display = 'flex';
            modal.classList.add('active');
        }
    }

    /**
     * Скрыть модальное окно
     */
    function hideModal(id) {
        var modal = document.getElementById(id);
        if (modal) {
            modal.style.display = 'none';
            modal.classList.remove('active');
        }
    }

    /**
     * Закрыть все модальные окна
     */
    function hideAllModals() {
        var modals = document.querySelectorAll('.modal');
        for (var i = 0; i < modals.length; i++) {
            modals[i].style.display = 'none';
            modals[i].classList.remove('active');
        }
    }

    /**
     * Показать уведомление (toast)
     */
    function showToast(message, type) {
        type = type || 'info'; // info, success, error, warning
        var toast = document.createElement('div');
        toast.className = 'flash flash-' + type;
        toast.textContent = message;
        toast.style.position = 'fixed';
        toast.style.top = '20px';
        toast.style.right = '20px';
        toast.style.zIndex = '9999';
        toast.style.minWidth = '250px';
        toast.style.boxShadow = '0 4px 20px rgba(0,0,0,0.15)';
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100px)';
        toast.style.transition = 'opacity 0.3s, transform 0.3s';

        document.body.appendChild(toast);

        // Animate in
        requestAnimationFrame(function() {
            toast.style.opacity = '1';
            toast.style.transform = 'translateX(0)';
        });

        // Auto remove after 3s
        setTimeout(function() {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(100px)';
            setTimeout(function() {
                if (toast.parentNode) toast.parentNode.removeChild(toast);
            }, 300);
        }, 3000);

        return toast;
    }

    /**
     * Запрос подтверждения
     */
    function confirm(message) {
        return window.confirm(message);
    }

    /**
     * Валидация формы (проверка required полей)
     */
    function validateForm(formEl) {
        var inputs = formEl.querySelectorAll('[required]');
        var valid = true;

        for (var i = 0; i < inputs.length; i++) {
            var input = inputs[i];
            if (!input.value || input.value.trim() === '') {
                input.style.borderColor = '#f44336';
                valid = false;
            } else {
                input.style.borderColor = '';
            }
        }

        return valid;
    }

    /**
     * Сериализация формы в объект
     */
    function serializeForm(formEl) {
        var data = {};
        var inputs = formEl.querySelectorAll('input, select, textarea');

        for (var i = 0; i < inputs.length; i++) {
            var input = inputs[i];
            if (input.name) {
                if (input.type === 'checkbox') {
                    data[input.name] = input.checked;
                } else {
                    data[input.name] = input.value;
                }
            }
        }

        return data;
    }

    /**
     * Загрузочный спиннер
     */
    function showLoading(target) {
        var spinner = document.createElement('div');
        spinner.className = 'spinner';
        spinner.id = 'global-spinner';

        if (typeof target === 'string') {
            target = document.querySelector(target);
        }
        if (target) {
            target.appendChild(spinner);
        } else {
            document.body.appendChild(spinner);
        }
    }

    function hideLoading() {
        var spinner = document.getElementById('global-spinner');
        if (spinner && spinner.parentNode) {
            spinner.parentNode.removeChild(spinner);
        }
    }

    return {
        showModal: showModal,
        hideModal: hideModal,
        hideAllModals: hideAllModals,
        showToast: showToast,
        confirm: confirm,
        validateForm: validateForm,
        serializeForm: serializeForm,
        showLoading: showLoading,
        hideLoading: hideLoading
    };
})();
