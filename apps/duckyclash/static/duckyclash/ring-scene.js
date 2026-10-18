/* Presentation only: no network, scoring, stock mutation or combat decisions. */
(() => {
    'use strict';
    const EFFECTS = {
        NUBE: {label: 'Nube', color: '#64748b', symbol: '~~~'},
        ACELERADOR: {label: 'Acelerador', color: '#f97316', symbol: '- tiempo'},
        MULTIPLICADOR: {label: 'Multiplicador', color: '#7c3aed', symbol: '+ opciones'},
        ESCUDO: {label: 'Escudo', color: '#2563eb', symbol: 'Escudo'},
        POCIMA_TIEMPO: {label: 'Pócima de tiempo', color: '#0d9488', symbol: '+ tiempo'},
        BOTIQUIN: {label: 'Botiquín', color: '#16a34a', symbol: '+'}
    };

    class RingScene {
        constructor(canvas, assets, announce, assetError) {
            this.canvas = canvas;
            this.ctx = canvas.getContext('2d');
            this.assets = assets;
            this.announce = announce;
            this.assetError = assetError;
            this.images = new Map();
            this.animations = new Map();
            this.animationTimers = new Map();
            this.effects = [];
            this.prepared = [];
            this.lastEvent = null;
            this.state = null;
            this.receivedAt = 0;
            this.versusAt = null;
            this.frame = null;
            this.motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
            this.reduced = this.motionPreference.matches;
            this.motionChanged = event => {
                this.reduced = event.matches;
                if (this.reduced && this.frame !== null) {
                    cancelAnimationFrame(this.frame);
                    this.frame = null;
                }
                if (this.state) this.update(this.state);
            };
            if (this.motionPreference.addEventListener) this.motionPreference.addEventListener('change', this.motionChanged);
            this.resizeObserver = null;
            this.resizeHandler = () => {
                if (this.resizeCanvas()) this.draw(performance.now());
            };
            if (typeof ResizeObserver === 'function' && this.canvas.getBoundingClientRect) {
                this.resizeObserver = new ResizeObserver(this.resizeHandler);
                this.resizeObserver.observe(this.canvas);
            } else if (window.addEventListener) {
                window.addEventListener('resize', this.resizeHandler);
            }
            this.resizeCanvas();
            this.load(assets.background);
            assets.fighters.forEach(fighter => {
                this.load(fighter.sprite);
                Object.values(fighter.animations || {}).forEach(animation => this.load(animation.url));
                this.setAnimation(fighter.player_id, 'idle');
            });
            if (!this.ctx) assetError('Canvas no disponible. Usa los controles y el estado HTML.');
        }

        load(url) {
            if (!url || this.images.has(url)) return;
            const image = new Image();
            this.images.set(url, image);
            image.onload = () => this.draw(performance.now());
            image.onerror = () => {
                this.images.delete(url);
                this.assetError('No se pudo cargar un recurso gráfico. Se usa un placeholder; el combate no cambia.');
                this.draw(performance.now());
            };
            image.src = url;
        }

        resizeCanvas() {
            if (!this.canvas.getBoundingClientRect) return false;
            const bounds = this.canvas.getBoundingClientRect();
            if (!bounds.width || !bounds.height) return false;
            const pixelRatio = window.devicePixelRatio || 1;
            const width = Math.round(bounds.width * pixelRatio);
            const height = Math.round(bounds.height * pixelRatio);
            if (this.canvas.width === width && this.canvas.height === height) return false;
            this.canvas.width = width;
            this.canvas.height = height;
            return true;
        }

        effect(code, target, at) {
            if (!EFFECTS[code]) return;
            this.effects.push({code, target, at});
            this.effects = this.effects.slice(-12);
            this.announce(`${EFFECTS[code].label}: efecto confirmado por el servidor.`);
        }

        setAnimation(playerId, name) {
            const fighter = this.assets.fighters.find(player => String(player.player_id) === String(playerId));
            if (!fighter || !fighter.animations || !fighter.animations[name]) return;
            const current = this.animations.get(fighter.player_id);
            if (current && (current.name === 'win' || current.name === 'lose' || current.name === name)) return;
            clearTimeout(this.animationTimers.get(fighter.player_id));
            this.animationTimers.delete(fighter.player_id);
            this.animations.set(fighter.player_id, {
                name, at: null, previous: current && current.at !== null ? current : current && current.previous
            });
        }

        attack(playerId) {
            if (!this.state || this.state.phase !== 'question_active' || this.state.result) return;
            const fighter = this.assets.fighters.find(player => String(player.player_id) === String(playerId));
            if (!fighter) return;
            const current = this.animations.get(fighter.player_id);
            if (current && ['hit', 'dealDamage'].includes(current.name)) return;
            this.setAnimation(playerId, 'attack');
            if (this.reduced) this.draw(performance.now());
        }

        drawCharacter(fighter, x, now) {
            let animation = this.animations.get(fighter.player_id);
            if (!animation) return false;
            let clip = fighter.animations[animation.name];
            let image = this.images.get(clip.url);
            const loaded = image && image.complete && image.naturalWidth;
            if (loaded && animation.at === null) {
                animation.at = now;
            }
            if (loaded && this.reduced && ['attack', 'hit', 'dealDamage'].includes(animation.name)
                && !this.animationTimers.has(fighter.player_id)) {
                this.animationTimers.set(fighter.player_id, setTimeout(() => {
                    this.animationTimers.delete(fighter.player_id);
                    this.draw(performance.now());
                }, Math.max(0, clip.frameCount * clip.frameDuration - (now - animation.at))));
            }
            if (loaded && ['attack', 'hit', 'dealDamage'].includes(animation.name)
                && now - animation.at >= clip.frameCount * clip.frameDuration) {
                this.setAnimation(fighter.player_id, 'idle');
                animation = this.animations.get(fighter.player_id);
                clip = fighter.animations.idle;
                image = this.images.get(clip.url);
                if (image && image.complete && image.naturalWidth) animation.at = now;
            }
            const drawClip = (entry, alpha) => {
                const definition = fighter.animations[entry.name];
                const bitmap = this.images.get(definition.url);
                if (!bitmap || !bitmap.complete || !bitmap.naturalWidth) return false;
                const frame = this.reduced ? 0
                    : Math.floor(Math.max(0, now - entry.at) / definition.frameDuration) % definition.frameCount;
                const frameWidth = bitmap.naturalWidth / definition.frameCount;
                const frameHeight = bitmap.naturalHeight;
                const height = 310;
                const width = height * frameWidth / frameHeight;
                this.ctx.save();
                this.ctx.globalAlpha = alpha;
                this.ctx.drawImage(bitmap, frame * frameWidth, 0, frameWidth, frameHeight,
                    x - width / 2, 62, width, height);
                this.ctx.restore();
                return true;
            };
            if (!image || !image.complete || !image.naturalWidth) {
                return animation.previous ? drawClip(animation.previous, 1) : false;
            }
            const blend = this.reduced ? 1 : Math.min(1, (now - animation.at) / 160);
            if (animation.previous && blend < 1) drawClip(animation.previous, 1 - blend);
            const drawn = drawClip(animation, animation.previous ? blend : 1);
            if (blend >= 1) animation.previous = null;
            return drawn;
        }

        update(state) {
            const now = performance.now();
            const previous = this.state;
            this.state = state;
            this.receivedAt = now;
            if (this.lastEvent === null) {
                // Reconnection shows current state, not historical consumptions/animations.
                this.lastEvent = state.sequence || 0;
            } else {
                for (const event of state.events || []) {
                    if (event.sequence <= this.lastEvent) continue;
                    this.lastEvent = event.sequence;
                    if (Date.parse(state.server_time) - Date.parse(event.at) > 8000) continue;
                    if (event.kind === 'item.prepared' && EFFECTS[event.payload.code]) {
                        this.prepared.push({code: event.payload.code, target: event.payload.target_id});
                        this.prepared = this.prepared.slice(-12);
                        this.announce(`${EFFECTS[event.payload.code].label}: ataque preparado, pendiente de apertura.`);
                    }
                    if (event.kind === 'item.used') this.effect(event.payload.code, event.payload.target_id, now);
                    if (event.kind === 'answer.locked') this.attack(event.actor_id);
                }
            }
            if (previous && previous.phase === 'countdown' && state.phase === 'question_active') {
                this.versusAt = now;
            }
            if (previous && previous.phase !== state.phase && state.phase === 'question_active') {
                this.prepared.forEach(effect => this.effect(effect.code, effect.target, now));
                this.prepared = [];
            }
            if (previous) {
                const damaged = new Set(state.participants.filter(player => {
                    const before = previous.participants.find(item => item.player_id === player.player_id);
                    return before && player.hp < before.hp;
                }).map(player => player.player_id));
                damaged.forEach(playerId => this.setAnimation(playerId, 'hit'));
                if (damaged.size) {
                    state.participants.filter(player => !damaged.has(player.player_id))
                        .forEach(player => this.setAnimation(player.player_id, 'dealDamage'));
                }
                state.participants.forEach(player => {
                    const before = previous.participants.find(item => item.player_id === player.player_id);
                    if (before && before.hp !== player.hp) {
                        this.effects.push({
                            code: player.hp < before.hp ? 'damage' : 'healing',
                            target: player.player_id, at: now, amount: player.hp - before.hp
                        });
                    }
                });
                if (state.result && !state.result.is_draw && !previous.result) {
                    this.effects.push({code: 'victory', target: state.result.winner_id, at: now});
                }
            }
            if (state.result && !state.result.is_draw) {
                state.participants.forEach(player => this.setAnimation(player.player_id,
                    player.player_id === state.result.winner_id ? 'win' : 'lose'));
            }
            this.effects = this.effects.slice(-16);
            this.draw(now);
            if (!this.reduced && !document.hidden && this.frame === null && this.ctx) {
                this.frame = requestAnimationFrame(time => this.animate(time));
            }
        }

        animate(now) {
            this.frame = null;
            this.draw(now);
            if (!document.hidden && !this.reduced && this.state) {
                this.frame = requestAnimationFrame(time => this.animate(time));
            } else if (!document.hidden && !this.reduced && this.effects.some(effect => now - effect.at < 1800)) {
                this.frame = requestAnimationFrame(time => this.animate(time));
            }
        }

        dispose() {
            if (this.frame !== null) cancelAnimationFrame(this.frame);
            this.frame = null;
            this.animationTimers.forEach(timer => clearTimeout(timer));
            this.animationTimers.clear();
            if (this.resizeObserver) this.resizeObserver.disconnect();
            if (window.removeEventListener) window.removeEventListener('resize', this.resizeHandler);
            if (this.motionPreference.removeEventListener) this.motionPreference.removeEventListener('change', this.motionChanged);
        }

        ellipse(x, y, rx, ry, color) {
            const ctx = this.ctx;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
            ctx.fill();
        }

        placeholder(x, y) {
            this.ellipse(x, y + 12, 42, 58, '#facc15');
            this.ellipse(x, y - 57, 46, 43, '#fde047');
            this.ellipse(x - 17, y - 65, 7, 10, '#1e293b');
            this.ellipse(x + 17, y - 65, 7, 10, '#1e293b');
            this.ellipse(x, y - 38, 22, 9, '#fb923c');
            this.ellipse(x - 23, y + 77, 27, 10, '#fb923c');
            this.ellipse(x + 23, y + 77, 27, 10, '#fb923c');
        }

        draw(now) {
            if (!this.ctx) return;
            const ctx = this.ctx;
            const scale = this.canvas.height / 420;
            const width = this.canvas.width / scale;
            const height = this.canvas.height / scale;
            if (ctx.setTransform) ctx.setTransform(scale, 0, 0, scale, 0, 0);
            ctx.clearRect(0, 0, width, height);
            const background = this.images.get(this.assets.background);
            if (background && background.complete && background.naturalWidth) {
                const scale = Math.max(width / background.naturalWidth, height / background.naturalHeight) * 1.2;
                const w = background.naturalWidth * scale, h = background.naturalHeight * scale;
                ctx.drawImage(background, (width - w) / 2, (height - h) / 2, w, h);
                ctx.fillStyle = 'rgba(15,23,42,.12)';
                ctx.fillRect(0, 0, width, height);
            } else {
                const gradient = ctx.createLinearGradient(0, 0, 0, height);
                gradient.addColorStop(0, '#dbeafe');
                gradient.addColorStop(1, '#94a3b8');
                ctx.fillStyle = gradient;
                ctx.fillRect(0, 0, width, height);
            }
            this.ellipse(width / 2, height - 35, width * .44, 38, 'rgba(15,23,42,.2)');
            this.assets.fighters.forEach((fighter, index) => {
                const x = width * (index ? .73 : .27);
                const state = this.state && this.state.participants.find(player => player.player_id === fighter.player_id);
                const effects = this.effects.filter(effect => effect.target === fighter.player_id && now - effect.at < 1800);
                const damaged = effects.some(effect => effect.code === 'damage');
                const offset = this.reduced ? 0 : damaged ? Math.sin(now / 45) * 7 : Math.sin(now / 450 + index) * 3;
                ctx.save();
                ctx.translate(offset, 0);
                const image = this.images.get(fighter.sprite);
                if (!this.drawCharacter(fighter, x, now)) {
                    if (image && image.complete && image.naturalWidth) {
                        ctx.drawImage(image, x - 110, 62, 220, 310);
                    } else {
                        this.placeholder(x, 253);
                    }
                }
                effects.forEach(effect => this.drawEffect(effect, x, now));
                if (state && this.state.question && this.state.question.hidden_until
                    && String(fighter.player_id) === this.canvas.dataset.playerId) {
                    this.drawEffect({code: 'NUBE', at: now}, x, now);
                }
                ctx.restore();
            });
            ctx.textAlign = 'center';
            const state = this.state;
            if (state) {
                const seconds = state.deadline
                    ? Math.max(0, Math.ceil((Date.parse(state.deadline) - Date.parse(state.server_time) - (now - this.receivedAt)) / 1000))
                    : null;
                if (state.phase === 'countdown') {
                    ctx.save();
                    ctx.textAlign = 'center';
                    ctx.font = '900 76px sans-serif';
                    ctx.shadowColor = '#f97316';
                    ctx.shadowBlur = 24;
                    ctx.fillStyle = '#facc15';
                    ctx.fillText(String(seconds), width / 2, height * .28);
                    ctx.restore();
                } else if (this.versusAt !== null && now - this.versusAt < 1600) {
                    const elapsed = (now - this.versusAt) / 1600;
                    const pulse = this.reduced ? 1 : .8 + Math.sin(elapsed * Math.PI * 5) * .2;
                    ctx.save();
                    ctx.globalAlpha = this.reduced ? 1 : Math.max(0, 1 - elapsed * .28);
                    ctx.textAlign = 'center';
                    ctx.font = `italic 900 ${Math.round(120 * pulse)}px Impact, sans-serif`;
                    ctx.shadowColor = '#ff2d00';
                    ctx.shadowBlur = 40 * pulse;
                    ctx.fillStyle = '#b91c1c';
                    ctx.fillText('VS', width / 2, height * .55 + 10, width * .5);
                    ctx.shadowColor = '#facc15';
                    ctx.shadowBlur = 24 * pulse;
                    ctx.fillStyle = '#fff7ad';
                    ctx.fillText('VS', width / 2, height * .55, width * .5);
                    ctx.restore();
                }
                if (state.result) {
                    const winner = this.assets.fighters.find(fighter => fighter.player_id === state.result.winner_id);
                    const label = state.result.is_draw ? 'Empate' : winner ? `Victoria: ${winner.name}` : 'Resultado registrado';
                    ctx.fillStyle = 'rgba(15,23,42,.9)';
                    ctx.fillRect(0, height - 38, width, 38);
                    ctx.fillStyle = '#fef3c7';
                    ctx.fillText(label, width / 2, height - 12, width - 24);
                }
            }
        }

        drawEffect(effect, x, now) {
            const ctx = this.ctx;
            const definition = EFFECTS[effect.code];
            const elapsed = this.reduced ? 0 : Math.min(1, (now - effect.at) / 1800);
            ctx.save();
            ctx.globalAlpha = 1 - elapsed * .7;
            ctx.textAlign = 'center';
            ctx.font = 'bold 24px sans-serif';
            ctx.fillStyle = definition ? definition.color : effect.code === 'damage' ? '#dc2626' : '#16a34a';
            if (effect.code === 'NUBE') {
                [-40, 0, 40].forEach(offset => this.ellipse(x + offset, 130, 40, 25, '#cbd5e1'));
            } else if (effect.code === 'ESCUDO') {
                ctx.strokeStyle = definition.color;
                ctx.lineWidth = 8;
                ctx.beginPath();
                ctx.ellipse(x, 220, 120, 160, 0, 0, Math.PI * 2);
                ctx.stroke();
            } else if (effect.code === 'damage') {
                ctx.fillText(String(effect.amount), x, 140 - elapsed * 40);
            } else if (effect.code === 'healing') {
                ctx.fillText(`+${effect.amount}`, x, 140 - elapsed * 40);
            } else if (effect.code === 'victory') {
                ctx.fillStyle = '#f59e0b';
                for (let index = 0; index < 8; index++) {
                    const angle = index * Math.PI / 4;
                    const radius = 60 + elapsed * 80;
                    ctx.fillRect(x + Math.cos(angle) * radius, 130 + Math.sin(angle) * radius, 9, 9);
                }
            } else if (['ACELERADOR', 'MULTIPLICADOR'].includes(effect.code)) {
                const start = x < this.canvas.width / 2 ? this.canvas.width * .73 : this.canvas.width * .27;
                const progress = this.reduced ? 1 : Math.min(1, elapsed * 3);
                this.ellipse(start + (x - start) * progress, 180, 12, 12, definition.color);
                ctx.fillText(definition.symbol, x, 140 - elapsed * 40);
            } else {
                ctx.fillText(definition.symbol, x, 140 - elapsed * 40);
            }
            ctx.restore();
        }
    }

    if (typeof module !== 'undefined') module.exports = {RingScene, EFFECTS};
    if (typeof window !== 'undefined') window.DuckyClashScene = RingScene;
})();
