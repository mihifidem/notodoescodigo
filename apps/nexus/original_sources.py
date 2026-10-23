"""Read literal question banks; never execute uploaded JavaScript or templates."""

import ast
import hashlib
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup


SOURCES = {
    'behaviormatrix': 'behaviormatrix.html',
    'careernavigator': 'careernavigator.html',
    'conglicttsync': 'conflictSync.html',
    'decissioncore': 'DecisionCore.html',
    'identitymap': 'IdentityMap.html',
    'personasphere': 'PersonaSphere.html',
    'techmind': 'techmind-full.html',
}
MODES = ('essential', 'advanced', 'premium')
DATA_DIR = Path(__file__).parent / 'data' / 'originals'
TOKEN = re.compile(
    r"""\s+|//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|"""
    r'-?\d+(?:\.\d+)?|[A-Za-z_$][\w$]*|[{}\[\]:,]',
    re.DOTALL,
)


class SourceError(ValueError):
    pass


class LiteralReader:
    def __init__(self, text, position=0):
        self.text = text
        self.position = position

    def token(self):
        while True:
            match = TOKEN.match(self.text, self.position)
            if not match:
                raise SourceError(f'Literal no admitido en posición {self.position}.')
            self.position = match.end()
            token = match.group()
            if token.isspace() or token.startswith(('//', '/*')):
                continue
            return token

    def value(self, token=None):
        token = self.token() if token is None else token
        if token in ('{', '['):
            result = {} if token == '{' else []
            closing = '}' if token == '{' else ']'
            token = self.token()
            while token != closing:
                if isinstance(result, dict):
                    key = ast.literal_eval(token) if token.startswith(("'", '"')) else token
                    if key in result:
                        raise SourceError(f'Clave duplicada: {key}.')
                    if self.token() != ':':
                        raise SourceError('Se esperaba dos puntos.')
                    result[key] = self.value()
                else:
                    result.append(self.value(token))
                token = self.token()
                if token == closing:
                    break
                if token != ',':
                    raise SourceError('Se esperaba coma.')
                token = self.token()
            return result
        if token.startswith(("'", '"')):
            return ast.literal_eval(token.replace(r'\/', '/'))
        if token in ('true', 'false', 'null'):
            return {'true': True, 'false': False, 'null': None}[token]
        if re.fullmatch(r'-?\d+(?:\.\d+)?', token):
            return float(token) if '.' in token else int(token)
        raise SourceError(f'No es un dato literal: {token}.')


def literal_after(text, pattern):
    match = re.search(pattern, text)
    if match is None:
        raise SourceError(f'No se encuentra el bloque {pattern}.')
    return LiteralReader(text, match.end()).value()


def select_questions(family, bank, mode):
    questions = []
    for dimension, items in bank.items():
        for item in items:
            versions = item.get('versions', [])
            if mode in versions or (not versions and family in ('careernavigator', 'conglicttsync')):
                questions.append({**item, 'dimKey': dimension})
    if family == 'behaviormatrix':
        core, scenarios, controls = {
            'essential': (20, 0, 2), 'advanced': (40, 6, 4), 'premium': (80, 10, 6),
        }[mode]
        validation = [q for q in questions if q['dimKey'] == 'VALIDATION'][:controls]
        scenario = [q for q in questions if q['type'] == 'scenario' or q['dimKey'] == 'SCENARIOS']
        regular = [q for q in questions if q not in validation
                   and q['dimKey'] not in ('VALIDATION', 'SCENARIOS') and q['type'] != 'scenario']
        questions = regular[:core - len(validation)] + validation + scenario[:scenarios]
    order = {code: index for index, code in enumerate(bank)}
    def phase(question):
        match = re.fullmatch(r'Fase (\d+)', question['phase'])
        return int(match[1]) if match else 99
    if family == 'careernavigator':
        questions.sort(key=phase)
        limit = {'essential': 20, 'advanced': 40, 'premium': 70}[mode]
        buckets = {code: [q for q in questions if q['dimKey'] == code] for code in bank}
        chosen = []
        while len(chosen) < min(limit, len(questions)):
            for bucket in buckets.values():
                if bucket and len(chosen) < limit:
                    chosen.append(bucket.pop(0))
        return [q for q in questions if q in chosen]
    lexical = family in ('identitymap', 'personasphere', 'decissioncore')
    questions.sort(key=lambda q: (phase(q), order[q['dimKey']], q['id'] if lexical else ''))
    if lexical:
        limits = (20, 40, 70) if family == 'decissioncore' else (30, 60, 120)
        questions = questions[:limits[MODES.index(mode)]]
    return questions


