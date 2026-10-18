export class PlayerController {
    constructor(scene, player, config, root, onInteract) {
        this.scene = scene;
        this.player = player;
        this.config = config;
        this.root = root;
        this.onInteract = onInteract;
        this.directions = { up: false, down: false, left: false, right: false };
        this.facing = config.player.direction || 'down';
        this.listeners = [];
        this.releaseAll = () => {
            Object.keys(this.directions).forEach((direction) => {
                this.directions[direction] = false;
            });
        };
        this.cursors = scene.input.keyboard.createCursorKeys();
        this.keys = scene.input.keyboard.addKeys('W,A,S,D,E');

        if (scene.textures.exists(`player-${this.facing}`)) {
            player.setTexture(`player-${this.facing}`);
        }
        this.bindTouchControls();
        this.keys.E.on('down', () => this.onInteract());
        window.addEventListener('blur', this.releaseAll);
    }

    bindTouchControls() {
        this.root.querySelectorAll('[data-move]').forEach((button) => {
            const direction = button.dataset.move;
            const release = (event) => {
                event.preventDefault();
                this.directions[direction] = false;
            };
            const press = (event) => {
                event.preventDefault();
                this.directions[direction] = true;
                button.setPointerCapture?.(event.pointerId);
            };
            const preventContextMenu = (event) => event.preventDefault();
            button.addEventListener('pointerdown', press);
            button.addEventListener('pointerup', release);
            button.addEventListener('pointercancel', release);
            button.addEventListener('lostpointercapture', release);
            button.addEventListener('contextmenu', preventContextMenu);
            this.listeners.push(
                [button, 'pointerdown', press],
                [button, 'pointerup', release],
                [button, 'pointercancel', release],
                [button, 'lostpointercapture', release],
                [button, 'contextmenu', preventContextMenu],
            );
        });
        this.root.addEventListener('pointerup', this.releaseAll);
        this.listeners.push([this.root, 'pointerup', this.releaseAll]);
    }

    update(collisionManager, delta) {
        const horizontal =
            Number(this.cursors.right.isDown || this.keys.D.isDown || this.directions.right) -
            Number(this.cursors.left.isDown || this.keys.A.isDown || this.directions.left);
        const vertical =
            Number(this.cursors.down.isDown || this.keys.S.isDown || this.directions.down) -
            Number(this.cursors.up.isDown || this.keys.W.isDown || this.directions.up);

        if (!horizontal && !vertical) {
            this.player.body.setVelocity(0, 0);
            return false;
        }

        const direction = Math.abs(horizontal) > Math.abs(vertical)
            ? (horizontal < 0 ? 'left' : 'right')
            : (vertical < 0 ? 'up' : 'down');
        this.setFacing(direction);

        const length = Math.hypot(horizontal, vertical);
        const speed = this.config.player.speed || 220;
        const velocityX = horizontal / length * speed;
        const velocityY = vertical / length * speed;
        const nextX = this.player.x + velocityX * delta / 1000;
        const nextY = this.player.y + velocityY * delta / 1000;
        if (!collisionManager.canOccupy(nextX, nextY)) {
            this.player.body.setVelocity(0, 0);
            return true;
        }

        this.player.body.setVelocity(velocityX, velocityY);
        return true;
    }

    setFacing(direction) {
        if (this.facing === direction) return;
        this.facing = direction;
        if (this.scene.textures.exists(`player-${direction}`)) {
            this.player.setTexture(`player-${direction}`);
        }
    }

    getDirection() {
        return this.facing;
    }

    destroy() {
        this.listeners.forEach(([target, event, handler]) => {
            target.removeEventListener(event, handler);
        });
        window.removeEventListener('blur', this.releaseAll);
        this.releaseAll();
    }
}
