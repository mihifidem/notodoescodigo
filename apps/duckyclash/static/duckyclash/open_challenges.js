(() => {
    let started = performance.now();
    const counters = [...document.querySelectorAll('[data-open-countdown]')];
    const update = () => {
        const elapsed = Math.floor((performance.now() - started) / 1000);
        counters.forEach((counter) => {
            const remaining = Math.max(0, Number(counter.dataset.seconds) - elapsed);
            counter.textContent = remaining > 0
                ? `${Math.floor(remaining / 3600)}h ${Math.floor(remaining / 60) % 60}m ${remaining % 60}s`
                : 'Caducado';
            if (remaining === 0) {
                const card = counter.closest('[data-open-card]');
                if (card.contains(document.activeElement)) {
                    document.getElementById('open-sync-status').focus();
                }
                card.hidden = true;
                card.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
            }
        });
    };
    update();
    setInterval(update, 1000);
    const panel = document.getElementById('open-panel');
    const status = document.getElementById('open-sync-status');
    const empty = document.getElementById('open-live-empty');
    if (!panel) return;
    const cards = [...panel.querySelectorAll('[data-open-card]')];
    if (!cards.length) return;
    let fetching = false;
    let stopped = false;
    let timer;
    const sync = async () => {
        if (fetching || stopped) return;
        clearTimeout(timer);
        fetching = true;
        try {
            const ids = cards.map(card => card.dataset.challengeId).join(',');
            const response = await fetch(`${panel.dataset.stateUrl}?ids=${encodeURIComponent(ids)}`, {
                credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}
            });
            if (!response.ok || response.redirected) throw new Error('Estado no disponible');
            const state = await response.json();
            if (!Array.isArray(state.available) || state.available.some(item =>
                !Number.isSafeInteger(item.id) || item.id < 1
                || !Number.isInteger(item.remaining) || item.remaining < 0
            )) throw new Error('Estado no válido');
            cards.forEach(card => {
                const available = state.available.find(item => String(item.id) === card.dataset.challengeId);
                if (!available && card.contains(document.activeElement)) status.focus();
                card.hidden = !available;
                card.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = !available; });
                const counter = card.querySelector('[data-open-countdown]');
                counter.dataset.seconds = available ? available.remaining : 0;
            });
            started = performance.now();
            update();
            empty.hidden = cards.some(card => !card.hidden);
            status.textContent = 'Estado actualizado desde el servidor. Actualiza el panel para cargar nuevos retos.';
            if (!document.hidden) timer = setTimeout(sync, 10000);
        } catch (error) {
            stopped = true;
            status.textContent = 'Actualización detenida: no se pudo consultar el servidor. Actualiza el panel antes de aceptar.';
            cards.forEach(card => card.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; }));
        } finally {
            fetching = false;
        }
    };
    document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
    sync();
})();
