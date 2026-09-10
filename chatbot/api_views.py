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
        if o.slug and o.slug == texto_norm.lower():
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


# --------------------------------------------------------------------------- #
#  Opciones de navegación fijas (no viven en la BD)                            #
#  Se agregan al final de TODO menú: "Volver al menú principal" (salvo en el   #
#  menú principal), "Hablar con un operador" (sólo si es día/horario hábil) y  #
#  "Terminar la conversación" (siempre).                                       #
# --------------------------------------------------------------------------- #
NAV_INICIO_TEXTO = 'Volver al menú principal'
NAV_TERMINAR_TEXTO = 'Terminar la conversación'


class _OpcionNav:
    """Opción de navegación agregada automáticamente al pie de un menú.
    Imita la interfaz mínima de `MenuOpcion` que usa el webhook."""
    pk = None
    archivo_id = None
    respuesta_texto = ''
    slug = ''
    activo = True
    tiene_boton = False

    def __init__(self, tipo, texto, mensaje_derivacion=''):
        self.tipo = tipo
        self.texto = texto
        self.mensaje_derivacion = mensaje_derivacion

    def __str__(self):
        return self.texto


def _derivacion_config(config, opcion_menu):
    """(ofrecer, texto, mensaje) de la opción "Hablar con un operador" para este
    menú. Cada menú puede pisar los valores generales de ConfiguracionChatbot;
    el menú principal (opcion_menu=None) usa directo los generales."""
    grl_texto = (config.derivacion_texto or '').strip() or defaults.DERIVACION_TEXTO
    grl_mensaje = (config.derivacion_mensaje or '').strip() or defaults.DERIVACION_MENSAJE
    if opcion_menu is None:
        return config.derivacion_ofrecer, grl_texto, grl_mensaje
    texto = (opcion_menu.derivacion_texto or '').strip() or grl_texto
    mensaje = (opcion_menu.mensaje_derivacion or '').strip() or grl_mensaje
    return opcion_menu.derivacion_ofrecer, texto[:24], mensaje


def _opciones_nav(config, opcion_menu, en_horario):
    navs = []
    if opcion_menu is not None:
        navs.append(_OpcionNav('INICIO', NAV_INICIO_TEXTO))
    ofrecer, texto_op, mensaje_op = _derivacion_config(config, opcion_menu)
    if en_horario and ofrecer:
        navs.append(_OpcionNav('DERIVACION', texto_op, mensaje_derivacion=mensaje_op))
    navs.append(_OpcionNav('TERMINAR', NAV_TERMINAR_TEXTO))
    return navs


def _opciones_visibles(config, opcion_menu, en_horario):
    """Opciones reales del menú (de la BD) + las de navegación fijas."""
    return _opciones_de(opcion_menu) + _opciones_nav(config, opcion_menu, en_horario)


def _es_opcion_db(opcion):
    return isinstance(opcion, MenuOpcion)


def _items_menu(opciones):
    """Ítems para `content_type: input_select` (WhatsApp: 1-3 => botones,
    4-10 => lista). Devuelve None si la cantidad se va de ese rango; en ese
    caso el mensaje sale como texto plano numerado (que también funciona)."""
    if not 1 <= len(opciones) <= 10:
        return None
    tope = 20 if len(opciones) <= 3 else 24
    return [
        {'title': f'{i} - {_limpiar_texto_opcion(o.texto)}'[:tope], 'value': str(i)}
        for i, o in enumerate(opciones, start=1)
    ]


def _render_menu(config, opcion_menu, opciones, nombre='', mensaje_extra=''):
    """(texto, items) de un menú: el texto numerado (con `mensaje_extra` al
    frente si lo hay) y los ítems para `input_select`."""
    texto = _menu_con_encabezado(config, opcion_menu, opciones, nombre)
    if mensaje_extra:
        texto = f'{mensaje_extra}\n\n{texto}'
    return texto, _items_menu(opciones)


def _enviar_menu(config, conversation_id, opcion_menu, opciones, nombre='', mensaje_extra=''):
    """Manda un menú como lista/botones interactivos (con el texto numerado de
    fallback en `content`)."""
    texto, items = _render_menu(config, opcion_menu, opciones, nombre, mensaje_extra)
    return _enviar_seguro(config, conversation_id, texto, items=items)


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


