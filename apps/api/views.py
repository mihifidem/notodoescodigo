import json
import logging

import httpx
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

MAX_TOKENS_LIMIT = 4000


def _get_openai_client():
    from openai import OpenAI
    try:
        import truststore
        truststore.inject_into_ssl()
        return OpenAI(api_key=settings.OPENAI_API_KEY)
    except ImportError:
        return OpenAI(
            api_key=settings.OPENAI_API_KEY,
            http_client=httpx.Client(verify=False),
        )


@csrf_exempt
@require_POST
def ai_generate(request):
    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        return JsonResponse(
            {'error': 'OPENAI_API_KEY no está configurada en el servidor.'},
            status=503,
        )

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'JSON inválido.'}, status=400)

    messages = body.get('messages')
    if not messages or not isinstance(messages, list):
        return JsonResponse({'error': 'El campo "messages" es requerido.'}, status=400)

    model = body.get('model', 'gpt-4o')
    max_tokens = min(int(body.get('max_tokens', 1500)), MAX_TOKENS_LIMIT)
    temperature = float(body.get('temperature', 0.7))

    try:
        client = _get_openai_client()
    except ImportError:
        return JsonResponse(
            {'error': 'La librería openai no está instalada. Ejecuta: pip install openai'},
            status=503,
        )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return JsonResponse({
            'choices': [
                {
                    'message': {
                        'role': response.choices[0].message.role,
                        'content': response.choices[0].message.content,
                    }
                }
            ]
        })
    except Exception as exc:
        logger.exception('Error calling OpenAI API')
        return JsonResponse({'error': str(exc)}, status=502)
