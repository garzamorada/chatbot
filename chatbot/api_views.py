import json
import logging
import re
from datetime import timedelta

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import chatealo_client, defaults, firma
from .defaults import aplicar_variables
from .horarios import esta_en_horario, mensaje_fuera_de_horario
from .labels import LABEL_MENU_RAIZ, SLUG_EQUIPO_GENERAL, aplicar_transicion_labels
from .models import ConfiguracionChatbot, Conversacion, ConversacionLog, InboxChatealo, MenuOpcion

logger = logging.getLogger('chatbot.webhook')


def _label_de(opcion_menu):
    return opcion_menu.label_menu if opcion_menu else LABEL_MENU_RAIZ


def _opciones_de(opcion_padre):
    qs = MenuOpcion.objects.filter(activo=True)
    qs = qs.filter(parent=opcion_padre) if opcion_padre else qs.filter(parent__isnull=True)
    return list(qs.order_by('orden', 'texto'))


def _match_opcion(texto, opciones):
    texto_norm = texto.strip()
    if texto_norm.isdigit():
        idx = int(texto_norm) - 1
        if 0 <= idx < len(opciones):
            return opciones[idx]
    # tolera que el usuario copie la línea completa "N - nombre de la opción"
    m = re.match(r'^\s*(\d+)\s*[-.)]\s*', texto_norm)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(opciones):
            return opciones[idx]
    for o in opciones:
        if o.texto.strip().lower() == texto_norm.lower():
            return o
    for o in opciones:
        if o.slug == texto_norm.lower():
            return o
    return None


# Prefijo de numeración manual que el usuario pudo haber dejado en el texto de
# la opción ("1 - ", "2) ", "5- ", "3. "): se saca para no numerar dos veces.
_RE_NUM_PREFIJO = re.compile(r'^\s*\d+\s*[-.):–—]\s*')


def _limpiar_texto_opcion(texto):
    return _RE_NUM_PREFIJO.sub('', texto or '').strip()


def _nombre_area(opcion_menu):
    if not opcion_menu:
        return 'Menú principal'
    return _limpiar_texto_opcion(opcion_menu.texto) or 'Menú principal'


def _texto_menu(opciones):
    # El orden lo define el panel (campo `orden`, seteado por arrastre); acá se
    # numera 1-indexado en ese mismo orden con el formato "N - nombre" para que
    # el usuario final responda con el número y `_match_opcion` lo resuelva.
    return '\n'.join(
        f'{i} - {_limpiar_texto_opcion(o.texto)}' for i, o in enumerate(opciones, start=1)
    )


def _encabezado_menu(config, opcion_menu, nombre=''):
    """Saludo configurable. Para un área usa `plantilla_saludo_area` con {area}
    reemplazado por el nombre del submenú (sin número); para la raíz usa
    `plantilla_saludo_inicial`. `{nombre}` se reemplaza por el del contacto."""
    if opcion_menu:
        plantilla = (config.plantilla_saludo_area or '').strip() or defaults.SALUDO_AREA
        return aplicar_variables(plantilla, area=_nombre_area(opcion_menu), nombre=nombre)
    plantilla = (config.plantilla_saludo_inicial or '').strip() or defaults.SALUDO_INICIAL
    return aplicar_variables(plantilla, nombre=nombre)


def _menu_con_encabezado(config, opcion_menu, opciones, nombre=''):
    """Saludo + listado numerado de opciones. El nombre del área va SIN número;
    sólo las opciones se numeran."""
    cuerpo = _texto_menu(opciones) if opciones else '(sin opciones)'
    return f'{_encabezado_menu(config, opcion_menu, nombre)}\n{cuerpo}'


def _url_de_archivo(request, archivo):
    if not archivo:
        return None
    return request.build_absolute_uri(archivo.archivo.url)


def _url_archivo(request, opcion):
    if not opcion.archivo_id:
        return None
    return request.build_absolute_uri(opcion.archivo.archivo.url)


def _extraer_inbox(payload):
    """(inbox_id, nombre, channel) tolerante a la forma del payload: los
    distintos eventos de chatealo ponen la inbox en lugares distintos."""
    inbox = payload.get('inbox') or {}
    conv = payload.get('conversation') or {}
    conv_inbox = conv.get('inbox') or {}
    inbox_id = (
        inbox.get('id') or payload.get('inbox_id')
        or conv.get('inbox_id') or conv_inbox.get('id')
    )
    nombre = inbox.get('name') or conv_inbox.get('name') or ''
    channel = conv.get('channel') or payload.get('channel') or ''
    return inbox_id, nombre, channel


