"""Deterministic server implementation of the original assessment rules."""

import math
from collections import Counter, defaultdict
from statistics import mean, pstdev

from django.core.exceptions import ValidationError


METHOD = 'nexus_original_deterministic_v1'
LIKERT = (10, 28, 52, 75, 100)
ABCD = (100, 75, 45, 20)
PREFIXES = {
    'behaviormatrix': ('BM', 8), 'careernavigator': ('CN', 10),
    'conglicttsync': ('CS', 8), 'decissioncore': ('DC', 10),
    'identitymap': ('IM', 9), 'personasphere': ('PS', 8), 'techmind': ('TM', 10),
}


def js_round(value):
    return math.floor(value + 0.5)


def item_score(question, answer, family):
    reverse = question.get('is_reverse', False)
    if question['type'] == 'likert':
        if answer not in range(1, 6):
            raise ValidationError('Respuesta fuera de la escala original.')
        if reverse and family in ('identitymap', 'decissioncore', 'personasphere'):
            return LIKERT[5 - answer]
        value = LIKERT[answer - 1]
    else:
        if answer not in range(len(question['options'])) or answer >= len(ABCD):
            raise ValidationError('Opción fuera del banco original.')
        value = ABCD[answer]
    return 100 - value if reverse else value


def vector(family, questions, answers):
    prefix, count = PREFIXES[family]
    buckets = {f'{prefix}{i:02}': [] for i in range(1, count + 1)}
    hits, opportunities = Counter(), Counter()
    for question, answer in zip(questions, answers):
        dimension = question['dimKey']
        if dimension not in buckets:
            continue
        if family == 'identitymap' and question['type'] != 'likert':
            dimensions = question['opt_dims']
            opportunities.update(dimensions)
            hits[dimensions[answer]] += 1
        else:
            buckets[dimension].append(item_score(question, answer, family))
    result = {}
    for dimension, values in buckets.items():
        scores = [mean(values)] if values else []
        if opportunities[dimension]:
            scores.append(100 * hits[dimension] / opportunities[dimension])
        if scores:
            result[dimension] = max(12, min(100, js_round(mean(scores))))
    return result


