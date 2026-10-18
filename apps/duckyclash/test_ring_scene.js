const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function harness(reduced = false, canvasAvailable = true, animated = false) {
    const text = [];
    const draws = [];
    const opacities = [];
    const timers = new Map();
    let timerId = 0;
    const announces = [];
    const errors = [];
    const frames = [];
    const imageInstances = [];
    const motion = {
        matches: reduced,
        addEventListener: (_, callback) => { motion.change = callback; },
        removeEventListener: () => { motion.change = null; }
    };
    const draw = {
        clearRect() {}, fillRect() {}, drawImage(...args) {
            draws.push(args);
            opacities.push(draw.globalAlpha);
        }, beginPath() {}, ellipse() {}, fill() {},
        save() {}, restore() {}, translate() {}, stroke() {}, setTransform() {},
        createLinearGradient: () => ({addColorStop() {}}),
        fillText: value => text.push(value)
    };
    const canvas = {width: 1000, height: 420, dataset: {playerId: '1'}, getContext: () => canvasAvailable ? draw : null};
    let now = 100;
    const context = {
        module: {exports: {}},
        window: {matchMedia: () => motion}, document: {hidden: false},
        performance: {now: () => now},
        requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
        cancelAnimationFrame() {},
        setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        Image: class {
            constructor() { imageInstances.push(this); this.complete = false; this.naturalWidth = 0; }
        }
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'static', 'duckyclash', 'ring-scene.js'), 'utf8'), context);
    const assets = {
        background: '/actual-landscape.png',
        fighters: [
            {player_id: 1, name: 'Ducky A', sprite: '/duck-front.svg', hp: 100, max_hp: 100, accent: '#2563eb'},
            {player_id: 2, name: 'Ducky B', sprite: '/duck-front.svg', hp: 100, max_hp: 100, accent: '#7c3aed'}
        ]
    };
    if (animated) {
        assets.fighters.forEach((fighter, index) => {
            fighter.animations = Object.fromEntries(['idle', 'attack', 'hit', 'dealDamage', 'win', 'lose']
                .map(name => [name, {url: `/PRE${index + 1}_${name}.webp`, frameCount: 4, frameDuration: 380}]));
        });
    }
    const scene = new context.module.exports.RingScene(canvas, assets, message => announces.push(message), message => errors.push(message));
    if (animated) imageInstances.forEach(image => {
        image.complete = true;
        image.naturalWidth = 2172;
        image.naturalHeight = 724;
    });
    return {scene, text, draws, opacities, timers, announces, errors, frames, imageInstances, context, motion, time: value => { now = value; }};
}

const state = overrides => ({
    sequence: 1, phase: 'countdown', server_time: '2026-10-14T10:00:00Z',
    deadline: '2026-10-14T10:00:03Z', question: null, result: null, events: [],
    participants: [{player_id: 1, hp: 100, max_hp: 100}, {player_id: 2, hp: 100, max_hp: 100}],
    ...overrides
});

test('animated fighters default to idle, advance frames and crossfade back after one attack cycle', () => {
    const app = harness(false, true, true);
    app.scene.update(state({phase: 'question_active'}));
    assert.equal(app.scene.animations.get(1).name, 'idle');
    app.scene.draw(480);
    assert.ok(app.draws.some(args => args.length === 9 && args[1] === 543));
    app.scene.attack('1');
    app.scene.draw(500);
    const attack = app.scene.animations.get(1);
    assert.equal(attack.name, 'attack');
    assert.equal(attack.previous.name, 'idle');
    assert.equal(attack.at, 500);
    app.scene.draw(580);
    assert.ok(app.opacities.includes(.5));
    app.scene.draw(2020);
    assert.equal(app.scene.animations.get(1).name, 'idle');
    const frames = app.draws.filter(args => args.length === 9);
    assert.ok(frames.every(args => args[7] === 232.5 && args[8] === 310));
});

