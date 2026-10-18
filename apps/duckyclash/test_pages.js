const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const scripts = name => fs.readFileSync(path.join(__dirname, 'static', 'duckyclash', name), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function element(dataset = {}) {
    const classes = new Set();
    return {
        dataset, hidden: false, disabled: false, textContent: '', attributes: {}, focused: false,
        classList: {add: name => classes.add(name), toggle: (name, on) => on ? classes.add(name) : classes.delete(name)},
        setAttribute(name, value) { this.attributes[name] = value; },
        removeAttribute(name) { delete this.attributes[name]; if (name === 'href') delete this.href; },
        focus() { this.focused = true; }
    };
}

function pages({state = {can_ring: false, is_terminal: false, status: 'Esperando', opens_at: null}, failure = false, waiting = true, images = []} = {}) {
    const ring = element({ringUrl: '/clash/duels/1/ring/'});
    const status = element();
    const results = element();
    const countdown = element();
    const clock = element();
    let elapsed = 0;
    const room = element({stateUrl: '/clash/duels/1/waiting/state/'});
    const imageStatus = element();
    const showcase = {
        querySelector: () => imageStatus,
        querySelectorAll: () => images
    };
    room.querySelector = () => images.length ? showcase : null;
    const local = element();
    local.dateTime = '2026-10-16T09:35:00+00:00';
    const button = element();
    const form = element({clashConfirm: 'Confirmar'});
    form.addEventListener = (name, callback) => { form.submit = callback; };
    form.querySelectorAll = () => [button];
    const nav = element();
    nav.href = 'https://example.com/clash/';
    const timers = [];
    const listeners = {};
    const document = {
        hidden: false,
        querySelectorAll: selector => selector === '[data-local-time]' ? [local]
            : selector === '[data-clash-confirm]' ? [form] : [nav],
        querySelector: selector => selector === '[data-clash-waiting]' && waiting ? room : null,
        getElementById: id => ({
            'waiting-ring': ring, 'waiting-status': status, 'waiting-results': results,
            'waiting-countdown': countdown, 'waiting-countdown-clock': clock
        })[id],
        addEventListener: (name, callback) => {
            const previous = listeners[name];
            listeners[name] = () => { if (previous) previous(); callback(); };
        }
    };
    const window = {
        location: {pathname: '/clash/'}, confirm: () => true,
        addEventListener: (name, callback) => { listeners[name] = callback; }
    };
    vm.runInNewContext(scripts('pages.js'), {
        document, window, Date, Intl, URL,
        performance: {now: () => elapsed},
        setTimeout: (callback, delay) => {
            callback.delay = delay;
            timers.push(callback);
            return timers.length;
        }, clearTimeout: () => {},
        fetch: async () => {
            if (failure) throw new Error('offline');
            return {ok: true, redirected: false, json: async () => state};
        }
    });
    return {
        ring, status, results, countdown, clock, local, form, button, window, nav, timers, document, listeners, imageStatus,
        advance: milliseconds => { elapsed += milliseconds; }
    };
}

test('waiting GIF load failures are reported without video playback APIs', async () => {
    const images = Array.from({length: 2}, () => ({
        complete: true, naturalWidth: 320, listeners: {},
        addEventListener(name, callback) { this.listeners[name] = callback; }
    }));
    const page = pages({images});
    await flush();
    assert.equal(page.imageStatus.textContent, '');
    images[0].listeners.error();
    assert.match(page.imageStatus.textContent, /No se pudo cargar una animación/);
});

test('already failed GIF loads are reported when initializing the waiting room', async () => {
    const page = pages({images: [{
        complete: true, naturalWidth: 0,
        addEventListener() {}
    }]});
    await flush();
    assert.match(page.imageStatus.textContent, /No se pudo cargar una animación/);
});

test('waiting opens only on explicit server permission', async () => {
    const blocked = pages();
    await flush();
    assert.equal(blocked.ring.href, undefined);
    assert.equal(blocked.ring.attributes['aria-disabled'], 'true');
    const open = pages({state: {can_ring: true, is_terminal: false, status: 'Preparado'}});
    await flush();
    assert.equal(open.ring.href, '/clash/duels/1/ring/');
    assert.equal(open.ring.attributes['aria-disabled'], 'false');
    assert.equal(open.timers.length, 1);
});

test('terminal state locks Ring and shows results without implying payment', async () => {
    const page = pages({state: {can_ring: true, is_terminal: true, status: 'Pendiente de liquidación'}});
    await flush();
    assert.equal(page.ring.href, undefined);
    assert.equal(page.results.hidden, false);
    assert.match(page.status.textContent, /liquidación/);
    assert.equal(page.timers.length, 0);
});

test('network and malformed-state failures stop polling visibly', async () => {
    for (const options of [
        {failure: true}, {state: {unlocked: true}},
        {state: {can_ring: false, is_terminal: false, opens_at: 'invalid', server_time: 'invalid'}}
    ]) {
        const page = pages(options);
        await flush();
        assert.equal(page.ring.href, undefined);
        assert.match(page.status.textContent, /Actualización detenida/);
        assert.equal(page.countdown.hidden, true);
        assert.equal(page.timers.length, 0);
        page.listeners.visibilitychange();
        await flush();
        assert.equal(page.timers.length, 0);
    }
});

test('confirmed waiting room countdown uses server time and monotonic elapsed time', async () => {
    const page = pages({state: {
        can_ring: false, is_terminal: false, status: 'Programado',
        opens_at: '2026-10-16T10:00:00+00:00', server_time: '2026-10-15T08:57:57+00:00'
    }});
    await flush();
    assert.equal(page.countdown.hidden, false);
    assert.equal(page.clock.textContent, '1 d · 01:02:03');
    const tick = page.timers.find(callback => callback.delay === 1000);
    page.advance(2000);
    tick();
    assert.equal(page.clock.textContent, '1 d · 01:02:01');
    assert.equal(page.ring.href, undefined);
});

test('zero countdown never unlocks Ring and waits for fresh server authorization', async () => {
    const state = {
        can_ring: false, is_terminal: false, status: 'Programado',
        opens_at: '2026-10-16T10:00:00+00:00', server_time: '2026-10-16T09:59:59+00:00'
    };
    const page = pages({state});
    await flush();
    page.advance(1000);
    page.timers.find(callback => callback.delay === 1000)();
    assert.match(page.clock.textContent, /Verificando apertura/);
    assert.equal(page.ring.href, undefined);
    assert.equal(page.ring.attributes['aria-disabled'], 'true');
    state.can_ring = true;
    page.timers.find(callback => callback.delay === 5000)();
    await flush();
    assert.equal(page.countdown.hidden, true);
    assert.equal(page.ring.href, '/clash/duels/1/ring/');
});

test('pending reserves and terminal state remove the opening countdown', async () => {
    const state = {
        can_ring: false, is_terminal: false, status: 'Programado',
        opens_at: '2026-10-16T10:00:00+00:00', server_time: '2026-10-16T09:00:00+00:00'
    };
    const page = pages({state});
    await flush();
    state.opens_at = null;
    page.timers.find(callback => callback.delay === 5000)();
    await flush();
    assert.equal(page.countdown.hidden, true);
    state.is_terminal = true;
    state.opens_at = '2026-10-16T10:00:00+00:00';
    page.listeners.visibilitychange();
    await flush();
    assert.equal(page.countdown.hidden, true);
    assert.equal(page.results.hidden, false);
});

test('local presentation preserves ISO and navigation identifies current page', () => {
    const page = pages({waiting: false});
    assert.equal(page.local.dateTime, '2026-10-16T09:35:00+00:00');
    assert.equal(page.local.textContent, new Intl.DateTimeFormat('es', {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
    }).format(new Date(page.local.dateTime)));
    assert.match(page.local.title, /Hora local:/);
    assert.equal(page.nav.attributes['aria-current'], 'page');
});

test('confirmation cancels safely and prevents accidental double submission', () => {
    const page = pages({waiting: false});
    let prevented = 0;
    const event = {preventDefault() { prevented++; }};
    page.window.confirm = () => false;
    page.form.submit(event);
    assert.equal(prevented, 1);
    assert.equal(page.button.disabled, false);
    page.window.confirm = () => true;
    page.form.submit(event);
    assert.equal(page.button.disabled, true);
    page.form.submit(event);
    assert.equal(prevented, 2);
    page.listeners.pageshow();
    assert.equal(page.button.disabled, false);
    assert.equal(page.form.dataset.sending, undefined);
});

function panel({available = [{id: 1, remaining: 40}], failure = false} = {}) {
    const counter = element({seconds: 60});
    const button = element();
    const card = element({challengeId: '1'});
    const status = element();
    const empty = element();
    const root = element({stateUrl: '/clash/open/state/'});
    root.querySelectorAll = () => [card];
    card.querySelector = () => counter;
    card.querySelectorAll = () => [button];
    card.contains = () => true;
    counter.closest = () => card;
    let tick;
    const timers = [];
    const requests = [];
    vm.runInNewContext(scripts('open_challenges.js'), {
        document: {
            hidden: false,
            querySelectorAll: () => [counter],
            getElementById: id => ({'open-panel': root, 'open-sync-status': status, 'open-live-empty': empty})[id],
            addEventListener: () => {}
        },
        performance: {now: () => 0},
        setInterval: callback => { tick = callback; },
        setTimeout: callback => { timers.push(callback); return timers.length; }, clearTimeout: () => {},
        encodeURIComponent,
        fetch: async url => {
            requests.push(url);
            if (failure) throw new Error('offline');
            return {ok: true, redirected: false, json: async () => ({available})};
        }
    });
    return {card, counter, button, status, empty, timers, requests, tick};
}

test('public panel removes claimed cards and restores focus to status', async () => {
    const page = panel({available: []});
    await flush();
    assert.equal(page.card.hidden, true);
    assert.equal(page.button.disabled, true);
    assert.equal(page.empty.hidden, false);
    assert.equal(page.status.focused, true);
    assert.deepEqual(page.requests, ['/clash/open/state/?ids=1']);
});

test('public panel refreshes monotonic countdown from server', async () => {
    const page = panel();
    await flush();
    assert.equal(page.card.hidden, false);
    assert.equal(page.counter.dataset.seconds, 40);
    assert.match(page.counter.textContent, /40s/);
    assert.equal(page.empty.hidden, true);
});

test('public refresh failure stops, disables acceptance and provides manual recovery', async () => {
    const page = panel({failure: true});
    await flush();
    assert.equal(page.button.disabled, true);
    assert.match(page.status.textContent, /Actualiza el panel/);
    assert.equal(page.timers.length, 0);
});

test('invalid public clock payload stops instead of showing a fake expiry', async () => {
    const page = panel({available: [{id: 1, remaining: 'unknown'}]});
    await flush();
    assert.equal(page.button.disabled, true);
    assert.match(page.status.textContent, /Actualización detenida/);
    assert.equal(page.timers.length, 0);
});
