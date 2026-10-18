export class CollisionManager {
    constructor(scene, Phaser, config, debugEnabled) {
        this.scene = scene;
        this.Phaser = Phaser;
        this.config = config;
        this.debugEnabled = debugEnabled;
        this.blockedRects = [];
        this.blockedPolygons = [];
        this.walkableZones = [];
        this.debugGraphics = null;
    }

    create(player) {
        this.config.forEach((zone) => {
            if (!zone || typeof zone !== 'object') return;
            const mode = zone.mode || 'obstacle';
            const shape = zone.type || 'rect';
            if (shape === 'rect' && this.isValidRect(zone)) {
                const rect = new this.Phaser.Geom.Rectangle(
                    zone.x,
                    zone.y,
                    zone.width,
                    zone.height,
                );
                this.recordZone(mode, rect);
                if (mode === 'obstacle') {
                    const obstacle = this.scene.add.rectangle(
                        rect.centerX,
                        rect.centerY,
                        rect.width,
                        rect.height,
                        0xffffff,
                        0,
                    );
                    this.scene.physics.add.existing(obstacle, true);
                    this.scene.physics.add.collider(player, obstacle);
                }
                return;
            }

            if (shape === 'polygon' && this.isValidPolygon(zone)) {
                const polygon = new this.Phaser.Geom.Polygon(
                    zone.points.flatMap(([x, y]) => [x, y]),
                );
                this.recordZone(mode, polygon);
            }
        });

        if (this.debugEnabled) this.drawDebug();
    }

    isValidRect(zone) {
        return ['x', 'y', 'width', 'height'].every(
            (key) => Number.isFinite(zone[key]),
        ) && zone.width > 0 && zone.height > 0;
    }

    isValidPolygon(zone) {
        return Array.isArray(zone.points)
            && zone.points.length >= 3
            && zone.points.every((point) =>
                Array.isArray(point)
                && point.length === 2
                && point.every(Number.isFinite)
            );
    }

    recordZone(mode, shape) {
        if (mode === 'walkable') {
            this.walkableZones.push(shape);
        } else if (mode === 'obstacle' || mode === 'building-entry') {
            if (shape instanceof this.Phaser.Geom.Rectangle) {
                this.blockedRects.push(shape);
            } else {
                this.blockedPolygons.push(shape);
            }
        }
    }

    canOccupy(x, y) {
        const insideWalkable = this.walkableZones.length === 0
            || this.walkableZones.some((zone) => (
                zone instanceof this.Phaser.Geom.Rectangle
                    ? zone.contains(x, y)
                    : this.Phaser.Geom.Polygon.Contains(zone, x, y)
            ));
        if (!insideWalkable) return false;

        const insideRect = this.blockedRects.some((rect) => rect.contains(x, y));
        const insidePolygon = this.blockedPolygons.some((polygon) =>
            this.Phaser.Geom.Polygon.Contains(polygon, x, y)
        );
        return !insideRect && !insidePolygon;
    }

    drawDebug() {
        this.debugGraphics = this.scene.add.graphics().setDepth(10);
        this.config.forEach((zone) => {
            const color = zone.mode === 'walkable' ? 0x22c55e : 0xef4444;
            this.debugGraphics.lineStyle(2, color, 0.9);
            if (zone.type === 'polygon' && this.isValidPolygon(zone)) {
                this.debugGraphics.strokePoints(
                    zone.points.map(([x, y]) => new this.Phaser.Geom.Point(x, y)),
                    true,
                );
            } else if (this.isValidRect(zone)) {
                this.debugGraphics.strokeRect(zone.x, zone.y, zone.width, zone.height);
            }
        });
    }
}