def _registrar_ignorado_por_pausa(payload):
    """El bot está pausado: sólo deja constancia de que un contacto escribió y no
    se le respondió. No manda nada ni toca etiquetas en chatealo."""
    if payload.get('event') != 'message_created' or payload.get('message_type') != 'incoming':
        return
    conv_data = payload.get('conversation') or {}
    conv_id = conv_data.get('id')
    if not conv_id:
        return
    try:
        sender = payload.get('sender') or {}
        conversacion, _ = Conversacion.objects.get_or_create(
            conversation_id=conv_id,
            defaults={
                'contacto': sender.get('phone_number') or sender.get('identifier') or '',
                'inbox_id': payload.get('inbox_id') or conv_data.get('inbox_id'),
                'nombre_contacto': sender.get('name') or '',
            },
        )
        _log(conversacion, payload.get('content') or '', None, 'PAUSADO',
             'el bot estaba pausado desde el panel')
    except Exception:
        logger.exception('Error registrando mensaje recibido con el bot pausado')


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


def _enviar_seguro(config, conversation_id, texto, items=None):
    try:
        chatealo_client.enviar_mensaje(config, conversation_id, texto, items=items)
        return ''
    except Exception as exc:
        logger.exception('Error enviando mensaje a conversación %s', conversation_id)
        return f'Error enviando mensaje: {exc}'


# Botón de acción de WhatsApp en las respuestas: código listo pero DESACTIVADO.
# chatealo devuelve 422 en TODO mensaje saliente ("undefined method 'members'
# for nil"), así que no tiene sentido habilitarlo hasta resolver eso. Para
# reactivar: poner True acá y devolver 'boton_texto'/'boton_url' a
# CAMPOS_POR_TIPO.RESPUESTA en chatbot_menu.js.
BOTON_RESPUESTA_HABILITADO = False


def _enviar_respuesta_seguro(config, conversation_id, texto, opcion):
    """Envía el texto de una RESPUESTA. Si la opción tiene botón (texto + link)
    lo manda como acción; si eso falla, reintenta como texto plano con el link
    al pie para que siempre quede accesible."""
    if not BOTON_RESPUESTA_HABILITADO or not opcion.tiene_boton:
        return _enviar_seguro(config, conversation_id, texto)
    boton = {'texto': opcion.boton_texto.strip(), 'url': opcion.boton_url.strip()}
    try:
        chatealo_client.enviar_mensaje(config, conversation_id, texto, boton=boton)
        return ''
    except Exception as exc:
        logger.warning('Botón de acción falló en conversación %s (%s); reintento como texto',
                       conversation_id, exc)
        return _enviar_seguro(
            config, conversation_id, f'{texto}\n\n👉 {boton["texto"]}: {boton["url"]}',
        )


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

    opciones = _opciones_visibles(config, None, esta_en_horario())
    err_msg = _enviar_menu(config, conversation_id, None, opciones, conversacion.nombre_contacto)
    return err_labels, err_msg