test('reduced motion returns temporary poses to idle even without polling or animation frames', () => {
    const app = harness(true, true, true);
    app.scene.update(state({phase: 'question_active'}));
    app.scene.attack(1);
    app.scene.draw(100);
    assert.equal(app.frames.length, 0);
    assert.equal(app.timers.size, 1);
    app.time(1620);
    app.timers.values().next().value();
    assert.equal(app.scene.animations.get(1).name, 'idle');
    app.scene.dispose();
    assert.equal(app.scene.animationTimers.size, 0);
});

test('a loading transition keeps its previous pose until the next strip is ready', () => {
    const app = harness(false, true, true);
    app.scene.update(state({phase: 'question_active'}));
    const attackImage = app.scene.images.get('/PRE1_attack.webp');
    attackImage.complete = false;
    app.scene.attack(1);
    app.scene.draw(1000);
    assert.equal(app.scene.animations.get(1).at, null);
    assert.equal(app.scene.animations.get(1).previous.name, 'idle');
    attackImage.complete = true;
    app.scene.draw(2000);
    assert.equal(app.scene.animations.get(1).at, 2000);
    app.scene.draw(3520);
    assert.equal(app.scene.animations.get(1).name, 'idle');
});

test('confirmed HP changes trigger hit and dealDamage, then return to idle without replay', () => {
    const app = harness(false, true, true);
    app.scene.update(state());
    const snapshot = state({phase: 'round_result', participants: [
        {player_id: 1, hp: 75, max_hp: 100}, {player_id: 2, hp: 100, max_hp: 100}
    ]});
    app.scene.update(snapshot);
    assert.equal(app.scene.animations.get(1).name, 'hit');
    assert.equal(app.scene.animations.get(2).name, 'dealDamage');
    app.scene.draw(1620);
    assert.equal(app.scene.animations.get(1).name, 'idle');
    assert.equal(app.scene.animations.get(2).name, 'idle');
    app.time(1700);
    app.scene.update(snapshot);
    assert.equal(app.scene.animations.get(1).name, 'idle');
});

test('only an explicit server winner selects persistent win and lose, including reconnects', () => {
    const app = harness(false, true, true);
    app.scene.update(state({phase: 'finished', participants: [
        {player_id: 1, hp: 0, max_hp: 100}, {player_id: 2, hp: 100, max_hp: 100}
    ]}));
    assert.equal(app.scene.animations.get(1).name, 'idle');
    app.scene.update(state({phase: 'finished', result: {winner_id: 2, is_draw: false}}));
    app.scene.draw(10000);
    app.scene.attack(1);
    assert.equal(app.scene.animations.get(1).name, 'lose');
    assert.equal(app.scene.animations.get(2).name, 'win');
    const draw = harness(false, true, true);
    draw.scene.update(state({phase: 'finished', result: {is_draw: true}}));
    assert.equal(draw.scene.animations.get(1).name, 'idle');
});

test('answer events animate the actor once and reconnect does not replay historical actions', () => {
    const event = {sequence: 2, kind: 'answer.locked', actor_id: 2, at: '2026-10-14T10:00:00Z', payload: {round: 1}};
    const app = harness(false, true, true);
    app.scene.update(state({phase: 'question_active'}));
    app.scene.update(state({phase: 'question_active', sequence: 2, events: [event]}));
    assert.equal(app.scene.animations.get(2).name, 'attack');
    app.scene.draw(1620);
    app.time(1800);
    app.scene.update(state({phase: 'question_active', sequence: 2, events: [event]}));
    assert.equal(app.scene.animations.get(2).name, 'idle');
    const reconnect = harness(false, true, true);
    reconnect.scene.update(state({phase: 'question_active', sequence: 2, events: [event]}));
    assert.equal(reconnect.scene.animations.get(2).name, 'idle');
});

test('renderer only reads snapshots and never derives victory from HP', () => {
    const app = harness();
    const snapshot = state({phase: 'finished', participants: [
        {player_id: 1, hp: 0, max_hp: 100}, {player_id: 2, hp: 90, max_hp: 100}
    ]});
    const before = JSON.stringify(snapshot);
    app.scene.update(snapshot);
    assert.equal(JSON.stringify(snapshot), before);
    assert.equal(app.text.includes('Ducky A'), false);
    assert.equal(app.text.includes('HP 0 / 100'), false);
    assert.equal(app.text.some(text => text.startsWith('Victoria:')), false);
    app.scene.update({...snapshot, result: {winner_id: 2, is_draw: false}});
    assert.ok(app.text.includes('Victoria: Ducky B'));
    assert.equal(app.scene.effects.filter(effect => effect.code === 'victory').length, 1);
});

