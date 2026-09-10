import logging

import requests

logger = logging.getLogger('chatbot.chatealo')

TIMEOUT = 10


def _headers(config):
    return {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': config.chatealo_authorization,
        'X-Chatealo-Admin-Id': config.chatealo_admin_id,
        'X-AppChatealo-User-Token': config.chatealo_user_token,
    }


def _base(config):
    return f'{config.chatealo_base_url.rstrip("/")}/api/v1/accounts/{config.chatealo_account_id}'


def enviar_mensaje(config, conversation_id, texto, boton=None, items=None):
    """POST .../conversations/{id}/messages — mensaje saliente.

    - `items`: lista de {'title', 'value'} → se manda como `content_type:
      input_select`, que WhatsApp renderiza como botones de respuesta (1-3) o
      lista desplegable (4-10). El `content` de texto queda como fallback.
    - `boton`: dict con 'texto' y 'url' → `content_type: cards` con una acción
      de tipo link (botón de acción).
    - sin nada de eso → texto plano.

    El canal decide cómo renderizarlo; si no lo soporta, el `content` igual llega.
    """
    url = f'{_base(config)}/conversations/{conversation_id}/messages'
    if items:
        body = {
            'message_type': 'outgoing',
            'content': texto,
            'content_type': 'input_select',
            'content_attributes': {'items': list(items)},
        }
    elif boton and boton.get('texto') and boton.get('url'):
        body = {
            'message_type': 'outgoing',
            'content': texto,
            'content_type': 'cards',
            'content_attributes': {
                'items': [{
                    'title': (texto or ' ')[:1024],
                    'actions': [{
                        'type': 'link',
                        'text': str(boton['texto'])[:20],
                        'uri': boton['url'],
                    }],
                }],
            },
        }
    else:
        body = {'content': texto, 'message_type': 'outgoing', 'content_type': 'text'}
    r = requests.post(url, json=body, headers=_headers(config), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def actualizar_labels(config, conversation_id, labels):
    """POST .../conversations/{id}/labels — reemplaza el set completo de etiquetas
    (según el comportamiento real de Chatwoot, en el que está basado chatealo)."""
    url = f'{_base(config)}/conversations/{conversation_id}/labels'
    body = {'labels': sorted(set(labels))}
    r = requests.post(url, json=body, headers=_headers(config), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def listar_webhooks(config):
    """GET .../webhooks — webhooks registrados en la cuenta de chatealo.
    Devuelve la lista [{id, url, account_id, subscriptions}] (desde
    payload.webhooks de la respuesta)."""
    url = f'{_base(config)}/webhooks'
    r = requests.get(url, headers=_headers(config), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json() or {}
    return (data.get('payload') or {}).get('webhooks') or []


def crear_webhook(config, webhook_url, subscriptions):
    """POST .../webhooks — registra webhook_url para los eventos `subscriptions`."""
    url = f'{_base(config)}/webhooks'
    body = {'url': webhook_url, 'subscriptions': list(subscriptions)}
    r = requests.post(url, json=body, headers=_headers(config), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def cambiar_estado_conversacion(config, conversation_id, status):
    """POST .../conversations/{id}/toggle_status — status en {'open','resolved','pending'}.
    Se usa para la opción 'Terminar la conversación' (status='resolved')."""
    url = f'{_base(config)}/conversations/{conversation_id}/toggle_status'
    r = requests.post(url, json={'status': status}, headers=_headers(config), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()