def _navegar_a_menu(config, conversation_id, conversacion, nuevo_menu, en_horario, mensaje_extra=''):
    """Cambia conversacion.menu_actual y manda el listado de opciones del nuevo
    menú (como lista/botones interactivos). Usado por SUBMENU e INICIO — solo
    cambia cómo se calcula `nuevo_menu`. Las etiquetas `menu-*` NO se aplican en
    chatealo: sólo se registran en el log interno (`nota_menu`)."""
    opciones = _opciones_visibles(config, nuevo_menu, en_horario)
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

    err_msg = _enviar_menu(
        config, conversation_id, nuevo_menu, opciones,
        conversacion.nombre_contacto, mensaje_extra,
    )

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

    # Interruptor general: si el bot está pausado desde el panel, no responde nada.
    if not config.activo:
        _registrar_ignorado_por_pausa(payload)
        return HttpResponse(status=200)

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

    # Se aceptan los mensajes de texto y las respuestas a listas/botones
    # interactivos (que chatealo puede etiquetar 'input_select'). El resto
    # (adjuntos, formularios, etc.) se ignora.
    if payload.get('content_type') not in ('text', 'input_select', None, ''):
        _log(conversacion, texto, None, 'IGNORADO', 'content_type no accionable')
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

    en_horario = esta_en_horario()
    opciones_actuales = _opciones_visibles(config, conversacion.menu_actual, en_horario)
    opcion = _match_opcion(texto, opciones_actuales)
    opcion_db = opcion if _es_opcion_db(opcion) else None

    if opcion is None:
        # "No entendí esa opción" SÓLO cuando la persona está dentro de un
        # submenú y eligió algo inválido. En el menú principal nunca: se le
        # muestra el menú (con bienvenida la primera vez), sin importar qué
        # haya escrito.
        nombre = conversacion.nombre_contacto
        partes = []
        if not en_horario:
            partes.append(mensaje_fuera_de_horario())
        if conversacion.menu_actual is not None:
            partes.append('No entendí esa opción.')
            accion = 'INVALIDA'
        elif not conversacion.menu_mostrado:
            partes.append(aplicar_variables(
                (config.mensaje_bienvenida or '').strip() or defaults.MENSAJE_BIENVENIDA, nombre=nombre,
            ))
            accion = 'BIENVENIDA'
        else:
            accion = 'MENU'
        err = _enviar_menu(
            config, conversation_id, conversacion.menu_actual, opciones_actuales,
            nombre, '\n\n'.join(partes),
        )
        conversacion.menu_mostrado = True
        _log(conversacion, texto, None, accion, err)
        _cerrar()
        return HttpResponse(status=200)

    if opcion.tipo == 'SUBMENU':
        nota_menu, err_msg = _navegar_a_menu(config, conversation_id, conversacion, opcion, en_horario)
        _cerrar(['menu_actual'])
        _log(conversacion, texto, opcion_db, 'MENU', '; '.join(filter(None, [nota_menu, err_msg])))
        return HttpResponse(status=200)

    if opcion.tipo == 'INICIO':
        nota_menu, err_msg = _navegar_a_menu(config, conversation_id, conversacion, None, en_horario)
        _cerrar(['menu_actual'])
        _log(conversacion, texto, opcion_db, 'MENU', '; '.join(filter(None, [nota_menu, err_msg])))
        return HttpResponse(status=200)

    if opcion.tipo == 'RESPUESTA':
        respuesta = opcion.respuesta_texto
        url_archivo = _url_archivo(request, opcion)
        if url_archivo:
            respuesta = f'{respuesta}\n{url_archivo}'
        err_msg = _enviar_respuesta_seguro(config, conversation_id, respuesta, opcion)

        # agenda volver a mostrar el menú donde estaba, pasados N segundos.
        if config.segundos_remostrar_menu:
            conversacion.remostrar_menu_en = timezone.now() + timedelta(seconds=config.segundos_remostrar_menu)
        _cerrar()
        _log(conversacion, texto, opcion_db, 'RESPUESTA', err_msg)
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
        _log(conversacion, texto, opcion_db, 'TERMINAR', '; '.join(filter(None, [err_msg, err_resolver])))
        return HttpResponse(status=200)

    # DERIVACION
    if not en_horario:
        opciones_fh = _opciones_visibles(config, conversacion.menu_actual, False)
        err_msg = _enviar_menu(
            config, conversation_id, conversacion.menu_actual, opciones_fh,
            conversacion.nombre_contacto,
            mensaje_fuera_de_horario() + '\n\nMientras tanto, elegí una opción:',
        )
        conversacion.menu_mostrado = True
        _cerrar()
        _log(conversacion, texto, opcion_db, 'FUERA_HORARIO', err_msg)
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

    respuesta = aplicar_variables(
        opcion.mensaje_derivacion or defaults.DERIVACION_MENSAJE,
        area=_nombre_area(conversacion.menu_actual), nombre=conversacion.nombre_contacto,
    )
    url_archivo = _url_archivo(request, opcion)
    if url_archivo:
        respuesta = f'{respuesta}\n{url_archivo}'
    err_msg = _enviar_seguro(config, conversation_id, respuesta)

    conversacion.label_equipo_actual = label_nueva_equipo
    conversacion.finalizado = True
    # recién derivada: el agente todavía no la resolvió.
    conversacion.chatealo_resuelta = False
    _cerrar(['label_equipo_actual', 'finalizado', 'chatealo_resuelta'])
    _log(conversacion, texto, opcion_db, 'DERIVACION', '; '.join(filter(None, [err_labels, err_msg])))
    return HttpResponse(status=200)
