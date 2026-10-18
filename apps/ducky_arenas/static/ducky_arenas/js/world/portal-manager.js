export class PortalManager {
    constructor(scene, Phaser, portals, root, onLaunch) {
        this.scene = scene;
        this.Phaser = Phaser;
        this.portals = portals;
        this.root = root;
        this.onLaunch = onLaunch;
        this.nearbyPortal = null;
        this.launching = false;
        this.prompt = root.querySelector('[data-portal-prompt]');
        this.promptName = root.querySelector('[data-portal-name]');
        this.interactButton = root.querySelector('[data-interact-portal]');
        this.touchInteractButton = root.querySelector('[data-interact-touch]');
        this.markers = [];
        this.editor = root.querySelector('[data-portal-editor]');
        this.editorSelect = root.querySelector('[data-portal-editor-select]');
        this.editorStatus = root.querySelector('[data-portal-editor-status]');
        this.editorOutput = root.querySelector('[data-portal-export-data]');
        this.captureCorner = null;
        this.firstCorner = null;
        this.debugOutline = null;

        this.handleInteract = () => this.interact();
        this.handleTouchInteract = () => this.interact();
        this.interactButton.addEventListener('click', this.handleInteract);
        this.touchInteractButton.addEventListener('click', this.handleTouchInteract);
        if (this.editor) {
            this.editor.querySelector('[data-portal-capture]')
                .addEventListener('click', () => this.beginCapture());
            this.editor.querySelector('[data-portal-export]')
                .addEventListener('click', () => this.exportGeometry());
            this.handleDebugPointer = (pointer) => this.capturePoint(pointer);
            this.scene.input.on('pointerdown', this.handleDebugPointer);
        }
        this.portals
            .filter((portal) => portal.status === 'available')
            .forEach((portal) => this.addPortal(portal));
        this.renderPortalStatuses();
    }

    addPortal(portal) {
        if (![portal.x, portal.y, portal.width, portal.height].every(Number.isFinite)) {
            return;
        }
        const zone = new this.Phaser.Geom.Rectangle(
            portal.x,
            portal.y,
            portal.width,
            portal.height,
        );
        const marker = this.scene.add.rectangle(
            zone.centerX,
            zone.centerY,
            zone.width,
            zone.height,
            0xfacc15,
            0.16,
        );
        marker.setStrokeStyle(2, 0xfacc15, 0.85).setDepth(1);
        marker.setInteractive({ useHandCursor: true });
        marker.on('pointerdown', () => this.interact());
        const label = this.scene.add.text(
            zone.centerX,
            zone.top - 8,
            portal.name,
            {
                color: '#ffffff',
                backgroundColor: '#172238',
                fontSize: '14px',
                padding: { x: 6, y: 4 },
            },
        ).setOrigin(0.5, 1).setDepth(4);
        this.markers.push({ portal, zone, marker, label });
    }

    beginCapture() {
        this.captureCorner = 1;
        this.firstCorner = null;
        this.editorStatus.textContent = 'Haz clic en la primera esquina del área del portal.';
    }

    capturePoint(pointer) {
        if (!this.captureCorner) return;
        const point = {
            x: Math.round(pointer.worldX * 100) / 100,
            y: Math.round(pointer.worldY * 100) / 100,
        };
        if (this.captureCorner === 1) {
            this.firstCorner = point;
            this.captureCorner = 2;
            this.editorStatus.textContent = 'Haz clic en la esquina opuesta del área.';
            return;
        }

        const portalId = this.editorSelect.value;
        const portal = this.portals.find((item) => item.portal_id === portalId);
        if (!portal || !this.firstCorner) {
            this.captureCorner = null;
            this.editorStatus.textContent = 'No se encontró la configuración de ese portal.';
            return;
        }
        portal.x = Math.min(this.firstCorner.x, point.x);
        portal.y = Math.min(this.firstCorner.y, point.y);
        portal.width = Math.abs(point.x - this.firstCorner.x);
        portal.height = Math.abs(point.y - this.firstCorner.y);
        if (portal.width === 0 || portal.height === 0) {
            this.captureCorner = null;
            this.editorStatus.textContent = 'El área debe tener ancho y alto mayores que cero.';
            return;
        }
        this.debugOutline?.destroy();
        this.debugOutline = this.scene.add.rectangle(
            portal.x + portal.width / 2,
            portal.y + portal.height / 2,
            portal.width,
            portal.height,
        ).setStrokeStyle(3, 0xff3b30).setDepth(5);
        this.captureCorner = null;
        this.editorStatus.textContent =
            `Geometría capturada para ${portal.name}; expórtala y guárdala desde Django Admin.`;
    }

    exportGeometry() {
        const geometry = this.portals
            .filter((portal) => Number.isFinite(portal.x)
                && Number.isFinite(portal.y)
                && Number.isFinite(portal.width)
                && Number.isFinite(portal.height))
            .map((portal) => ({
                portal_id: portal.portal_id,
                x: portal.x,
                y: portal.y,
                area_width: portal.width,
                area_height: portal.height,
            }));
        this.editorOutput.value = JSON.stringify(geometry, null, 2);
    }

    renderPortalStatuses() {
        const list = this.root.querySelector('[data-portal-statuses]');
        if (!list) return;
        list.replaceChildren();
        if (!this.portals.length) {
            const message = document.createElement('p');
            message.textContent = 'No hay portales configurados en este mapa.';
            list.append(message);
            return;
        }

        this.portals.forEach(({ name, status }) => {
            const item = document.createElement('p');
            item.textContent = `${name} · ${
                status === 'available'
                    ? 'disponible'
                    : status === 'locked'
                        ? 'bloqueado por requisitos'
                        : 'pendiente de configuración'
            }`;
            list.append(item);
        });
    }

    update(player) {
        const nextPortal = this.markers
            .filter(({ zone }) => zone.contains(player.x, player.y))
            .sort((first, second) => {
                const firstDistance = this.Phaser.Math.Distance.Between(
                    player.x,
                    player.y,
                    first.zone.centerX,
                    first.zone.centerY,
                );
                const secondDistance = this.Phaser.Math.Distance.Between(
                    player.x,
                    player.y,
                    second.zone.centerX,
                    second.zone.centerY,
                );
                return firstDistance - secondDistance
                    || first.portal.display_order - second.portal.display_order;
            })[0];
        this.nearbyPortal = nextPortal ? nextPortal.portal : null;
        this.interactButton.disabled = !this.nearbyPortal || this.launching;
        this.touchInteractButton.disabled = !this.nearbyPortal || this.launching;
        this.prompt.hidden = !this.nearbyPortal;
        if (this.nearbyPortal) {
            this.promptName.textContent = this.nearbyPortal.name;
        }
    }

    interact() {
        if (!this.nearbyPortal || this.launching) return;
        this.onLaunch(this.nearbyPortal);
    }

    setLaunching(launching) {
        this.launching = launching;
        this.interactButton.disabled = !this.nearbyPortal || launching;
        this.touchInteractButton.disabled = !this.nearbyPortal || launching;
    }

    destroy() {
        this.interactButton.removeEventListener('click', this.handleInteract);
        this.touchInteractButton.removeEventListener('click', this.handleTouchInteract);
        if (this.handleDebugPointer) {
            this.scene.input.off('pointerdown', this.handleDebugPointer);
        }
        this.debugOutline?.destroy();
        this.markers.forEach(({ marker, label }) => {
            marker.destroy();
            label.destroy();
        });
    }
}
