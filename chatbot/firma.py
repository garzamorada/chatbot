"""Verificación de la firma de los webhooks entrantes de chatealo.

chatealo no documenta (todavía) en qué header manda la firma ni con qué
formato, y Chatwoot de base no firma. Así que esto es deliberadamente
tolerante: prueba HMAC-SHA256/SHA1 del body crudo en hex y base64 (con y sin
prefijo `algo=`) contra la lista de headers de firma habituales, y devuelve
también un detalle legible para poder cerrar el formato exacto mirando el log.
"""
import base64
import hashlib
import hmac
import logging

logger = logging.getLogger('chatbot.webhook')

# request.META usa el prefijo HTTP_ y mayúsculas con guión bajo.
HEADERS_FIRMA = (
    'HTTP_X_CHATEALO_SIGNATURE',
    'HTTP_X_CHATEALO_SIGNATURE_256',
    'HTTP_X_CHATEALO_HMAC',
    'HTTP_X_CHATEALO_WEBHOOK_SIGNATURE',
    'HTTP_X_HUB_SIGNATURE_256',
    'HTTP_X_HUB_SIGNATURE',
    'HTTP_X_WEBHOOK_SIGNATURE',
    'HTTP_X_SIGNATURE',
    'HTTP_X_CHATWOOT_SIGNATURE',
    'HTTP_SIGNATURE',
)

_PREFIJOS_TOKEN = ('sha256', 'sha1', 'v1', 's')


def _firmas_esperadas(secret_bytes, body):
    out = {}
    for algo in ('sha256', 'sha1'):
        dig = hmac.new(secret_bytes, body, getattr(hashlib, algo)).digest()
        hex_, b64 = dig.hex(), base64.b64encode(dig).decode()
        out[f'{algo}/hex'] = hex_
        out[f'{algo}/b64'] = b64
        out[f'{algo}=/hex'] = f'{algo}={hex_}'
    return out


def _tokens(valor):
    """De 'sha256=abc', 't=1,v1=abc', 'abc,def' -> ['abc', 'def', ...]."""
    tokens = []
    for parte in valor.replace(';', ',').split(','):
        parte = parte.strip()
        if not parte:
            continue
        tokens.append(parte)
        if '=' in parte and parte.split('=', 1)[0].strip().lower() in _PREFIJOS_TOKEN:
            tokens.append(parte.split('=', 1)[1].strip())
    return tokens


def verificar(request, secret):
    """Devuelve (ok, detalle).

    ok = None  -> no hay secreto configurado, o no vino ninguna cabecera de firma
    ok = True  -> alguna cabecera coincide
    ok = False -> vino cabecera de firma pero ninguna coincide
    Nunca levanta excepción.
    """
    if not secret:
        return None, 'sin secreto de firma configurado'
    try:
        body = request.body or b''
        presentes = {h[5:]: v for h in HEADERS_FIRMA if (v := request.META.get(h))}
        if not presentes:
            sospechosos = sorted(
                k[5:] for k in request.META
                if k.startswith('HTTP_') and ('SIGN' in k or 'HMAC' in k or 'DIGEST' in k)
            )
            return None, f'sin cabecera de firma conocida (sospechosos: {sospechosos or "ninguno"})'
        esperadas = _firmas_esperadas(secret.encode(), body)
        for header, valor in presentes.items():
            for token in _tokens(valor):
                for nombre, esperada in esperadas.items():
                    if hmac.compare_digest(token, esperada):
                        return True, f'{header} coincide ({nombre})'
        return False, f'ninguna coincide; recibido: {presentes}'
    except Exception as exc:  # nunca romper el webhook por esto
        logger.exception('Error verificando la firma del webhook de chatealo')
        return None, f'error verificando firma: {exc}'
