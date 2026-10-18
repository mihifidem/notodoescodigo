(() => {
    'use strict';
    const ring = document.getElementById('clash-ring');
    if (!ring) return;
    const status = document.getElementById('clash-sync-status');
    const options = document.getElementById('clash-options');
    const questionPanel = document.getElementById('clash-question');
    const questionClose = document.getElementById('clash-question-close');
    const questionOpen = document.getElementById('clash-question-open');
    const answer = document.getElementById('clash-answer');
    const cloud = document.getElementById('clash-cloud');
    const answerForm = document.getElementById('clash-answer-form');
    const feedback = document.getElementById('clash-answer-feedback');
    const presence = document.getElementById('clash-presence');
    const engineClock = document.getElementById('clash-engine-clock');
    const ready = document.getElementById('clash-ready');
    const graphical = ring.dataset.canvasRing === 'true';
    const itemFeedback = document.getElementById('clash-item-feedback');
    const fighterCanvas = document.getElementById('clash-canvas');
    const fighterDialog = document.getElementById('clash-fighter-video-dialog');
    const fighterVideo = document.getElementById('clash-fighter-video');
    const fighterVideoStatus = ring.querySelector('[data-fighter-video-status]');
    let pendingItem = null;
    let pendingItemTimer;
    let scene;
    let assets;
    if (graphical && typeof window.DuckyClashScene === 'function') {
        assets = JSON.parse(document.getElementById('clash-assets').textContent);
        scene = new window.DuckyClashScene(
            document.getElementById('clash-canvas'),
            assets,
            text => { document.getElementById('clash-effect-status').textContent = text; },
            text => { document.getElementById('clash-assets-status').textContent = text; }
        );
    }
    let eventCursor = 0;
    let lastHeartbeat = -Infinity;
    let optionsSignature = '';
    let timer;
    let refreshing = false;
    let stopped = false;
    let sendingAnswer = false;
    let socket;
    let socketOpen = false;
    let reconnects = 0;
    let leaving = false;
    let latestState;
    let lastSequence = -1;
    let lastServerTime = -Infinity;

    if (fighterCanvas && fighterDialog && fighterVideo) {
        const openFighterVideo = () => {
            if (typeof fighterDialog.showModal !== 'function') {
                fighterVideoStatus.textContent = 'Tu navegador no admite la ventana de vídeo.';
                return;
            }
            if (!fighterDialog.open) fighterDialog.showModal();
            fighterVideoStatus.textContent = '';
            fighterVideo.play().catch(() => {
                fighterVideoStatus.textContent = 'Pulsa reproducir para iniciar el vídeo.';
            });
        };
        fighterCanvas.addEventListener('click', event => {
            const bounds = fighterCanvas.getBoundingClientRect();
            if (!bounds.width || !bounds.height) return;
            const scale = Math.max(bounds.width / fighterCanvas.width, bounds.height / fighterCanvas.height);
            const offsetX = (fighterCanvas.width * scale - bounds.width) / 2;
            const offsetY = (fighterCanvas.height * scale - bounds.height) / 2;
            const x = (event.clientX - bounds.left + offsetX) / scale;
            const y = (event.clientY - bounds.top + offsetY) / scale;
            const clickedFighter = [fighterCanvas.width * .27, fighterCanvas.width * .73].some(center =>
                x >= center - 110 && x <= center + 110 && y >= 62 && y <= 372
            );
            if (clickedFighter) openFighterVideo();
        });
        fighterCanvas.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openFighterVideo();
            }
        });
        fighterDialog.querySelector('[data-close-fighter-video]').addEventListener('click', () => {
            fighterDialog.close();
        });
        fighterDialog.addEventListener('click', event => {
            if (event.target === fighterDialog) fighterDialog.close();
        });
        fighterDialog.addEventListener('close', () => {
            fighterVideo.pause();
            if (fighterVideo.readyState >= HTMLMediaElement.HAVE_METADATA) fighterVideo.currentTime = 0;
        });
        fighterVideo.addEventListener('error', () => {
            fighterVideoStatus.textContent = 'No se pudo cargar el vídeo del personaje.';
        });
    }

    function setQuestionPanelVisible(visible, moveFocus = false) {
        questionPanel.hidden = !visible;
        questionPanel.style.display = visible ? '' : 'none';
        questionClose.setAttribute('aria-expanded', String(visible));
        questionOpen.hidden = visible;
        questionOpen.setAttribute('aria-expanded', String(visible));
        if (moveFocus) (visible ? questionClose : questionOpen).focus();
    }

    questionClose.addEventListener('click', () => setQuestionPanelVisible(false, true));
    questionOpen.addEventListener('click', () => setQuestionPanelVisible(true, true));
    if (answerForm) answerForm.addEventListener('change', () => {
        if (scene && !answer.disabled) scene.attack(ring.dataset.playerId);
    });

    function heartbeatDue(state) {
        const interval = state.phase === 'countdown' ? 1000 : 5000;
        return Date.parse(state.server_time) - lastHeartbeat >= interval;
    }

    function renderQuestion(question, serverTime, answered, phase) {
        if (!graphical && question && String(question.id) !== ring.dataset.questionId) {
            location.reload();
            return;
        }
        ring.querySelectorAll('[data-clash-item]').forEach(button => {
            if (graphical) {
                const entry = (latestState.items || []).find(item => item.code === button.dataset.code);
                button.disabled = pendingItem !== null || stopped || !entry || !entry.enabled || entry.quantity < 1;
                if (entry) button.textContent = `${button.dataset.name} (${entry.quantity})`;
                const form = button.closest('form');
                const questionId = latestState.question_id || (question && question.id);
                if (questionId) form.action = ring.dataset.itemTemplate.replace('/0/', `/${questionId}/`);
                return;
            }
            button.disabled = phase === 'countdown' ? false
                : !question || answered || (button.dataset.code === 'ESCUDO' && Boolean(question.hidden_until));
        });
        if (!options) return;
        const hidden = Boolean(question && question.hidden_until);
        cloud.hidden = !hidden;
        answer.disabled = !question || hidden || answered || sendingAnswer;
        if (graphical) {
            const nextId = question ? String(question.id) : '';
            if (nextId !== ring.dataset.questionId) {
                if (nextId) setQuestionPanelVisible(true);
                optionsSignature = '';
                feedback.textContent = '';
                ring.dataset.questionId = nextId;
                options.replaceChildren();
            }
            document.getElementById('clash-question-title').textContent = question
                ? `Ronda ${question.round} · Dificultad ${question.difficulty}` : 'Pregunta central';
            document.getElementById('clash-prompt').textContent = question ? question.prompt : 'Esperando una pregunta autorizada del servidor.';
            if (question) answerForm.action = ring.dataset.answerTemplate.replace('/0/', `/${question.id}/`);
        }
        const values = question ? question.options : [];
        const signature = JSON.stringify(values);
        if (signature !== optionsSignature) {
            const selected = options.querySelector('input:checked');
            const selectedId = selected ? selected.value : '';
            options.replaceChildren();
            values.forEach(option => {
                const wrapper = document.createElement('div');
                wrapper.className = 'form-check mb-2';
                const input = document.createElement('input');
                input.type = 'radio';
                input.name = 'option_id';
                input.value = option.id;
                input.id = `option-${option.id}`;
                input.required = true;
                input.checked = option.id === selectedId;
                input.className = 'form-check-input';
                const label = document.createElement('label');
                label.htmlFor = input.id;
                label.className = 'form-check-label text-break';
                label.textContent = option.text;
                wrapper.append(input, label);
                options.append(wrapper);
            });
            optionsSignature = signature;
        }
        if (graphical) options.querySelectorAll('input').forEach(input => { input.disabled = answer.disabled; });
        const clock = document.getElementById('clash-clock');
        clock.textContent = question
            ? `Tiempo aproximado: ${Math.max(0, Math.ceil((Date.parse(question.closes_at) - Date.parse(serverTime)) / 1000))} s. El servidor valida el plazo.`
            : 'Tu plazo ha terminado. Esperando al servidor.';
    }

    function renderState(state) {
            if (!Array.isArray(state.participants)) throw new Error('Respuesta no valida');
            if (typeof state.sequence === 'number') {
                const serverTime = Date.parse(state.server_time);
                if (state.sequence < lastSequence || serverTime < lastServerTime) return true;
                lastSequence = state.sequence;
                lastServerTime = serverTime;
            }
            if (!graphical && ring.dataset.enginePhase && ring.dataset.enginePhase !== state.phase) {
                location.reload();
                return false;
            }
            latestState = state;
            if (typeof state.cursor === 'number') eventCursor = state.cursor;
            state.participants.forEach(player => {
                const health = ring.querySelector(`[data-health-player="${player.player_id}"]`);
                if (health) health.textContent = `HP: ${player.hp} / ${player.max_hp}`;
                if (graphical) {
                    const bar = ring.querySelector(`[data-hp-bar="${player.player_id}"]`);
                    if (bar) { bar.max = player.max_hp; bar.value = player.hp; }
                }
                if (ring.dataset.enginePhase) {
                    const connection = ring.querySelector(`[data-presence-player="${player.player_id}"]`);
                    if (connection) connection.textContent = `${player.ready ? 'Listo' : 'No preparado'} - ${player.online ? 'En linea' : 'Desconectado'}`;
                }
            });
            status.textContent = state.status;
            if (engineClock) {
                const seconds = state.deadline ? Math.max(0, Math.ceil((Date.parse(state.deadline) - Date.parse(state.server_time)) / 1000)) : 0;
                engineClock.textContent = state.phase === 'countdown' ? `Cuenta atras: ${seconds}` : state.status;
            }
            if (ready && (graphical || state.phase === 'waiting_players')) {
                const self = state.participants.find(player => String(player.player_id) === ring.dataset.playerId);
                ready.disabled = !state.unlocked || state.phase !== 'waiting_players'
                    || ['finished', 'canceled', 'no_show', 'settlement_pending'].includes(state.economic_status)
                    || Boolean(self && self.ready);
            }
            const engineError = document.getElementById('clash-engine-error');
            if (engineError) engineError.textContent = state.error ? 'El motor necesita conciliar o recuperar un proveedor. No se ha inventado un resultado.' : '';
            renderQuestion(state.question, state.server_time, state.answered, state.phase);
            if (graphical) {
                if (scene) scene.update(state);
                const result = document.getElementById('clash-result');
                result.hidden = !state.result && !['finished', 'canceled', 'no_show', 'settlement_pending'].includes(state.phase);
                const winner = state.result && assets && assets.fighters.find(player => player.player_id === state.result.winner_id);
                document.getElementById('clash-result-text').textContent = state.result
                    ? state.result.is_draw ? 'Empate registrado por Django. Consulta la devolución.'
                    : `Ganador registrado: ${winner ? winner.name : 'consulta el resultado'}. Consulta la liquidación por separado.`
                    : 'Combate cerrado o pendiente de liquidación; consulta el detalle.';
            }
            if (['finished', 'canceled'].includes(state.phase)) stopped = true;
            return true;
    }

    async function refresh() {
        if (refreshing || stopped) return;
        clearTimeout(timer);
        refreshing = true;
        try {
            const separator = ring.dataset.stateUrl.includes('?') ? '&' : '?';
            const response = await fetch(`${ring.dataset.stateUrl}${separator}after=${eventCursor}`, {
                credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}
            });
            if (!response.ok || response.redirected) throw new Error('Estado no disponible');
            const state = await response.json();
            if (!renderState(state)) return;
            if (presence && state.unlocked && state.phase !== 'finished' && heartbeatDue(state)) {
                const heartbeat = await fetch(presence.action, {
                    method: 'POST', body: new FormData(presence), credentials: 'same-origin',
                    headers: {'Accept': 'application/json'}
                });
                if (!heartbeat.ok || heartbeat.redirected) throw new Error('Presencia no confirmada');
                lastHeartbeat = Date.parse(state.server_time);
            }
            if (!document.hidden && !stopped) timer = setTimeout(refresh, socketOpen ? 15000 : (ring.dataset.enginePhase || state.phase === 'in_progress' ? 1000 : 5000));
        } catch (error) {
            if (socketOpen) {
                status.textContent = 'Recuperacion HTTP no disponible; la sala en tiempo real sigue conectada.';
                timer = setTimeout(refresh, 15000);
                return;
            }
            stopped = true;
            status.textContent = 'Se ha detenido la sincronizacion. Recarga el Ring para recuperar el estado del servidor.';
            if (answer) answer.disabled = true;
            if (graphical) ring.querySelectorAll('[data-clash-item]').forEach(button => { button.disabled = true; });
        } finally {
            refreshing = false;
        }
    }
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refresh();
    });
    if (answerForm) answerForm.addEventListener('submit', async event => {
        event.preventDefault();
        if (sendingAnswer || stopped || answer.disabled) return;
        if (scene) scene.attack(ring.dataset.playerId);
        sendingAnswer = true;
        answer.disabled = true;
        try {
            const response = await fetch(answerForm.action, {
                method: 'POST', body: new FormData(answerForm),
                credentials: 'same-origin', headers: {'Accept': 'application/json'}
            });
            const result = await response.json();
            if (!response.ok || response.redirected || result.accepted !== true) {
                throw new Error(result.detail || 'No se pudo registrar la respuesta.');
            }
            feedback.textContent = 'Respuesta registrada. La ronda se resuelve cuando ambos responden o termina el plazo.';
        } catch (error) {
            feedback.textContent = `Respuesta pendiente de verificar: ${error.message} Revisa el estado del Ring antes de reenviar.`;
        } finally {
            sendingAnswer = false;
            refresh();
        }
    });
    if (graphical) {
        ring.querySelectorAll('[data-item-form]').forEach(form => {
            form.addEventListener('submit', event => {
                const button = form.querySelector('[data-clash-item]');
                if (pendingItem || stopped || button.disabled) { event.preventDefault(); return; }
                if (!socketOpen) return; // Existing CSRF POST is the fallback, without replay.
                event.preventDefault();
                const questionId = latestState && latestState.question_id;
                if (!questionId) return;
                pendingItem = form;
                ring.querySelectorAll('[data-clash-item]').forEach(item => { item.disabled = true; });
                socket.send(JSON.stringify({
                    type: 'item', question_id: questionId, code: button.dataset.code,
                    action_id: form.querySelector('[name="action_id"]').value
                }));
                itemFeedback.textContent = 'Solicitud enviada. Esperando confirmación; no se ha aplicado ningún efecto local.';
                pendingItemTimer = setTimeout(() => {
                    if (pendingItem) itemFeedback.textContent = 'Confirmación pendiente. Recarga para conciliar con Django; no se reenviará la solicitud.';
                }, 10000);
            });
        });
        if (ready) ready.closest('form').addEventListener('submit', event => {
            if (stopped || ready.disabled) { event.preventDefault(); return; }
            if (!socketOpen) return;
            event.preventDefault();
            ready.disabled = true;
            socket.send(JSON.stringify({type: 'ready'}));
        });
    }
    function connectSocket() {
        if (leaving || stopped || typeof WebSocket === 'undefined' || !ring.dataset.socketPath) return;
        socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${ring.dataset.socketPath}`);
        socket.addEventListener('open', () => {
            socketOpen = true;
            stopped = false;
            socket.send(JSON.stringify({type: 'sync', after: eventCursor}));
        });
        socket.addEventListener('message', event => {
            try {
                const message = JSON.parse(event.data);
                if (graphical && message.type === 'ack' && message.action === 'item' && pendingItem) {
                    clearTimeout(pendingItemTimer);
                    const bytes = crypto.getRandomValues(new Uint8Array(16));
                    bytes[6] = (bytes[6] & 15) | 64;
                    bytes[8] = (bytes[8] & 63) | 128;
                    pendingItem.querySelector('[name="action_id"]').value = Array.from(
                        bytes, (byte, index) => ([4, 6, 8, 10].includes(index) ? '-' : '') + byte.toString(16).padStart(2, '0')
                    ).join('');
                    pendingItem = null;
                    itemFeedback.textContent = 'Objeto confirmado por Django. Recuperando stock y efectos.';
                }
                if (message.state) {
                    stopped = false;
                    if (!renderState(message.state)) return;
                    if (latestState.unlocked && !stopped && heartbeatDue(latestState)) {
                        socket.send(JSON.stringify({type: 'presence'}));
                        lastHeartbeat = Date.parse(latestState.server_time);
                    }
                } else if (message.type === 'error') {
                    status.textContent = `Tiempo real: ${message.code}. Recupera el estado antes de reenviar una accion.`;
                    if (graphical && pendingItem) {
                        itemFeedback.textContent = 'Acción no confirmada. Revisa el estado; no se reenviará automáticamente. Recarga para recuperar los controles.';
                    }
                }
            } catch (error) {
                status.textContent = 'Mensaje de tiempo real no valido; se recuperara el estado por HTTP.';
                socket.close();
            }
        });
        socket.addEventListener('close', event => {
            socketOpen = false;
            if (graphical && pendingItem) itemFeedback.textContent = 'Conexión perdida: resultado de la solicitud desconocido. Recarga para conciliar; no se reenvía.';
            if (leaving || stopped) return;
            if ([4401, 4403, 4429].includes(event.code)) {
                stopped = true;
                clearTimeout(timer);
                clearTimeout(pendingItemTimer);
                if (answer) answer.disabled = true;
                if (graphical) ring.querySelectorAll('[data-clash-item]').forEach(button => { button.disabled = true; });
                status.textContent = 'Sala cerrada por permisos o limite de mensajes. Recarga para verificar tu acceso.';
                return;
            }
            status.textContent = 'Tiempo real desconectado. Recuperando el estado por HTTP.';
            refresh();
            if (reconnects < 3) {
                reconnects += 1;
                setTimeout(connectSocket, reconnects * 2000);
            }
        });
        socket.addEventListener('error', () => {
            status.textContent = 'Tiempo real no disponible; se mantiene la recuperacion por HTTP.';
        });
    }
    if (typeof window !== 'undefined') window.addEventListener('pagehide', () => {
        leaving = true;
        clearTimeout(timer);
        if (socket) socket.close();
        if (scene) scene.dispose();
    });
    if (graphical && scene) {
        const initial = document.getElementById('clash-initial-state');
        if (initial) scene.update(JSON.parse(initial.textContent));
    }
    connectSocket();
    refresh();
})();
