LABEL_MENU_RAIZ = 'menu-raiz'
# Slug de equipo cuando se pide derivación desde el menú principal (todavía sin
# categoría elegida): la etiqueta queda 'equipo-general'.
SLUG_EQUIPO_GENERAL = 'general'


def aplicar_transicion_labels(labels_actuales, label_anterior, label_nuevo):
    """Devuelve el set completo de etiquetas a mandar a chatealo: si había una
    `label_anterior` (distinta de la nueva), se reemplaza por 'hist-<label_anterior>'
    y se agrega `label_nuevo`. Si no cambió, solo se asegura que esté presente."""
    labels = set(labels_actuales)
    if label_anterior and label_anterior != label_nuevo:
        labels.discard(label_anterior)
        labels.add(f'hist-{label_anterior}')
    labels.add(label_nuevo)
    return sorted(labels)


def etiquetas_posibles():
    """Etiquetas que el bot maneja según cómo está armado el árbol de menú,
    para mostrarlas en el panel de configuración.

    - `menu-<slug>`  : navegación (más `menu-raiz` en la raíz). SÓLO log interno,
      NO se aplican en chatealo.
    - `equipo-<slug>`: al pedir "Hablar con un operador", con el slug del menú
      donde estaba parado el usuario (o `equipo-general` si fue desde la raíz).
      Esta SÍ se aplica en chatealo; la anterior queda archivada `hist-equipo-<slug>`.
    """
    from .models import ConfiguracionChatbot, MenuOpcion

    config = ConfiguracionChatbot.obtener()
    opciones = list(MenuOpcion.objects.select_related('parent').order_by('orden', 'texto'))

    menu = [{'label': LABEL_MENU_RAIZ, 'origen': 'Menú principal', 'activo': True}]
    for o in opciones:
        if o.tipo == 'SUBMENU':
            menu.append({'label': o.label_menu, 'origen': o.texto, 'activo': o.activo})

    # El bot agrega solo un "Hablar con un operador" al pie de cada menú que la
    # ofrezca (config general para la raíz; MenuOpcion.derivacion_ofrecer por
    # submenú), que deriva con 'equipo-<slug del menú>' — o 'equipo-general'
    # desde el menú principal.
    equipo = {}
    if config.derivacion_ofrecer:
        equipo[f'equipo-{SLUG_EQUIPO_GENERAL}'] = {
            'label': f'equipo-{SLUG_EQUIPO_GENERAL}', 'origenes': ['Menú principal'], 'activo': True,
        }
    for o in opciones:
        if o.tipo == 'SUBMENU' and o.activo and o.derivacion_ofrecer:
            label = f'equipo-{o.slug}'
            entrada = equipo.setdefault(label, {'label': label, 'origenes': [], 'activo': True})
            if o.texto not in entrada['origenes']:
                entrada['origenes'].append(o.texto)

    return {'menu': menu, 'equipo': sorted(equipo.values(), key=lambda e: e['label'])}
