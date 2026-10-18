export function createEvolutionScene(Phaser) {
    return class EvolutionScene extends Phaser.Scene {
        constructor() {
            super({ key: 'DuckyEvolution' });
        }

        create(data) {
            if (!data?.name) {
                this.scene.stop();
                return;
            }

            const { width, height } = this.scale;
            const backdrop = this.add.rectangle(
                width / 2,
                height / 2,
                width,
                height,
                0x14223a,
                0.78,
            ).setAlpha(0);
            const emblem = this.add.circle(
                width / 2,
                height / 2 - 24,
                38,
                0xfacc15,
            ).setAlpha(0).setScale(0.35);
            const title = this.add.text(
                width / 2,
                height / 2 + 48,
                `Nueva era: ${data.name}`,
                {
                    color: '#ffffff',
                    fontFamily: 'sans-serif',
                    fontSize: '34px',
                    fontStyle: 'bold',
                    align: 'center',
                },
            ).setOrigin(0.5).setAlpha(0);

            this.tweens.add({
                targets: [backdrop, emblem, title],
                alpha: 1,
                duration: 350,
                ease: 'Sine.easeOut',
            });
            this.tweens.add({
                targets: emblem,
                scale: 1.25,
                duration: 550,
                yoyo: true,
                repeat: 1,
                ease: 'Back.easeOut',
            });
            this.time.delayedCall(1700, () => this.scene.stop());
        }
    };
}
