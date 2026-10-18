const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static', 'duckyclash', 'ring.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

class Element {
    constructor() {
        this.children = [];
        this.listeners = {};
        this.textContent = '';
        this.disabled = false;
        this.hidden = false;
        this.dataset = {};
        this.style = {};
        this.attributes = {};
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(event, callback) { this.listeners[event] = callback; }
    setAttribute(name, value) { this.attributes[name] = value; }
    focus() {}
    querySelector() { return this.children.flatMap(child => child.children).find(child => child.checked) || null; }
    querySelectorAll() { return this.children.flatMap(child => child.children).filter(child => child.type === 'radio'); }
}

function state(overrides = {}) {
    return {
        phase: 'in_progress', status: 'En combate', answered: false,
        server_time: '2026-01-01T00:00:00Z',
        participants: [{player_id: 1, hp: 80, max_hp: 100}],
        question: {
            id: 10, closes_at: '2026-01-01T00:00:30Z',
            options: [{id: 'a', text: '<safe>'}, {id: 'b', text: 'B'}]
        },
        ...overrides
    };
}

function harness(initial, enginePhase = '', realtime = false, graphical = false) {
    const ids = Object.fromEntries([
        'clash-ring', 'clash-sync-status', 'clash-options', 'clash-answer', 'clash-cloud',
        'clash-question', 'clash-question-close', 'clash-question-open',
        'clash-clock', 'clash-answer-form', 'clash-answer-feedback',
        ...(enginePhase ? ['clash-presence', 'clash-engine-clock', 'clash-ready', 'clash-engine-error'] : []),
        ...(graphical ? ['clash-item-feedback', 'clash-result', 'clash-result-text', 'clash-question-title',
            'clash-prompt', 'clash-canvas', 'clash-assets', 'clash-assets-status', 'clash-effect-status'] : [])
    ].map(id => [id, new Element()]));
    const health = new Element();
    const item = new Element();
    const timers = new Map();
    const listeners = {};
    const calls = [];
    const sockets = [];
    const sceneStates = [];
    const sceneAttacks = [];
    const itemForm = new Element();
    const actionId = {value: 'original-action-id'};
    itemForm.querySelector = selector => selector === '[name="action_id"]' ? actionId : item;
    item.closest = () => itemForm;
    item.dataset = {code: 'NUBE', name: 'Nube'};
    const readyForm = new Element();
    let timerId = 0;
    let payload = initial;
    let failure = false;
    let reloads = 0;
    ids['clash-ring'].dataset = {stateUrl: '/state/', questionId: '10', enginePhase, playerId: '1'};
    ids['clash-ring'].querySelector = () => health;
    ids['clash-ring'].querySelectorAll = () => [item];
    if (graphical) {
        ids['clash-ring'].dataset.canvasRing = 'true';
        ids['clash-ring'].dataset.answerTemplate = '/questions/0/answer/';
        ids['clash-ring'].dataset.itemTemplate = '/questions/0/item/';
        ids['clash-ring'].querySelectorAll = selector => selector === '[data-item-form]' ? [itemForm] : [item];
        ids['clash-assets'].textContent = '{"fighters":[{"player_id":1,"name":"Ducky uno"}]}';
        if (ids['clash-ready']) ids['clash-ready'].closest = () => readyForm;
    }
    ids['clash-answer-form'].action = '/answer/';
    if (enginePhase) ids['clash-presence'].action = '/presence/';
    const context = {
        document: {
            hidden: false,
            getElementById: id => ids[id],
            createElement: () => new Element(),
            addEventListener: (event, callback) => { listeners[event] = callback; }
        },
        location: {protocol: 'https:', host: 'example.test', reload: () => { reloads += 1; }},
        setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
        clearTimeout: id => timers.delete(id),
        FormData: class {},
        fetch: async (url, options) => {
            calls.push({url, options});
            if (failure) throw new Error('offline');
            return {
                ok: true, redirected: false,
                json: async () => options.method ? {accepted: true} : payload
            };
        }
    };
    if (graphical) {
        context.window = {
            DuckyClashScene: class {
                update(value) { sceneStates.push(value); }
                attack(playerId) { sceneAttacks.push(playerId); }
                dispose() {}
            },
            addEventListener: () => {}
        };
        context.crypto = {getRandomValues: bytes => bytes.fill(7)};
    }
    if (realtime) {
        ids['clash-ring'].dataset.socketPath = '/ws/clash/duels/1/';
        context.WebSocket = class {
            constructor(url) { this.url = url; this.listeners = {}; this.sent = []; sockets.push(this); }
            addEventListener(event, callback) { this.listeners[event] = callback; }
            send(data) { this.sent.push(JSON.parse(data)); }
            close() { this.closed = true; }
        };
    }
    vm.runInNewContext(source, context);
    return {
        ids, health, item, itemForm, actionId, readyForm, sceneStates, sceneAttacks, timers, calls, listeners, sockets,
        context, setState: value => { payload = value; },
        fail: () => { failure = true; }, reloads: () => reloads,
        poll: async () => {
            const callback = timers.values().next().value;
            assert.equal(typeof callback, 'function');
            callback();
            await tick();
        }
    };
}

test('renders server HP, plain-text options and informative remaining time', async () => {
    const app = harness(state());
    await tick();
    assert.equal(app.health.textContent, 'HP: 80 / 100');
    assert.equal(app.ids['clash-options'].children[0].children[1].textContent, '<safe>');
    assert.equal(app.ids['clash-answer'].disabled, false);
    assert.match(app.ids['clash-clock'].textContent, /30 s/);
    assert.equal(app.calls[0].options.cache, 'no-store');
});

test('answer interaction requests only a local attack animation while answering is enabled', async () => {
    const app = harness(state({phase: 'question_active'}), 'question_active', false, true);
    await tick();
    const before = app.health.textContent;
    app.ids['clash-answer-form'].listeners.change();
    assert.deepEqual(app.sceneAttacks, ['1']);
    assert.equal(app.health.textContent, before);
    app.ids['clash-answer'].disabled = true;
    app.ids['clash-answer-form'].listeners.change();
    assert.deepEqual(app.sceneAttacks, ['1']);
});

test('countdown sends authenticated presence each server second over HTTP and WebSocket', async () => {
    const countdown = state({
        phase: 'countdown', question: null, unlocked: true, sequence: 1,
    });
    const http = harness(countdown, 'countdown', false, true);
    await tick();
    assert.equal(http.calls.filter(call => call.url === '/presence/').length, 1);
    http.setState({...countdown, server_time: '2026-01-01T00:00:01Z'});
    await http.poll();
    assert.equal(http.calls.filter(call => call.url === '/presence/').length, 2);
    const realtime = harness(countdown, 'countdown', true, true);
    await tick();
    const socket = realtime.sockets[0];
    socket.listeners.open();
    socket.listeners.message({data: JSON.stringify({
        type: 'state', state: {...countdown, server_time: '2026-01-01T00:00:01Z'}
    })});
    assert.equal(socket.sent.filter(message => message.type === 'presence').length, 1);
    socket.listeners.message({data: JSON.stringify({
        type: 'state', state: {...countdown, server_time: '2026-01-01T00:00:01.500Z'}
    })});
    assert.equal(socket.sent.filter(message => message.type === 'presence').length, 1);
});

test('graphical Ring changes phase and question without reloading or keeping old options', async () => {
    const app = harness(state({
        phase: 'countdown', question: null, unlocked: true, question_id: 10, sequence: 1,
        items: [{code: 'NUBE', quantity: 2, enabled: true}]
    }), 'countdown', true, true);
    await tick();
    assert.equal(app.item.disabled, false);
    assert.equal(app.itemForm.action, '/questions/10/item/');
    const active = state({
        phase: 'question_active', question_id: 11, sequence: 2,
        question: {...state().question, id: 11, round: 2, difficulty: 1, prompt: '<script>texto</script>'},
        items: [{code: 'NUBE', quantity: 1, enabled: false}]
    });
    app.setState(active);
    await app.poll();
    assert.equal(app.reloads(), 0);
    assert.equal(app.ids['clash-answer-form'].action, '/questions/11/answer/');
    assert.equal(app.ids['clash-prompt'].textContent, '<script>texto</script>');
    assert.equal(app.ids['clash-ring'].dataset.questionId, '11');
    assert.equal(app.item.disabled, true);
    assert.equal(app.sceneStates.length, 2);
    app.setState({...active, answered: true});
    await app.poll();
    assert.equal(app.ids['clash-options'].children[0].children[0].disabled, true);
});

test('graphical item waits for server acknowledgement and never consumes optimistically', async () => {
    const original = state({
        phase: 'countdown', question: null, unlocked: true, question_id: 10, sequence: 1,
        items: [{code: 'NUBE', quantity: 2, enabled: true}]
    });
    const app = harness(original, 'countdown', true, true);
    await tick();
    const socket = app.sockets[0];
    socket.listeners.open();
    let prevented = 0;
    app.itemForm.listeners.submit({preventDefault: () => prevented++});
    assert.equal(app.item.disabled, true);
    assert.equal(app.item.textContent, 'Nube (2)');
    assert.equal(app.actionId.value, 'original-action-id');
    assert.deepEqual(socket.sent[1], {type: 'item', question_id: 10, code: 'NUBE', action_id: 'original-action-id'});
    app.itemForm.listeners.submit({preventDefault: () => prevented++});
    assert.equal(socket.sent.length, 2);
    Array.from(app.timers.values()).at(-1)();
    assert.match(app.ids['clash-item-feedback'].textContent, /Recarga para conciliar/);
    assert.equal(socket.sent.length, 2);
    assert.equal(app.item.disabled, true);
    socket.listeners.message({data: JSON.stringify({type: 'ack', action: 'item'})});
    socket.listeners.message({data: JSON.stringify({
        type: 'state', state: {...original, sequence: 2, items: [{code: 'NUBE', quantity: 1, enabled: false}]}
    })});
    assert.equal(app.actionId.value, '07070707-0707-4707-8707-070707070707');
    assert.equal(app.item.textContent, 'Nube (1)');
    assert.equal(prevented, 2);
});

test('graphical item unknown outcome stays locked on reconnect and readiness uses existing socket', async () => {
    const app = harness(state({
        phase: 'waiting_players', question_id: 10, unlocked: true, sequence: 1,
        items: [{code: 'NUBE', quantity: 2, enabled: true}]
    }), 'waiting_players', true, true);
    await tick();
    const socket = app.sockets[0];
    socket.listeners.open();
    app.readyForm.listeners.submit({preventDefault() {}});
    assert.equal(socket.sent[1].type, 'ready');
    app.itemForm.listeners.submit({preventDefault() {}});
    socket.listeners.close({code: 1006});
    await tick();
    assert.equal(app.item.disabled, true);
    assert.match(app.ids['clash-item-feedback'].textContent, /desconocido/);
    assert.equal(socket.sent.filter(message => message.type === 'item').length, 1);
});

test('graphical victory comes only from explicit server resolution, not zero HP', async () => {
    const app = harness(state({
        phase: 'question_active', result: null,
        participants: [{player_id: 1, hp: 0, max_hp: 100}], items: []
    }), 'question_active', false, true);
    await tick();
    assert.equal(app.ids['clash-result'].hidden, true);
    app.setState(state({
        phase: 'finished', question: null,
        participants: [{player_id: 1, hp: 0, max_hp: 100}],
        result: {winner_id: 1, is_draw: false}, items: []
    }));
    await app.poll();
    assert.equal(app.ids['clash-result'].hidden, false);
    assert.match(app.ids['clash-result-text'].textContent, /Ducky uno/);
    assert.match(app.ids['clash-result-text'].textContent, /liquidación por separado/);
});

test('cloud removes already rendered choices and restoration reinstates them', async () => {
    const original = state();
    const app = harness(original);
    await tick();
    app.setState(state({question: {...original.question, options: [], hidden_until: '2026-01-01T00:00:05Z'}}));
    await app.poll();
    assert.equal(app.ids['clash-options'].children.length, 0);
    assert.equal(app.ids['clash-answer'].disabled, true);
    assert.equal(app.ids['clash-cloud'].hidden, false);
    app.setState(original);
    await app.poll();
    assert.equal(app.ids['clash-options'].children.length, 2);
    assert.equal(app.ids['clash-cloud'].hidden, true);
    assert.equal(app.ids['clash-answer'].disabled, false);
});

test('shield preserves a surviving selection and answered/expired states disable actions', async () => {
    const original = state();
    const app = harness(original);
    await tick();
    app.ids['clash-options'].children[0].children[0].checked = true;
    app.setState(state({question: {...original.question, options: [original.question.options[0]]}}));
    await app.poll();
    assert.equal(app.ids['clash-options'].children[0].children[0].checked, true);
    app.setState(state({answered: true}));
    await app.poll();
    assert.equal(app.ids['clash-answer'].disabled, true);
    assert.equal(app.item.disabled, true);
    app.setState(state({question: null}));
    await app.poll();
    assert.equal(app.ids['clash-options'].children.length, 0);
    assert.equal(app.ids['clash-answer'].disabled, true);
});

test('connection failures stop polling explicitly and disable answers', async () => {
    const app = harness(state());
    await tick();
    app.fail();
    await app.poll();
    const calls = app.calls.length;
    app.listeners.visibilitychange();
    await tick();
    assert.equal(app.calls.length, calls);
    assert.equal(app.ids['clash-answer'].disabled, true);
    assert.match(app.ids['clash-sync-status'].textContent, /detenido/);
});

test('next question reloads the Ring and terminal states stop scheduling', async () => {
    const original = state();
    const app = harness(original);
    await tick();
    app.setState(state({question: {...original.question, id: 11}}));
    await app.poll();
    assert.equal(app.reloads(), 1);
    app.setState(state({phase: 'finished', question: null}));
    await app.poll();
    const calls = app.calls.length;
    app.listeners.visibilitychange();
    await tick();
    assert.equal(app.calls.length, calls);
});

test('answer submission stays in Ring and acknowledges no correctness', async () => {
    const app = harness(state());
    await tick();
    app.setState(state({answered: true}));
    let prevented = false;
    await app.ids['clash-answer-form'].listeners.submit({preventDefault: () => { prevented = true; }});
    await tick();
    assert.equal(prevented, true);
    assert.equal(app.calls.filter(call => call.options.method === 'POST').length, 1);
    assert.equal(app.calls.find(call => call.options.method === 'POST').options.credentials, 'same-origin');
    assert.match(app.ids['clash-answer-feedback'].textContent, /Respuesta registrada/);
    assert.equal(app.ids['clash-answer'].disabled, true);
    assert.equal(app.reloads(), 0);
});

test('countdown uses only server time, preserves attacks and resumes ordered events', async () => {
    const original = state({
        phase: 'countdown', question: null, unlocked: true, cursor: 7,
        deadline: '2026-01-01T00:00:03Z'
    });
    const app = harness(original, 'countdown');
    vm.runInNewContext('Date.now = () => 999999999999999;', app.context);
    await tick();
    assert.equal(app.ids['clash-engine-clock'].textContent, 'Cuenta atras: 3');
    assert.equal(app.item.disabled, false);
    assert.equal(app.calls.filter(call => call.url === '/presence/').length, 1);
    for (const seconds of [1, 2]) {
        app.setState({...original, server_time: `2026-01-01T00:00:0${seconds}Z`});
        await app.poll();
        assert.equal(app.ids['clash-engine-clock'].textContent, `Cuenta atras: ${3 - seconds}`);
        assert.equal(app.calls.filter(call => call.url === '/presence/').length, seconds + 1);
    }
    assert.equal(app.calls.filter(call => call.url === '/state/?after=7').length, 2);
    app.setState(state({phase: 'question_active'}));
    await app.poll();
    assert.equal(app.reloads(), 1);
});

test('readiness stays disabled once recorded and presence uses server pacing', async () => {
    const original = state({
        phase: 'waiting_players', question: null, unlocked: true,
        participants: [{player_id: 1, hp: 100, max_hp: 100, ready: true, online: true}]
    });
    const app = harness(original, 'waiting_players');
    await tick();
    assert.equal(app.ids['clash-ready'].disabled, true);
    app.setState({...original, server_time: '2026-01-01T00:00:05Z'});
    await app.poll();
    assert.equal(app.calls.filter(call => call.url === '/presence/').length, 2);
    app.setState({...original, unlocked: false});
    await app.poll();
    assert.equal(app.ids['clash-ready'].disabled, true);
});

test('cloud disables shield until its options become visible', async () => {
    const original = state();
    const app = harness(original);
    app.item.dataset.code = 'ESCUDO';
    await tick();
    app.setState(state({question: {...original.question, options: [], hidden_until: '2026-01-01T00:00:05Z'}}));
    await app.poll();
    assert.equal(app.item.disabled, true);
    app.setState(original);
    await app.poll();
    assert.equal(app.item.disabled, false);
});

test('private websocket recovers cursor and ignores stale snapshots that expose hidden choices', async () => {
    const original = state({sequence: 7, cursor: 7});
    const app = harness(original, '', true);
    await tick();
    const socket = app.sockets[0];
    assert.equal(socket.url, 'wss://example.test/ws/clash/duels/1/');
    socket.listeners.open();
    assert.deepEqual(socket.sent[0], {type: 'sync', after: 7});
    socket.listeners.message({data: JSON.stringify({
        type: 'state', state: state({
            sequence: 8, cursor: 8, server_time: '2026-01-01T00:00:01Z',
            question: {...original.question, options: [], hidden_until: '2026-01-01T00:00:05Z'}
        })
    })});
    assert.equal(app.ids['clash-options'].children.length, 0);
    socket.listeners.message({data: JSON.stringify({type: 'state', state: original})});
    assert.equal(app.ids['clash-options'].children.length, 0);
    assert.equal(app.ids['clash-cloud'].hidden, false);
});

test('websocket permission revocation stops fallback instead of silently reconnecting', async () => {
    const app = harness(state(), '', true);
    await tick();
    const socket = app.sockets[0];
    socket.listeners.open();
    socket.listeners.close({code: 4403});
    const calls = app.calls.length;
    app.listeners.visibilitychange();
    await tick();
    assert.equal(app.calls.length, calls);
    assert.equal(app.ids['clash-answer'].disabled, true);
    assert.match(app.ids['clash-sync-status'].textContent, /permisos/);
});