def extract_source(directory, family):
    filename = SOURCES[family]
    raw = (Path(directory) / filename).read_bytes()
    soup = BeautifulSoup(raw.decode('utf-8-sig'), 'html.parser')
    scripts = '\n'.join(s.get_text() for s in soup.find_all('script') if not s.get('src'))
    external = {
        'techmind': 'techmind-questions.json',
    }.get(family)
    source_files = {filename: hashlib.sha256(raw).hexdigest()}
    if external:
        data = (Path(directory) / external).read_bytes()
        loaded = json.loads(data.decode('utf-8-sig'))
        bank = loaded.get('questions', loaded)
        source_files[external] = hashlib.sha256(data).hexdigest()
    elif family == 'behaviormatrix':
        bank = {}
        for match in re.finditer(r"qb(?:\['(\w+)'\]|\.(\w+))\s*=\s*", scripts):
            bank[match[1] or match[2]] = LiteralReader(scripts, match.end()).value()
    elif family in ('careernavigator', 'conglicttsync'):
        bank = literal_after(scripts, r'function\s+getQB\s*\(\)\s*\{\s*return\s*')
    else:
        bank = literal_after(scripts, r'(?:const|let)\s+QB\s*=\s*')
    labels = literal_after(scripts, r'(?:const|var)\s+DIM_LABELS\s*=\s*')
    archetypes = literal_after(scripts, r'(?:const|var)\s+ARCHETYPES\s*=\s*')
    if not isinstance(bank, dict) or not bank:
        raise SourceError(f'{filename}: banco vacío o no válido.')
    keys = set()
    for dimension, items in bank.items():
        if not isinstance(items, list):
            raise SourceError(f'{dimension}: se esperaba una lista.')
        for question in items:
            key = (dimension, question['id'])
            if key in keys or not isinstance(question.get('text'), str) or not question['text'].strip():
                raise SourceError(f'{filename}: pregunta duplicada o vacía: {key}.')
            keys.add(key)
            if question['type'] not in ('likert', 'abcd', 'sit', 'scenario'):
                raise SourceError(f'Tipo no admitido: {question["type"]}.')
            if question['type'] != 'likert' and not question.get('options'):
                raise SourceError(f'Faltan opciones en {key}.')
    return {
        'schema': 1, 'family': family, 'sources': source_files,
        'source_notice': (
            'Banco BM interno elegido expresamente: el JSON externo contiene dimensiones TM incompatibles.'
            if family == 'behaviormatrix' else ''
        ),
        'labels': labels, 'archetypes': archetypes, 'questions': bank,
        'modes': {mode: [[q['dimKey'], q['id']] for q in select_questions(family, bank, mode)]
                  for mode in MODES},
    }


def load_bank(family):
    if family not in SOURCES:
        raise SourceError('Familia original desconocida.')
    return json.loads((DATA_DIR / f'{family}.json').read_text(encoding='utf-8'))


def mode_questions(bank, mode):
    lookup = {(dimension, q['id']): {**q, 'dimKey': dimension}
              for dimension, items in bank['questions'].items() for q in items}
    return [lookup[tuple(key)] for key in bank['modes'][mode]]