def _registrar_inbox_seguro(payload):
    try:
        inbox_id, nombre, channel = _extraer_inbox(payload)
        account_id = (payload.get('account') or {}).get('id')
        InboxChatealo.registrar_desde_payload(inbox_id, nombre, channel, account_id)
    except Exception:
        logger.exception('Error registrando inbox del webhook')


def _procesar_evento_conversacion(payload, evento):
    """conversation_created / conversation_status_changed: en estos eventos el
    payload ES la conversación (no viene anidada). Se registra/actualiza la
    fila local para tener los datos listos para interactuar después."""
    conv_id = payload.get('id')
    if not conv_id:
        return
    sender = (payload.get('meta') or {}).get('sender') or {}
    contacto = sender.get('phone_number') or sender.get('identifier') or ''
    nombre = sender.get('name') or ''
    account_id = (payload.get('account') or {}).get('id')
    inbox_id = payload.get('inbox_id')
    estado = payload.get('status') or ''

    # el agente marcó "resolved" tras derivar/terminar -> si el contacto vuelve
    # a escribir, el bot se reactiva (ver rama `finalizado` del webhook).
    resuelta = estado == 'resolved'

    conversacion, creado = Conversacion.objects.get_or_create(
        conversation_id=conv_id,
        defaults={
            'account_id': account_id, 'inbox_id': inbox_id,
            'contacto': contacto, 'nombre_contacto': nombre, 'estado': estado,
            'chatealo_resuelta': resuelta,
        },
    )
    if not creado:
        campos = ['actualizado']
        for attr, valor in (
            ('estado', estado), ('inbox_id', inbox_id), ('contacto', contacto),
            ('nombre_contacto', nombre), ('account_id', account_id),
        ):
            if valor and getattr(conversacion, attr) != valor:
                setattr(conversacion, attr, valor)
                campos.append(attr)
        if resuelta and not conversacion.chatealo_resuelta:
            conversacion.chatealo_resuelta = True
            campos.append('chatealo_resuelta')
        conversacion.save(update_fields=campos)

    _log(conversacion, '', None, 'CONVERSACION',
         f'{evento}: estado={estado or "?"}' + (' (nueva)' if creado else ''))


def _procesar_evento_conversacion_seguro(payload, evento):
    try:
        _procesar_evento_conversacion(payload, evento)
    except Exception:
        logger.exception('Error procesando evento de conversación %s', evento)


def _procesar_message_updated(payload):
    """message_updated son sobre todo los ACK de WhatsApp (sent/delivered/read)
    de mensajes salientes; solo interesa `status=failed` para dejar constancia
    de que una respuesta no llegó. El resto se ignora (sería mucho ruido)."""
    if payload.get('status') != 'failed':
        return
    conv_id = (payload.get('conversation') or {}).get('id')
    if not conv_id:
        return
    try:
        conversacion = Conversacion.objects.get(conversation_id=conv_id)
    except Conversacion.DoesNotExist:
        return
    error = (payload.get('content_attributes') or {}).get('external_error') or 'sin detalle'
    _log(conversacion, (payload.get('content') or '')[:120], None, 'MSG_FALLIDO',
         f'message_updated status=failed (msg {payload.get("id")}): {error}')


def _procesar_message_updated_seguro(payload):
    try:
        _procesar_message_updated(payload)
    except Exception:
        logger.exception('Error procesando message_updated')


def _log(conversacion, mensaje, opcion, accion, detalle=''):
    ConversacionLog.objects.create(
        conversacion=conversacion, mensaje_recibido=mensaje, opcion=opcion,
        accion=accion, detalle=detalle,
    )


def _aplicar_labels_seguro(config, conversation_id, labels):
    try:
        chatealo_client.actualizar_labels(config, conversation_id, labels)
        return ''
    except Exception as exc:
        logger.exception('Error actualizando labels de conversación %s', conversation_id)
        return f'Error actualizando labels: {exc}'


def _enviar_seguro(config, conversation_id, texto):
    try:
        chatealo_client.enviar_mensaje(config, conversation_id, texto)
        return ''
    except Exception as exc:
        logger.exception('Error enviando mensaje a conversación %s', conversation_id)
        return f'Error enviando mensaje: {exc}'


def _resolver_conversacion_segura(config, conversation_id):
    try:
        chatealo_client.cambiar_estado_conversacion(config, conversation_id, 'resolved')
        return ''
    except Exception as exc:
        logger.exception('Error resolviendo conversación %s', conversation_id)
        return f'Error resolviendo conversación: {exc}'