test('versus flash appears only briefly when countdown opens the question phase', () => {
    const app = harness();
    app.scene.update(state());
    app.scene.update(state({phase: 'question_active'}));
    assert.ok(app.text.includes('VS'));
    app.text.length = 0;
    app.scene.draw(2000);
    assert.equal(app.text.includes('VS'), false);
});

test('six object animations require confirmed ordered events and deduplicate replay', () => {
    const app = harness();
    app.scene.update(state());
    const codes = ['NUBE', 'ACELERADOR', 'MULTIPLICADOR', 'ESCUDO', 'POCIMA_TIEMPO', 'BOTIQUIN'];
    const snapshot = state({
        sequence: 7, phase: 'question_active',
        events: codes.map((code, index) => ({
            sequence: index + 2, kind: 'item.used', at: '2026-10-14T10:00:00Z',
            payload: {code, target_id: 2}
        }))
    });
    app.scene.update(snapshot);
    assert.equal(app.scene.effects.length, 6);
    assert.equal(app.announces.length, 6);
    app.scene.update(snapshot);
    assert.equal(app.scene.effects.length, 6);
    assert.equal(app.announces.length, 6);
});

test('prepared attack animation waits for active question and reconnect skips old events', () => {
    const app = harness();
    const prepared = {sequence: 2, kind: 'item.prepared', at: '2026-10-14T10:00:00Z', payload: {code: 'NUBE', target_id: 2}};
    app.scene.update(state());
    app.scene.update(state({sequence: 2, events: [prepared]}));
    assert.equal(app.scene.effects.length, 0);
    assert.equal(app.scene.prepared.length, 1);
    app.scene.update(state({sequence: 3, phase: 'question_active'}));
    assert.equal(app.scene.effects.length, 1);
    assert.equal(app.scene.prepared.length, 0);
    const reconnect = harness();
    reconnect.scene.update(state({sequence: 3, phase: 'question_active', events: [prepared]}));
    assert.equal(reconnect.scene.effects.length, 0);
    assert.equal(reconnect.scene.prepared.length, 0);
});

test('damage and healing animate only observed server HP differences', () => {
    const app = harness();
    app.scene.update(state());
    const next = state({participants: [{player_id: 1, hp: 50, max_hp: 100}, {player_id: 2, hp: 100, max_hp: 100}]});
    app.scene.update(next);
    assert.equal(app.scene.effects[0].code, 'damage');
    assert.equal(app.scene.effects[0].amount, -50);
    app.scene.update(state());
    assert.equal(app.scene.effects[1].code, 'healing');
    assert.equal(app.scene.effects[1].amount, 50);
    assert.equal(next.participants[0].hp, 50);
});

test('reduced motion uses static rendering and image errors produce explicit placeholder notice', () => {
    const app = harness(true);
    app.scene.update(state());
    assert.equal(app.frames.length, 0);
    assert.equal(app.imageInstances.length, 2);
    app.imageInstances[0].onerror();
    assert.match(app.errors[0], /placeholder/);
    assert.equal(app.scene.images.has('/actual-landscape.png'), false);
    assert.equal(app.text.includes('Ducky A'), false);
});

test('canvas failure leaves HTML interaction possible and reports failure', () => {
    const app = harness(false, false);
    app.scene.update(state());
    assert.match(app.errors[0], /Canvas no disponible/);
    assert.equal(app.frames.length, 0);
});

test('motion preference changes cancel animation and disposal removes listener', () => {
    const app = harness();
    app.scene.update(state());
    assert.notEqual(app.scene.frame, null);
    app.motion.change({matches: true});
    assert.equal(app.scene.frame, null);
    assert.equal(app.scene.reduced, true);
    app.motion.change({matches: false});
    assert.notEqual(app.scene.frame, null);
    app.scene.dispose();
    assert.equal(app.scene.frame, null);
    assert.equal(app.motion.change, null);
});
