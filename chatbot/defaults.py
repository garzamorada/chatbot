"""Textos por defecto de los mensajes del bot y variables disponibles por campo.

Se centralizan acá para que el runtime (`api_views`, el comando de
temporizadores) y el panel (forms) usen exactamente los mismos valores: los
campos vacíos en `ConfiguracionChatbot` se muestran con estos textos y el bot
los usa cuando el campo quedó en blanco.
"""

MENSAJE_BIENVENIDA = (
    '¡Hola! Te está respondiendo el asistente virtual. '
    'Te ayudo con las consultas más frecuentes a través de este menú.'
)
SALUDO_INICIAL = 'Usted está en el menú principal, por favor seleccione la opción deseada.'
SALUDO_AREA = 'Usted está en el área de "{area}", por favor seleccione la opción deseada.'
MENSAJE_DESPEDIDA = 'Gracias por contactarnos. ¡Hasta luego!'
# "Hablar con un operador" — la opción de derivación que el bot agrega al pie
# de cada menú. Texto de la opción y mensaje que se le manda al contacto al
# derivarlo. Ambos se pueden pisar por menú (ver ConfiguracionChatbot y
# MenuOpcion).
DERIVACION_TEXTO = 'Hablar con un operador'
DERIVACION_MENSAJE = 'Te derivamos con un operador. En breve te van a contactar. 🕑'
MENSAJE_CIERRE_INACTIVIDAD = (
    'Cerramos esta conversación por inactividad. Si necesitás algo más, '
    'volvé a escribirnos y con gusto te ayudamos. ¡Hasta pronto!'
)

# Variables que se pueden usar dentro de cada plantilla (para los "tags" del panel).
#   {area}   -> nombre del menú donde está el contacto (sólo saludo de menú)
#   {nombre} -> nombre del contacto en chatealo (vacío si no lo tenemos)
VARIABLES_POR_CAMPO = {
    'mensaje_bienvenida': ['nombre'],
    'plantilla_saludo_inicial': ['nombre'],
    'plantilla_saludo_area': ['area', 'nombre'],
    'mensaje_despedida': ['nombre'],
    'mensaje_cierre_inactividad': ['nombre'],
    'derivacion_mensaje': ['area', 'nombre'],
    'mensaje_derivacion': ['area', 'nombre'],
}

DEFAULT_POR_CAMPO = {
    'mensaje_bienvenida': MENSAJE_BIENVENIDA,
    'plantilla_saludo_inicial': SALUDO_INICIAL,
    'plantilla_saludo_area': SALUDO_AREA,
    'mensaje_despedida': MENSAJE_DESPEDIDA,
    'mensaje_cierre_inactividad': MENSAJE_CIERRE_INACTIVIDAD,
    'derivacion_texto': DERIVACION_TEXTO,
    'derivacion_mensaje': DERIVACION_MENSAJE,
}


def aplicar_variables(texto, area='', nombre=''):
    return (texto or '').replace('{area}', area or '').replace('{nombre}', nombre or '')