def _reactivar_conversacion(config, conversation_id, conversacion, labels_actuales):
    """El contacto vuelve a escribir en una conversación ya derivada/terminada
    que el agente resolvió: se le saca la etiqueta de equipo (`equipo-*` /
    `hist-equipo-*`) en chatealo, se vuelve a la raíz y se muestra el menú
    principal. Las `menu-*` no se tocan (no las aplicamos). No modifica
    `conversacion` en la BD (lo hace el `_cerrar` del caller)."""
    limpias = [l for l in labels_actuales if not (l.startswith('equipo-') or l.startswith('hist-equipo-'))]
    err_labels = ''
    if set(limpias) != set(labels_actuales):
        err_labels = _aplicar_labels_seguro(config, conversation_id, sorted(set(limpias)))

    conversacion.finalizado = False
    conversacion.chatealo_resuelta = False
    conversacion.menu_actual = None
    conversacion.label_equipo_actual = ''
    conversacion.menu_mostrado = True

    hijos = _opciones_de(None)
    err_msg = _enviar_seguro(
        config, conversation_id,
        _menu_con_encabezado(config, None, hijos, conversacion.nombre_contacto),
    )
    return err_labels, err_msg


def _navegar_a_menu(config, conversation_id, conversacion, nuevo_menu, mensaje_extra=''):
    """Cambia conversacion.menu_actual y manda el listado de opciones del nuevo
    menú. Usado por SUBMENU, VOLVER e INICIO — solo cambia cómo se calcula
    `nuevo_menu`. Las etiquetas `menu-*` NO se aplican en chatealo: sólo se
    registran en el log interno (`nota_menu`)."""
    hijos = _opciones_de(nuevo_menu)
    label_anterior = _label_de(conversacion.menu_actual)
    label_nueva = _label_de(nuevo_menu)
    nota_menu = f'menu: {label_anterior} → {label_nueva}'

    # si el primer mensaje del contacto ya matcheó una opción, igual conviene
    # aclarar que es un bot antes de mostrarle el submenú.
    if not conversacion.menu_mostrado and not mensaje_extra:
        mensaje_extra = aplicar_variables(
            (config.mensaje_bienvenida or '').strip() or defaults.MENSAJE_BIENVENIDA,
            nombre=conversacion.nombre_contacto,
        )

    respuesta = _menu_con_encabezado(config, nuevo_menu, hijos, conversacion.nombre_contacto)
    if mensaje_extra:
        respuesta = mensaje_extra + '\n\n' + respuesta
    err_msg = _enviar_seguro(config, conversation_id, respuesta)

    conversacion.menu_actual = nuevo_menu
    conversacion.menu_mostrado = True
    return nota_menu, err_msg