def derived_scores(family, values):
    def weighted(weights, data=values, rounded=True):
        if any(key not in data for key in weights):
            return None
        result = sum(data[key] * weight for key, weight in weights.items()) / sum(weights.values())
        return js_round(result) if rounded else result

    def pairs(prefix, specifications):
        return {name: weighted({f'{prefix}{index:02}': weight for index, weight in weights.items()})
                for name, weights in specifications.items()}

    competencies, archetypes = {}, {}
    if family == 'identitymap':
        competencies = pairs('IM', {
            'autoexigencia': {1: 1, 3: 1}, 'conexion': {2: 1, 9: 1},
            'autonomia': {8: 1, 4: 1}, 'profundidad': {5: 1, 4: 1},
            'apertura': {7: 1, 5: 1}, 'seguridad': {6: 1, 1: 1},
        })
        archetypes = dict(values)
        ranked = sorted(values.values(), reverse=True)
        overall = js_round(ranked[0] * .45 + ranked[1] * .25 + mean(ranked) * .30) if len(ranked) == 9 else None
    elif family == 'decissioncore':
        competencies = pairs('DC', {
            'strategic_thinking': {6: 1, 5: 1}, 'risk_management': {5: 1, 1: 1},
            'decision_agility': {4: 1, 9: 1}, 'innovation_decision': {8: 1, 2: 1},
            'leadership_decision': {7: 1, 10: 1}, 'decision_confidence': {10: 1, 4: 1},
        })
        archetypes = {name: weighted({f'DC{i:02}': 1 for i in dimensions}, rounded=False)
                      for name, dimensions in {
                          'Analytical Decision Maker': (1, 3), 'Strategic Decision Maker': (6, 5),
                          'Intuitive Decision Maker': (2, 10), 'Collaborative Decision Maker': (7, 9),
                          'Action-Oriented Decision Maker': (4, 10), 'Risk-Aware Decision Maker': (5, 1),
                          'Innovative Decision Maker': (8, 9), 'Balanced Decision Maker': tuple(range(1, 11)),
                      }.items()}
        overall = weighted({f'DC{i:02}': 1 for i in (1, 2, 5, 6, 9, 10)})
    elif family == 'personasphere':
        competencies = pairs('PS', {
            'leadership': {7: 1, 2: 1, 6: 1}, 'communication': {1: 1, 2: 1},
            'innovation': {8: 1, 4: 1}, 'execution': {3: 1, 7: 1},
            'collaboration': {6: 1, 1: 1}, 'resilience': {5: 1, 4: 1},
            'strategic_thinking': {3: 1, 8: 1},
        })
        archetypes = {name: weighted({f'PS{i:02}': 1 for i in dimensions}, rounded=False)
                      for name, dimensions in {
                          'Creative Innovator': (8, 4), 'Strategic Analyst': (3, 2),
                          'Collaborative Builder': (6, 1), 'Visionary Leader': (2, 7, 8),
                      }.items()}
        overall = weighted({f'PS{i:02}': 1 for i in (1, 2, 7, 8)})
    elif family == 'behaviormatrix':
        names = ('execution_drive', 'accountability', 'adaptability', 'collaboration',
                 'initiative', 'decision_behavior', 'consistency', 'pressure_response')
        competencies = {name: values.get(f'BM{i:02}') for i, name in enumerate(names, 1)}
        for name, weights in {
            'Strategic Executor': {'execution_drive': .40, 'decision_behavior': .35, 'consistency': .25},
            'Resilient Achiever': {'pressure_response': .45, 'accountability': .30, 'consistency': .25},
            'Accountable Driver': {'accountability': .45, 'execution_drive': .30, 'decision_behavior': .25},
            'Adaptive Catalyst': {'adaptability': .45, 'initiative': .30, 'decision_behavior': .25},
            'Collaborative Builder': {'collaboration': .45, 'accountability': .30, 'initiative': .25},
            'Initiative Pioneer': {'initiative': .45, 'execution_drive': .30, 'adaptability': .25},
            'Decisive Operator': {'decision_behavior': .45, 'execution_drive': .30, 'accountability': .25},
            'Consistent Guardian': {'consistency': .45, 'accountability': .30, 'pressure_response': .25},
        }.items():
            data = {key: value for key, value in competencies.items() if value is not None}
            archetypes[name] = weighted(weights, data, rounded=False)
        overall = weighted(dict(zip((f'BM{i:02}' for i in range(1, 9)), (.15, .15, .12, .12, .12, .14, .10, .10))))
    else:
        specifications = {
            'careernavigator': ('CN', {
                'growth_mindset': {1: .7, 6: .3}, 'leadership': {2: .6, 9: .4},
                'networking': {3: .6, 10: .4}, 'resilience': {4: .6, 1: .4},
                'strategy': {5: .6, 8: .4}, 'learning': {6: .5, 1: .3, 8: .2},
                'execution': {7: .6, 4: .4}, 'innovation': {8: .6, 5: .4},
            }, (1, 2, 5, 8), {
                'Growth Leader': ('CN01', 'CN02', 'growth_mindset'),
                'Career Architect': ('CN05', 'strategy', 'CN09'),
                'Resilient Pioneer': ('CN04', 'CN08', 'resilience'),
                'Network Maven': ('CN03', 'CN10', 'networking'),
                'Impact Executor': ('CN07', 'execution', 'CN04'),
                'Learning Machine': ('CN06', 'learning', 'CN01'),
                'Visible Expert': ('CN10', 'CN09', 'networking'),
                'Agile Innovator': ('CN08', 'innovation', 'CN05'),
            }),
            'conglicttsync': ('CS', {
                'awareness': {1: .7, 6: .3}, 'regulation': {2: .7, 8: .3},
                'communication': {3: .6, 4: .4}, 'negotiation': {4: .6, 5: .4},
                'resolution': {5: .5, 1: .3, 7: .2}, 'empathy': {6: .6, 7: .4},
                'fairness': {7: .6, 3: .4}, 'recovery': {8: .6, 2: .4},
            }, (1, 2, 5, 6), {
                'Conflict Architect': ('CS05', 'CS01', 'resolution'),
                'Empathic Navigator': ('CS06', 'empathy', 'CS02'),
                'Assertive Challenger': ('CS03', 'CS01', 'communication'),
                'Diplomatic Broker': ('CS04', 'negotiation', 'CS07'),
                'Resilient Processor': ('CS08', 'recovery', 'CS02'),
                'Systemic Mediator': ('CS07', 'fairness', 'CS05'),
                'Regulated Responder': ('CS02', 'regulation', 'CS06'),
            }),
            'techmind': ('TM', {
                'ai_readiness': {4: .6, 3: .4}, 'automation': {5: .6, 6: .4},
                'digital_adapt': {2: .5, 8: .5}, 'innovation': {7: .6, 1: .4},
                'digital_lead': {9: .3, 7: .3, 10: .4},
                'future_readiness': {8: .4, 2: .3, 7: .3},
                'learning_cap': {3: .5, 1: .3, 4: .2},
                'transform_ready': {9: .4, 10: .3, 1: .3},
            }, (1, 2, 4, 8), {
                'AI Pioneer': ('TM04', 'TM07', 'ai_readiness'),
                'Innovation Driver': ('TM07', 'TM01', 'innovation'),
                'Future Navigator': ('TM08', 'TM02', 'digital_adapt'),
                'Digital Explorer': ('TM01', 'TM03', 'TM02'),
                'Tech Leader': ('TM09', 'digital_lead', 'TM07'),
                'Automation Builder': ('TM05', 'TM06', 'automation'),
                'Digital Collaborator': ('TM10', 'TM02', 'digital_adapt'),
                'Pragmatic Adopter': ('TM02', 'TM09', 'TM06'),
            }),
        }
        prefix, spec, overall_dimensions, archetype_spec = specifications[family]
        competencies = pairs(prefix, spec)
        data = {**values, **{key: value for key, value in competencies.items() if value is not None}}
        archetypes = {name: weighted({key: 1 for key in keys}, data, rounded=False)
                      for name, keys in archetype_spec.items()}
        if family == 'conglicttsync':
            avoided = weighted({'CS03': 1, 'CS05': 1, 'CS01': 1}, rounded=False)
            archetypes['Conflict Avoider'] = max(10, 80 - avoided) if avoided is not None else None
        overall = weighted({f'{prefix}{i:02}': 1 for i in overall_dimensions})
    return (
        {key: value for key, value in competencies.items() if value is not None},
        {key: value for key, value in archetypes.items() if value is not None},
        overall,
    )


