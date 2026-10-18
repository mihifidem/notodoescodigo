"""
Management command: python manage.py seed_nexus_modules
Seeds the 18 official Nexus 2026 modules with their weights and metadata.
"""
from django.core.management.base import BaseCommand
from apps.nexus.models import NexusModule


MODULES = [
    # Foundation Layer (22%)
    {
        'code': 'IDENTITY_MAP', 'name': 'IdentityMap',
        'layer': NexusModule.LAYER_FOUNDATION,
        'description': 'Maps professional motivations, identity and self-awareness dimensions.',
        'weight': '0.0800', 'color': '#2563EB', 'order': 1,
    },
    {
        'code': 'CORE_VALUES', 'name': 'CoreValues',
        'layer': NexusModule.LAYER_FOUNDATION,
        'description': 'Evaluates organizational and personal values alignment.',
        'weight': '0.0600', 'color': '#7C3AED', 'order': 2,
    },
    {
        'code': 'COGNITYPE', 'name': 'Cognitype',
        'layer': NexusModule.LAYER_FOUNDATION,
        'description': 'Assesses cognitive styles, thinking patterns and decision frameworks.',
        'weight': '0.0800', 'color': '#6366F1', 'order': 3,
    },
    # Talent Intelligence Layer (25%)
    {
        'code': 'DRIVE_SYNC', 'name': 'DriveSync',
        'layer': NexusModule.LAYER_TALENT,
        'description': 'Measures intrinsic and extrinsic motivational drivers.',
        'weight': '0.0500', 'color': '#F59E0B', 'order': 4,
    },
    {
        'code': 'CONFLICT_SYNC', 'name': 'ConflictSync',
        'layer': NexusModule.LAYER_TALENT,
        'description': 'Evaluates conflict resolution styles and negotiation capacity.',
        'weight': '0.0400', 'color': '#EF4444', 'order': 5,
    },
    {
        'code': 'CULTURE_SYNC', 'name': 'CultureSync',
        'layer': NexusModule.LAYER_TALENT,
        'description': 'Assesses cultural fit and organizational culture preferences.',
        'weight': '0.0400', 'color': '#EC4899', 'order': 6,
    },
    {
        'code': 'DECISION_CORE', 'name': 'DecisionCore',
        'layer': NexusModule.LAYER_TALENT,
        'description': 'Analyzes decision-making processes, risk tolerance and strategic thinking.',
        'weight': '0.0600', 'color': '#DC2626', 'order': 7,
    },
    {
        'code': 'TECH_MIND', 'name': 'TechMind',
        'layer': NexusModule.LAYER_TALENT,
        'description': 'Measures digital mindset, technology adoption and AI readiness.',
        'weight': '0.0600', 'color': '#06B6D4', 'order': 8,
    },
    # Human Intelligence Layer (53%)
    {
        'code': 'LEARN_SYNC', 'name': 'LearnSync',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Evaluates learning agility, knowledge retention and growth mindset.',
        'weight': '0.0500', 'color': '#14B8A6', 'order': 9,
    },
    {
        'code': 'EMOTION_SYNC', 'name': 'EmotionSync',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Assesses emotional intelligence, empathy and self-regulation.',
        'weight': '0.0500', 'color': '#F97316', 'order': 10,
    },
    {
        'code': 'BEHAVIOR_MATRIX', 'name': 'BehaviorMatrix',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Maps behavioral patterns, work styles and professional conduct.',
        'weight': '0.0700', 'color': '#8B5CF6', 'order': 11,
    },
    {
        'code': 'LEADERSHIP_SYNC', 'name': 'LeadershipSync',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Evaluates leadership style, influence capacity and team development.',
        'weight': '0.0700', 'color': '#DC2626', 'order': 12,
    },
    {
        'code': 'TEAM_SYNC', 'name': 'TeamSync',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Measures team collaboration, cohesion and collective intelligence.',
        'weight': '0.0500', 'color': '#2563EB', 'order': 13,
    },
    {
        'code': 'PERSONA_SPHERE', 'name': 'PersonaSphere',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Comprehensive personality assessment and behavioral prediction.',
        'weight': '0.0800', 'color': '#EC4899', 'order': 14,
    },
    {
        'code': 'RESILIENCE_X', 'name': 'ResilienceX',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Evaluates stress tolerance, recovery capacity and anti-fragility.',
        'weight': '0.0400', 'color': '#22C55E', 'order': 15,
    },
    {
        'code': 'ADAPTABILITY_IQ', 'name': 'AdaptabilityIQ',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Measures adaptability, flexibility and change management capacity.',
        'weight': '0.0500', 'color': '#14B8A6', 'order': 16,
    },
    {
        'code': 'COMMUNICATION_DNA', 'name': 'CommunicationDNA',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Assesses communication styles, persuasion and storytelling ability.',
        'weight': '0.0400', 'color': '#F59E0B', 'order': 17,
    },
    {
        'code': 'ETHICS_COMPASS', 'name': 'EthicsCompass',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Evaluates ethical reasoning, integrity and values-based decision making.',
        'weight': '0.0300', 'color': '#6366F1', 'order': 18,
    },
    {
        'code': 'INNOVATION_MIND', 'name': 'InnovationMind',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Measures creative thinking, innovation capacity and disruptive potential.',
        'weight': '0.0500', 'color': '#8B5CF6', 'order': 19,
    },
    {
        'code': 'FUTURE_FIT', 'name': 'FutureFit',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Evaluates future readiness, trend awareness and strategic adaptability.',
        'weight': '0.0500', 'color': '#22C55E', 'order': 20,
    },
    {
        'code': 'CAREER_NAVIGATOR', 'name': 'CareerNavigator',
        'layer': NexusModule.LAYER_HUMAN,
        'description': 'Assesses career goals, professional trajectory and growth potential.',
        'weight': '0.0500', 'color': '#22C55E', 'order': 21,
    },
]


class Command(BaseCommand):
    help = 'Seeds the 21 official Nexus 2026 assessment modules'

    def handle(self, *args, **options):
        created = updated = 0
        for data in MODULES:
            obj, was_created = NexusModule.objects.update_or_create(
                code=data['code'],
                defaults={k: v for k, v in data.items() if k != 'code'},
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f'  + Created: {obj.name}'))
            else:
                updated += 1
                self.stdout.write(f'  · Updated: {obj.name}')

        self.stdout.write(
            self.style.SUCCESS(
                f'\nDone! {created} modules created, {updated} updated. '
                f'Total weight: {sum(float(m["weight"]) for m in MODULES):.2f}',
            )
        )
