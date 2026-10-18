import { ArenaApiClient } from './world/arena-api-client.js';
import { CollisionManager } from './world/collision-manager.js';
import { createEvolutionScene } from './world/evolution-scene.js';
import { PlayerController } from './world/player-controller.js';
import { PortalManager } from './world/portal-manager.js';

const root = document.querySelector('[data-ducky-world]');
const configElement = document.getElementById('ducky-world-config');
const evolutionNoticesElement = document.getElementById('ducky-evolution-notices');

if (root && configElement) {
    const config = JSON.parse(configElement.textContent);
    const evolutionNotices = evolutionNoticesElement
        ? JSON.parse(evolutionNoticesElement.textContent)
        : [];
    const evolutionDialog = root.querySelector('[data-evolution-dialog]');
    const evolutionTitle = root.querySelector('[data-evolution-title]');
    const evolutionPortrait = root.querySelector('[data-evolution-portrait]');
    const evolutionError = root.querySelector('[data-evolution-error]');
    const evolutionAckButton = root.querySelector('[data-evolution-ack]');
    const loading = root.querySelector('[data-world-loading]');
    const error = root.querySelector('[data-world-error]');
    const portalError = root.querySelector('[data-portal-error]');
    const portalButton = root.querySelector('[data-interact-portal]');
    const fullscreenButton = root.querySelector('[data-fullscreen]');
    const api = new ArenaApiClient(root, root.querySelector('[data-csrf-token]').value);
    let game;
    let player;
    let controller;
    let portalManager;
    let collisionManager;
    let checkpointTimer = 0;
    let checkpointPending = false;
    let checkpointDirty = false;
    let checkpointInFlight = null;
    let portalLaunchPending = false;
    let stopped = false;

    function showWorldError(message) {
        error.textContent = message;
        error.hidden = false;
        loading.hidden = true;
    }

    function positionPayload() {
        return {
            era: config.era,
            x: player ? player.x : config.player.x,
            y: player ? player.y : config.player.y,
            direction: controller ? controller.getDirection() : config.player.direction,
            version: config.player.version,
        };
    }

    function saveCheckpoint(keepalive = false) {
        if (checkpointInFlight) return checkpointInFlight;
        if (!checkpointDirty || (stopped && !keepalive)) return Promise.resolve();
        checkpointPending = true;
        checkpointDirty = false;
        checkpointInFlight = api.saveState(positionPayload(), keepalive)
            .then((result) => {
                config.player.version = result.exploration.version;
            })
            .catch((requestError) => {
                checkpointDirty = true;
                showWorldError(`No se pudo guardar la posición: ${requestError.message}`);
            })
            .finally(() => {
                checkpointPending = false;
                checkpointInFlight = null;
            });
        return checkpointInFlight;
    }

    function reportSceneError(message) {
        showWorldError(message);
        if (player?.body) player.body.setVelocity(0, 0);
    }

    function showNextEvolutionNotice() {
        const notice = evolutionNotices[0];
        if (!notice || !evolutionDialog) return;
        evolutionTitle.textContent = notice.name;
        evolutionPortrait.hidden = !notice.portrait_url;
        if (notice.portrait_url) {
            evolutionPortrait.src = notice.portrait_url;
        } else {
            evolutionPortrait.removeAttribute('src');
        }
        evolutionPortrait.alt = notice.portrait_url
            ? `Personaje de la época ${notice.name}`
            : '';
        evolutionError.hidden = true;
        if (!evolutionDialog.open) evolutionDialog.showModal();
        if (game?.scene.isActive('PrehistoricWorld')) {
            game.scene.pause('PrehistoricWorld');
        }
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches && game) {
            game.scene.start('DuckyEvolution', { name: notice.name });
        }
    }

    evolutionAckButton?.addEventListener('click', async () => {
        const notice = evolutionNotices[0];
        if (!notice) return;
        evolutionAckButton.disabled = true;
        try {
            await api.acknowledgeEvolution(root.dataset.evolutionAckUrl, notice.code);
            evolutionNotices.shift();
            evolutionDialog.close();
            showNextEvolutionNotice();
        } catch (requestError) {
            evolutionError.textContent = `No se pudo confirmar el aviso: ${requestError.message}`;
            evolutionError.hidden = false;
        } finally {
            evolutionAckButton.disabled = false;
        }
    });
    evolutionDialog?.addEventListener('close', () => {
        if (game?.scene.isActive('PrehistoricWorld')) {
            game.scene.resume('PrehistoricWorld');
        }
    });

    function loadAsset(sceneRef, key, url) {
        sceneRef.load.image(key, url);
    }

    function startGame(Phaser) {
        class WorldScene extends Phaser.Scene {
            constructor() {
                super('PrehistoricWorld');
            }

            preload() {
                this.load.on('loaderror', (file) => {
                    reportSceneError(`No se pudo cargar un recurso del mapa: ${file.key}`);
                });
                loadAsset(this, 'world-map', config.map.url);
                const character = config.player.character;
                if (character?.kind === 'directional-sprites') {
                    Object.entries(character.directions).forEach(([direction, url]) => {
                        loadAsset(this, `player-${direction}`, url);
                    });
                }
            }

            create() {
                if (!this.textures.exists('world-map')) {
                    reportSceneError(
                        'El mapa no está disponible. Comprueba que el recurso estático exista.',
                    );
                    return;
                }

                this.add.image(0, 0, 'world-map')
                    .setOrigin(0)
                    .setDisplaySize(config.map.width, config.map.height);
                this.physics.world.setBounds(0, 0, config.map.width, config.map.height);
                this.cameras.main.setBounds(0, 0, config.map.width, config.map.height);

                const initialDirection = config.player.direction || 'down';
                const initialTexture = `player-${initialDirection}`;
                if (config.player.character && this.textures.exists(initialTexture)) {
                    player = this.physics.add.sprite(
                        config.player.x,
                        config.player.y,
                        initialTexture,
                    );
                    player.setDisplaySize(54, 98);
                    player.body.setSize(30, 28).setOffset(17, 66);
                } else {
                    player = this.add.circle(
                        config.player.x,
                        config.player.y,
                        14,
                        0xfacc15,
                    );
                    this.physics.add.existing(player);
                    player.body.setCircle(14);
                    reportSceneError(
                        config.sprite_notice || 'No hay un personaje disponible para esta época.',
                    );
                }

                player.body.setCollideWorldBounds(true);
                player.setDepth?.(2);
                collisionManager = new CollisionManager(
                    this,
                    Phaser,
                    config.collision_zones || [],
                    config.debug_allowed,
                );
                collisionManager.create(player);

                portalManager = new PortalManager(
                    this,
                    Phaser,
                    config.portals || [],
                    root,
                    (portal) => launchPortal(portal),
                );
                controller = new PlayerController(
                    this,
                    player,
                    config,
                    root,
                    () => portalManager.interact(),
                );
                this.cameras.main.startFollow(player, true, 0.08, 0.08);
                this.events.on('update', (time, delta) => this.updateWorld(delta));
                this.events.once('shutdown', () => {
                    controller?.destroy();
                    portalManager?.destroy();
                });
                loading.hidden = true;
                root.querySelector('[data-sprite-notice]').textContent = config.sprite_notice || '';
            }

            updateWorld(delta) {
                if (!player || stopped) return;
                const moved = controller.update(collisionManager, delta);
                portalManager.update(player);
                if (moved) checkpointDirty = true;
                if (!checkpointDirty) return;
                checkpointTimer += delta;
                if (checkpointTimer >= config.checkpoint_interval_ms) {
                    checkpointTimer = 0;
                    saveCheckpoint();
                }
            }
        }

        game = new Phaser.Game({
            type: Phaser.AUTO,
            parent: 'ducky-world-game',
            width: config.map.width,
            height: config.map.height,
            backgroundColor: '#172238',
            physics: { default: 'arcade', arcade: { debug: false } },
            scale: {
                mode: Phaser.Scale.FIT,
                autoCenter: Phaser.Scale.CENTER_BOTH,
                width: config.map.width,
                height: config.map.height,
            },
            scene: [WorldScene, createEvolutionScene(Phaser)],
        });
        game.events.once('destroy', () => {
            stopped = true;
        });
    }

    async function launchPortal(portal) {
        if (!portal || !portal.launch_url || portalLaunchPending) return;
        portalLaunchPending = true;
        portalButton.disabled = true;
        portalManager?.setLaunching(true);
        portalError.hidden = true;
        try {
            if (checkpointInFlight) await checkpointInFlight;
            await api.launchPortal(portal, positionPayload());
            checkpointDirty = false;
        } catch (requestError) {
            portalError.textContent = `No se pudo abrir ${portal.name}: ${requestError.message}`;
            portalError.hidden = false;
        } finally {
            portalLaunchPending = false;
            portalManager?.setLaunching(false);
        }
    }

    let mapTransitionPending = false;
    root.querySelectorAll('[data-map-transition]').forEach((link) => {
        link.addEventListener('click', async (event) => {
            event.preventDefault();
            if (link.hasAttribute('aria-current') || mapTransitionPending) return;
            mapTransitionPending = true;
            if (checkpointInFlight) await checkpointInFlight;
            try {
                await api.transitionMap(link.dataset.transitionUrl, positionPayload());
            } catch (requestError) {
                showWorldError(`No se pudo cambiar de mapa: ${requestError.message}`);
                mapTransitionPending = false;
            }
        });
    });

    root.querySelector('[data-exit-world]').addEventListener('click', () => {
        stopped = true;
        if (checkpointDirty && !checkpointPending) {
            saveCheckpoint(true);
        }
        game?.destroy(true);
    });

    fullscreenButton.addEventListener('click', () => {
        if (!game) return;
        if (game.scale.isFullscreen) game.scale.stopFullscreen();
        else game.scale.startFullscreen();
    });

    window.addEventListener('pagehide', () => {
        if (checkpointDirty && !checkpointPending) saveCheckpoint(true);
        stopped = true;
    });

    if (!window.Phaser) {
        showWorldError('Phaser no está disponible. Comprueba la conexión y vuelve a cargar la página.');
    } else {
        try {
            startGame(window.Phaser);
        } catch (initializationError) {
            showWorldError(`No se pudo iniciar el mapa: ${initializationError.message}`);
        }
    }

    if (evolutionNotices.length > 0) {
        showNextEvolutionNotice();
    }
}