def validation_scores(family, questions, answers, timings):
    frequency = Counter(answers)
    dominance = max(frequency.values()) / len(answers)
    if family in ('identitymap', 'decissioncore', 'personasphere'):
        lookup = {q['id']: (q, a) for q, a in zip(questions, answers)}
        consistency, attention, authenticity = [], [], []
        for question, answer in zip(questions, answers):
            reference = lookup.get(question.get('mirror_of'))
            if reference and question['type'] == 'likert':
                difference = abs(reference[1] - (6 - answer))
                consistency.append(100 - difference * (20 if family == 'identitymap' else 25))
            if question.get('is_attention_check'):
                attention.append(answer == question['expected'])
            if question.get('is_anti_fake'):
                authenticity.append(answer >= 4)
        components = {
            'consistency': js_round(mean(consistency)) if consistency else None,
            'attention': js_round(mean(attention) * 100) if attention else None,
            'authenticity': js_round(100 - mean(authenticity) * 50) if authenticity else None,
        }
        penalty = 20 if dominance >= .7 else 10 if dominance >= .6 else 0
        if family == 'personasphere':
            penalty = next((p for threshold, p in ((.8, 30), (.7, 20), (.6, 10), (.5, 5))
                            if dominance >= threshold), 0)
            deviation = pstdev(answers)
            if len(answers) >= 5:
                penalty += next((p for threshold, p in ((.5, 25), (.8, 15), (1.2, 8), (1.6, 3))
                                 if deviation < threshold), 0)
            if timings:
                seconds = mean(timings.values()) / 1000
                penalty += next((p for threshold, p in ((1, 30), (1.5, 20), (2, 10), (2.5, 5))
                                 if seconds < threshold), 0)
        # A missing control is not evidence of perfect consistency or attention.
        complete = all(value is not None for value in components.values())
        components['reliability'] = max(0, js_round(mean(components.values())) - penalty) if complete else None
        components['penalty'] = penalty
        return components
    run, longest = 1, 1
    for previous, current in zip(answers, answers[1:]):
        run = run + 1 if previous == current else 1
        longest = max(longest, run)
    pattern = max(0, js_round(100 - max(0, (longest - 4) * 12) - max(0, (dominance - .5) * 80)))
    groups = defaultdict(list)
    for question, answer in zip(questions, answers):
        raw = (answer - 1) * 25 if question['type'] == 'likert' else ABCD[answer]
        groups[question['dimKey']].append((raw, question.get('is_reverse', False)))
    coherence = []
    for items in groups.values():
        normal = [raw for raw, reverse in items if not reverse]
        if normal:
            coherence.extend(max(0, js_round(100 - abs(raw - (100 - mean(normal))) * .9))
                             for raw, reverse in items if reverse)
    mirror = js_round(mean(coherence)) if coherence else None
    speed = max(0, js_round(100 - sum(t < 2000 for t in timings.values()) / len(timings) * 160)) if timings else None
    reliability = js_round(speed * .30 + pattern * .35 + mirror * .35) if speed is not None and mirror is not None else None
    result = {
        'pattern': pattern, 'mirror': mirror, 'speed': speed, 'reliability': reliability,
        'timed_answers': len(timings), 'longest_run': longest, 'dominance_percent': js_round(dominance * 100),
    }
    if family == 'behaviormatrix':
        controls = [item_score(q, a, family) for q, a in zip(questions, answers) if q['dimKey'] == 'VALIDATION']
        result['psychometric'] = js_round(mean(controls)) if controls else None
    return result