@csrf_exempt
@require_POST
def webhook_chatealo(request, secret):
    config = ConfiguracionChatbot.obtener()
    if secret != config.webhook_secret:
        return JsonResponse({'error': 'not found'}, status=404)

    firma_ok, firma_detalle = firma.verificar(request, config.webhook_firma_secret)
    if firma_ok is False:
        logger.warning('Webhook chatealo: firma INVÁLIDA — %s', firma_detalle)
        if config.webhook_firma_enforce:
            return JsonResponse({'error': 'invalid signature'}, status=401)
    elif firma_ok is True:
        logger.debug('Webhook chatealo: firma OK — %s', firma_detalle)
    elif config.webhook_firma_secret:
        # hay secreto pero no se pudo comparar: sirve para descubrir el formato real
        logger.warning('Webhook chatealo: firma NO verificada — %s', firma_detalle)

    try:
        payload = json.loads(request.body or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse(status=200)  # payload ilegible: no tiene sentido reintentar

    # Se registran TODAS las bandejas que le pegan al webhook (cualquier evento),
    # para poder asociarles nombre/tipo de fuente desde el panel.
    _registrar_inbox_seguro(payload)

    evento = payload.get('event')
    if evento in ('conversation_created', 'conversation_status_changed'):
        _procesar_evento_conversacion_seguro(payload, evento)
        return HttpResponse(status=200)
    if evento == 'message_updated':
        _procesar_message_updated_seguro(payload)
        return HttpResponse(status=200)

    if evento != 'message_created':
        return HttpResponse(status=200)
    if payload.get('message_type') != 'incoming' or payload.get('private'):
        return HttpResponse(status=200)

    conv_data = payload.get('conversation') or {}
    conversation_id = conv_data.get('id')
    if not conversation_id:
        return HttpResponse(status=200)

    account_id = (payload.get('account') or {}).get('id')
    sender = payload.get('sender') or {}
    contacto = sender.get('phone_number') or sender.get('identifier') or ''
    message_id = payload.get('id')
    texto = payload.get('content') or ''
    labels_actuales = list(conv_data.get('labels') or [])

    conversacion, _created = Conversacion.objects.get_or_create(
        conversation_id=conversation_id,
        defaults={
            'account_id': account_id, 'contacto': contacto,
            'inbox_id': payload.get('inbox_id') or conv_data.get('inbox_id'),
            'nombre_contacto': sender.get('name') or '',
            'estado': conv_data.get('status') or '',
        },
    )

    if message_id and conversacion.ultimo_message_id == message_id:
        return HttpResponse(status=200)  # reintento del mismo evento

    # el contacto escribió: se registra la actividad y se cancela cualquier
    # re-mostrado de menú pendiente (lo re-agenda solo el handler de RESPUESTA).
    conversacion.ultima_actividad = timezone.now()
    conversacion.remostrar_menu_en = None

    def _cerrar(campos_extra=None):
        conversacion.ultimo_message_id = message_id
        conversacion.contacto = contacto or conversacion.contacto
        campos = [
            'ultimo_message_id', 'contacto', 'menu_mostrado',
            'ultima_actividad', 'remostrar_menu_en', 'actualizado',
        ] + list(campos_extra or [])
        conversacion.save(update_fields=campos)

    if payload.get('content_type') != 'text':
        _log(conversacion, texto, None, 'IGNORADO', 'content_type no es texto')
        _cerrar()
        return HttpResponse(status=200)

    if conversacion.finalizado:
        if conversacion.chatealo_resuelta:
            # el agente resolvió la conversación y el contacto vuelve a escribir:
            # se reactiva el bot, se limpian etiquetas y se muestra el menú.
            err_labels, err_msg = _reactivar_conversacion(
                config, conversation_id, conversacion, labels_actuales,
            )
            _cerrar(['finalizado', 'chatealo_resuelta', 'menu_actual', 'label_equipo_actual'])
            _log(conversacion, texto, None, 'REACTIVADA', '; '.join(filter(None, [err_labels, err_msg])))
            return HttpResponse(status=200)
        _log(conversacion, texto, None, 'IGNORADO', 'conversación ya derivada, no se responde más')
        _cerrar()
        return HttpResponse(status=200)

    opciones_actuales = _opciones_de(conversacion.menu_actual)
    opcion = _match_opcion(texto, opciones_actuales)

    if opcion is None:
        if not opciones_actuales:
            err = _enviar_seguro(config, conversation_id, 'Por el momento no hay opciones configuradas.')
            _log(conversacion, texto, None, 'INVALIDA', err)
            _cerrar()
            return HttpResponse(status=200)

        # "No entendí esa opción" SÓLO cuando la persona está dentro de un
        # submenú y eligió algo inválido. En el menú principal nunca: se le
        # muestra el menú (con bienvenida la primera vez), sin importar qué
        # haya escrito.
        nombre = conversacion.nombre_contacto
        if conversacion.menu_actual is not None:
            respuesta = 'No entendí esa opción.\n\n' + _menu_con_encabezado(
                config, conversacion.menu_actual, opciones_actuales, nombre,
            )
            accion = 'INVALIDA'
        elif not conversacion.menu_mostrado:
            bienvenida = aplicar_variables(
                (config.mensaje_bienvenida or '').strip() or defaults.MENSAJE_BIENVENIDA, nombre=nombre,
            )
            respuesta = bienvenida + '\n\n' + _menu_con_encabezado(config, None, opciones_actuales, nombre)
            accion = 'BIENVENIDA'
        else:
            respuesta = _menu_con_encabezado(config, None, opciones_actuales, nombre)
            accion = 'MENU'
        if not esta_en_horario():
            respuesta = mensaje_fuera_de_horario() + '\n\n' + respuesta
        err = _enviar_seguro(config, conversation_id, respuesta)
        conversacion.menu_mostrado = True
        _log(conversacion, texto, None, accion, err)
        _cerrar()
        return HttpResponse(status=200)

    if opcion.tipo == 'SUBMENU':
        nota_menu, err_msg = _navegar_a_menu(config, conversation_id, conversacion, opcion)
        _cerrar(['menu_actual'])
        _log(conversacion, texto, opcion, 'MENU', '; '.join(filter(None, [nota_menu, err_msg])))
        return HttpResponse(status=200)

    if opcion.tipo == 'VOLVER':
        destino = conversacion.menu_actual.parent if conversacion.menu_actual else None
        nota_menu, err_msg = _navegar_a_menu(config, conversation_id, conversacion, destino)
        _cerrar(['menu_actual'])
        _log(conversacion, texto, opcion, 'MENU', '; '.join(filter(None, [nota_menu, err_msg])))
        return HttpResponse(status=200)

    if opcion.tipo == 'INICIO':
        nota_menu, err_msg = _navegar_a_menu(config, conversation_id, conversacion, None)
        _cerrar(['menu_actual'])
        _log(conversacion, texto, opcion, 'MENU', '; '.join(filter(None, [nota_menu, err_msg])))
        return HttpResponse(status=200)

    if opcion.tipo == 'RESPUESTA':
        respuesta = opcion.respuesta_texto
        url_archivo = _url_archivo(request, opcion)
        if url_archivo:
            respuesta = f'{respuesta}\n{url_archivo}'
        err_msg = _enviar_seguro(config, conversation_id, respuesta)

        # agenda volver a mostrar el menú donde estaba, pasados N segundos.
        if config.segundos_remostrar_menu:
            conversacion.remostrar_menu_en = timezone.now() + timedelta(seconds=config.segundos_remostrar_menu)
        _cerrar()
        _log(conversacion, texto, opcion, 'RESPUESTA', err_msg)
        return HttpResponse(status=200)

    if opcion.tipo == 'TERMINAR':
        # Mensaje único de despedida: es de la configuración general, no de
        # esta opción puntual, para que todos los "Terminar" del árbol digan
        # lo mismo y se editen en un solo lugar.
        respuesta = aplicar_variables(
            (config.mensaje_despedida or '').strip() or defaults.MENSAJE_DESPEDIDA,
            nombre=conversacion.nombre_contacto,
        )
        url_archivo = _url_de_archivo(request, config.archivo_despedida)
        if url_archivo:
            respuesta = f'{respuesta}\n{url_archivo}'
        err_msg = _enviar_seguro(config, conversation_id, respuesta)
        err_resolver = _resolver_conversacion_segura(config, conversation_id)

        conversacion.finalizado = True
        # ya la resolvimos en chatealo: si el contacto vuelve a escribir, reactivar.
        conversacion.chatealo_resuelta = True
        _cerrar(['finalizado', 'chatealo_resuelta'])
        _log(conversacion, texto, opcion, 'TERMINAR', '; '.join(filter(None, [err_msg, err_resolver])))
        return HttpResponse(status=200)

    # DERIVACION
    if not esta_en_horario():
        hijos_actuales = _opciones_de(conversacion.menu_actual)
        respuesta = mensaje_fuera_de_horario()
        if hijos_actuales:
            respuesta += '\n\nMientras tanto, elegí una opción:\n' + _texto_menu(hijos_actuales)
        err_msg = _enviar_seguro(config, conversation_id, respuesta)
        conversacion.menu_mostrado = True
        _cerrar()
        _log(conversacion, texto, opcion, 'FUERA_HORARIO', err_msg)
        return HttpResponse(status=200)

    # etiqueta 'equipo-<slug del menú actual>' (el menú donde el usuario
    # estaba parado cuando pidió hablar con un operador). Si pidió derivación
    # desde el menú principal no hay categoría todavía: 'equipo-general'.
    slug_equipo = conversacion.menu_actual.slug if conversacion.menu_actual else SLUG_EQUIPO_GENERAL
    label_nueva_equipo = f'equipo-{slug_equipo}'
    nuevas_labels = aplicar_transicion_labels(
        labels_actuales, conversacion.label_equipo_actual, label_nueva_equipo,
    )
    err_labels = _aplicar_labels_seguro(config, conversation_id, nuevas_labels)

    respuesta = opcion.mensaje_derivacion or 'Te estamos derivando con un agente. En breve te van a contactar.'
    url_archivo = _url_archivo(request, opcion)
    if url_archivo:
        respuesta = f'{respuesta}\n{url_archivo}'
    err_msg = _enviar_seguro(config, conversation_id, respuesta)

    conversacion.label_equipo_actual = label_nueva_equipo
    conversacion.finalizado = True
    # recién derivada: el agente todavía no la resolvió.
    conversacion.chatealo_resuelta = False
    _cerrar(['label_equipo_actual', 'finalizado', 'chatealo_resuelta'])
    _log(conversacion, texto, opcion, 'DERIVACION', '; '.join(filter(None, [err_labels, err_msg])))
    return HttpResponse(status=200)
