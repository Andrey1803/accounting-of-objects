/**
 * Прокрутка области .table-wrapper.table-pan-viewport колёсиком / тачем;
 * мышью — зажать ЛКМ на ячейке (не на th / кнопках / полях) и тянуть.
 */
(function () {
    function interactiveTarget(t) {
        return t && t.closest && t.closest('button, a, input, select, textarea, label, th');
    }

    function bindPan(el) {
        if (el.dataset.tablePanBound) return;
        el.dataset.tablePanBound = '1';

        var drag = false;
        var pid = null;

        el.addEventListener('pointerdown', function (e) {
            if (e.pointerType !== 'mouse' || e.button !== 0) return;
            if (interactiveTarget(e.target)) return;
            drag = true;
            pid = e.pointerId;
            try {
                el.setPointerCapture(pid);
            } catch (_) {}
        });

        el.addEventListener('pointermove', function (e) {
            if (!drag || e.pointerId !== pid || e.pointerType !== 'mouse') return;
            var mx = e.movementX;
            var my = e.movementY;
            if (mx || my) {
                el.scrollLeft -= mx;
                el.scrollTop -= my;
                el.classList.add('is-dragging');
            }
        });

        function end(e) {
            if (!drag) return;
            if (e && e.pointerId != null && e.pointerId !== pid) return;
            drag = false;
            el.classList.remove('is-dragging');
            if (pid != null) {
                try {
                    el.releasePointerCapture(pid);
                } catch (_) {}
                pid = null;
            }
        }

        el.addEventListener('pointerup', end);
        el.addEventListener('pointercancel', end);
    }

    function run() {
        document.querySelectorAll('.table-wrapper.table-pan-viewport, .tbl-wrap.table-pan-viewport').forEach(bindPan);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', run);
    } else {
        run();
    }
})();
