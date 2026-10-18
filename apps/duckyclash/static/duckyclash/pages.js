(() => {
    'use strict';
    document.querySelectorAll('[data-local-time]').forEach(element => {
        const date = new Date(element.dateTime);
        if (!Number.isNaN(date.getTime())) {
            element.textContent = new Intl.DateTimeFormat('es', {
                year: 'numeric', month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
            }).format(date);
            element.title = `Hora local: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
        }
    });
    document.querySelectorAll('nav[aria-label="DuckyClash"] a').forEach(link => {
        if (new URL(link.href).pathname === window.location.pathname) {
            link.classList.add('active');
            link.setAttribute('aria-current', 'page');
        }
    });
    document.querySelectorAll('[data-clash-confirm]').forEach(form => {
        form.addEventListener('submit', event => {
            if (event.submitter && event.submitter.name === 'catalog_action') return;
            if (form.dataset.sending === 'true' || !window.confirm(form.dataset.clashConfirm)) {
                event.preventDefault();
                return;
            }
            form.dataset.sending = 'true';
            form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
        });
        window.addEventListener('pageshow', () => {
            document.querySelectorAll('[data-clash-confirm]').forEach(form => {
                if (form.dataset.sending === 'true') {
                    delete form.dataset.sending;
                    form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = false; });
                }
            });
        });
    });
    const errors = document.querySelector('[data-clash-errors]');
    if (errors) errors.focus();
    const waiting = document.querySelector('[data-clash-waiting]');
    if (!waiting) return;
    const duckShowcase = waiting.querySelector('[data-waiting-ducks]');
    if (duckShowcase) {
        const duckStatus = duckShowcase.querySelector('[data-waiting-duck-status]');
        duckShowcase.querySelectorAll('.waiting-duck-source').forEach(image => {
            const reportError = () => {
                duckStatus.textContent = 'No se pudo cargar una animación de la sala. Recarga la página para volver a intentarlo.';
            };
            image.addEventListener('error', reportError);
            if (image.complete && !image.naturalWidth) {
                reportError();
            }
        });
    }
    const link = document.getElementById('waiting-ring');
    const status = document.getElementById('waiting-status');
    const results = document.getElementById('waiting-results');
    let fetching = false;
    let stopped = false;
    let timer;
    const countdown = document.getElementById('waiting-countdown');
    const clock = document.getElementById('waiting-countdown-clock');
    let clockTimer;
    let opening;
    let serverAnchor;
    let monotonicAnchor;
    const hideCountdown = () => {
        clearTimeout(clockTimer);
        opening = undefined;
        if (countdown) countdown.hidden = true;
    };
    const tickCountdown = () => {
        clearTimeout(clockTimer);
        if (opening === undefined || stopped || !clock) return;
        const remaining = Math.max(0, Math.ceil(
            (opening - serverAnchor - (performance.now() - monotonicAnchor)) / 1000
        ));
        if (remaining === 0) {
            clock.textContent = 'Verificando apertura con el servidor…';
            // Reaching zero never grants access; poll still verifies reserves and permissions.
            return;
        }
        const days = Math.floor(remaining / 86400);
        const hours = Math.floor((remaining % 86400) / 3600);
        const minutes = Math.floor((remaining % 3600) / 60);
        const seconds = remaining % 60;
        clock.textContent = `${days ? `${days} d · ` : ''}${[hours, minutes, seconds]
            .map(value => String(value).padStart(2, '0')).join(':')}`;
        if (!document.hidden) clockTimer = setTimeout(tickCountdown, 1000);
    };
    const syncCountdown = (opensAt, serverTime) => {
        if (!countdown || !clock) return;
        hideCountdown();
        if (opensAt === null) return;
        const deadline = typeof opensAt === 'string' ? Date.parse(opensAt) : NaN;
        const now = typeof serverTime === 'string' ? Date.parse(serverTime) : NaN;
        if (!Number.isFinite(deadline) || !Number.isFinite(now)) {
            throw new Error('Horario de apertura no válido.');
        }
        opening = deadline;
        serverAnchor = now;
        monotonicAnchor = performance.now();
        countdown.hidden = false;
        tickCountdown();
    };
    const poll = async () => {
        if (fetching || stopped) return;
        clearTimeout(timer);
        fetching = true;
        try {
            const response = await fetch(waiting.dataset.stateUrl, {
                credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}
            });
            if (!response.ok || response.redirected) throw new Error('No se pudo verificar la sala.');
            const state = await response.json();
            if (typeof state.can_ring !== 'boolean' || typeof state.is_terminal !== 'boolean') {
                throw new Error('Estado de sala no válido.');
            }
            const terminal = state.is_terminal;
            const enabled = state.can_ring && !terminal;
            syncCountdown(terminal || enabled ? null : state.opens_at, state.server_time);
            link.classList.toggle('disabled', !enabled);
            link.setAttribute('aria-disabled', String(!enabled));
            if (enabled) {
                link.href = link.dataset.ringUrl;
                link.removeAttribute('tabindex');
            } else {
                link.removeAttribute('href');
                link.setAttribute('tabindex', '-1');
            }
            results.hidden = !terminal;
            status.textContent = terminal ? 'El combate ha terminado. Consulta el resultado y la liquidación.'
                : enabled ? 'Ring disponible. Ambos jugadores deberán confirmar su preparación.'
                : `Esperando la fecha y reservas. ${state.status}`;
            stopped = terminal;
            if (!terminal && !document.hidden) timer = setTimeout(poll, 5000);
        } catch (error) {
            stopped = true;
            hideCountdown();
            link.removeAttribute('href');
            link.setAttribute('aria-disabled', 'true');
            link.setAttribute('tabindex', '-1');
            link.classList.add('disabled');
            status.textContent = 'Actualización detenida: no se pudo verificar el servidor. Pulsa Actualizar sala.';
        } finally {
            fetching = false;
        }
    };
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            clearTimeout(clockTimer);
        } else {
            tickCountdown();
            poll();
        }
    });
    window.addEventListener('pageshow', () => { if (!stopped) poll(); });
    if (clock && clock.dataset.opensAt) {
        try {
            syncCountdown(clock.dataset.opensAt, clock.dataset.serverTime);
        } catch (error) {
            hideCountdown();
            status.textContent = 'Horario de apertura no válido. Verificando la sala con el servidor…';
        }
    }
    poll();
})();