def calculate_original(snapshot, values, timings=None):
    family = snapshot['original_family']
    questions, answers = [], []
    for question in snapshot['questions']:
        if str(question['id']) not in values:
            raise ValidationError('Faltan respuestas obligatorias del cuestionario original.')
        raw = float(values[str(question['id'])])
        if not math.isfinite(raw) or raw != int(raw):
            raise ValidationError('Respuesta original no válida.')
        answer = int(raw)
        item_score(question['original'], answer, family)
        questions.append(question['original'])
        answers.append(answer)
    result_vector = vector(family, questions, answers)
    competencies, archetypes, overall = derived_scores(family, result_vector)
    ranked = sorted(archetypes, key=lambda name: -archetypes[name])
    dimensions = []
    for code, score in result_vector.items():
        metadata = snapshot['dimensions'][code]
        contributing = [q for q in questions if q['dimKey'] == code or code in (q.get('opt_dims') or [])]
        dimensions.append({
            **metadata, 'code': code, 'score': str(score),
            'answered': len(contributing), 'total': len(contributing),
        })
    return {
        'method': METHOD, 'dimensions': dimensions, 'vector': result_vector,
        'competencies': competencies, 'overall_score': overall,
        'archetypes': [{'name': snapshot['original_archetypes'][name].get('name', name),
                        'score': js_round(archetypes[name])} for name in ranked],
        'validation': validation_scores(family, questions, answers, timings or {}),
        'limitations': snapshot['limitations'], 'validation_evidence': snapshot['validation_evidence'],
        'ai_used': False,
        'notice': 'Cálculo orientativo reproducible. Sin ruido aleatorio, datos de relleno ni percentiles no acreditados. '
                  'Los controles y tiempos declarados por el navegador no certifican fiabilidad psicométrica.',
    }
