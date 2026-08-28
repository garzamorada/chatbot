import calendar
import datetime as dt
import json
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from logs.models import UserLog

from . import chatealo_client
from .forms import (
    ArchivoChatbotForm, ConfiguracionChatbotForm, DespedidaChatbotForm,
    DiaExcepcionalRapidoForm, InboxChatealoForm, MenuOpcionForm, SaludosChatbotForm,
    TiemposChatbotForm,
)
from .horarios import (
    expandir_rango_a_horas, generar_slots_grilla, merge_horas_a_rangos, sincronizar_feriados,
)
from .labels import etiquetas_posibles
from .models import (
    ArchivoChatbot, ConfiguracionChatbot, Conversacion, DIA_SEMANA_CHOICES,
    DiaExcepcional, HorarioAtencion, InboxChatealo, MenuOpcion,
)


def _check(request, perm):
    if not request.user.has_perm(f'chatbot.{perm}'):
        raise PermissionDenied


def _ctx_embed(request):
    """Cuando ?embed=1 (la página se muestra dentro de un iframe en el panel)
    se usa el base sin header/footer del sitio y se ocultan los botones de
    navegación propios."""
    embebido = bool(request.GET.get('embed'))
    return {
        'embed': embebido,
        'base_template': 'base_iframe.html' if embebido else 'base.html',
    }


_DOCS_DIR = Path(__file__).resolve().parent / 'docs'


@login_required
def manual(request):
    """Sirve el manual de uso. `?formato=pdf` descarga el PDF; por defecto muestra
    el HTML (el archivo está pensado para el wrapper de Artifacts, así que acá se
    lo envuelve en un documento completo)."""
    _check(request, 'ver_panel_chatbot')
    if request.GET.get('formato') == 'pdf':
        pdf = _DOCS_DIR / 'manual_chatbot.pdf'
        if not pdf.exists():
            raise Http404
        return FileResponse(pdf.open('rb'), content_type='application/pdf',
                            filename='manual_chatbot.pdf')
    html = _DOCS_DIR / 'manual_chatbot.html'
    if not html.exists():
        raise Http404
    cuerpo = html.read_text(encoding='utf-8')
    return HttpResponse(
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'</head><body>{cuerpo}</body></html>'
    )


@login_required
def panel_chatbot(request):
    _check(request, 'ver_panel_chatbot')
    config = ConfiguracionChatbot.obtener()
    opciones_raiz = MenuOpcion.objects.filter(parent__isnull=True).order_by('orden', 'texto').prefetch_related('hijos')
    inboxes = InboxChatealo.objects.all()
    return render(request, 'chatbot/panel.html', {
        'config': config,
        'config_form': ConfiguracionChatbotForm(instance=config),
        'despedida_form': DespedidaChatbotForm(instance=config),
        'saludos_form': SaludosChatbotForm(instance=config),
        'tiempos_form': TiemposChatbotForm(instance=config),
        'opciones_raiz': opciones_raiz,
        'inboxes': [(ib, InboxChatealoForm(instance=ib, prefix=f'inbox{ib.pk}')) for ib in inboxes],
        'etiquetas': etiquetas_posibles(),
    })


@login_required
def editar_config(request):
    _check(request, 'gestionar_token_chatbot')
    config = ConfiguracionChatbot.obtener()
    if request.method == 'POST':
        form = ConfiguracionChatbotForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            UserLog.objects.create(usuario=request.user, accion='Editó la configuración de Chatbot')
            messages.success(request, 'Configuración guardada.')
        else:
            messages.error(request, 'No se pudo guardar la configuración.')
    return redirect(reverse('chatbot:panel') + '#tab-api')


@login_required
def editar_despedida(request):
    """Mensaje/archivo de despedida: es de la configuración general, único
    para todas las opciones tipo TERMINAR del árbol."""
    _check(request, 'gestionar_menu_chatbot')
    config = ConfiguracionChatbot.obtener()
    if request.method == 'POST':
        form = DespedidaChatbotForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            UserLog.objects.create(usuario=request.user, accion='Editó el mensaje de despedida de Chatbot')
            messages.success(request, 'Mensaje de despedida guardado.')
        else:
            messages.error(request, 'No se pudo guardar el mensaje de despedida.')
    return redirect(reverse('chatbot:panel') + '#tab-mensajes')


@login_required
def editar_saludos(request):
    """Plantillas de saludo: encabezado del menú principal y de cada área
    (esta última con {area} para el nombre del submenú)."""
    _check(request, 'gestionar_menu_chatbot')
    config = ConfiguracionChatbot.obtener()
    if request.method == 'POST':
        form = SaludosChatbotForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            UserLog.objects.create(usuario=request.user, accion='Editó los saludos de Chatbot')
            messages.success(request, 'Saludos guardados.')
        else:
            messages.error(request, 'No se pudieron guardar los saludos.')
    return redirect(reverse('chatbot:panel') + '#tab-mensajes')


@login_required
def editar_tiempos(request):
    """Tiempos: re-mostrar el menú tras una respuesta y cierre por inactividad.
    Los aplica el comando `procesar_temporizadores_chatbot` (cron)."""
    _check(request, 'gestionar_menu_chatbot')
    config = ConfiguracionChatbot.obtener()
    if request.method == 'POST':
        form = TiemposChatbotForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            UserLog.objects.create(usuario=request.user, accion='Editó los tiempos de Chatbot')
            messages.success(request, 'Tiempos guardados.')
        else:
            messages.error(request, 'No se pudieron guardar los tiempos.')
    return redirect(reverse('chatbot:panel') + '#tab-mensajes')


@login_required
def regenerar_webhook_secret(request):
    _check(request, 'gestionar_token_chatbot')
    if request.method != 'POST':
        return redirect('chatbot:panel')
    config = ConfiguracionChatbot.obtener()
    config.regenerar_webhook_secret()
    UserLog.objects.create(usuario=request.user, accion='Regeneró el secreto del webhook de Chatbot')
    messages.success(request, 'Secreto regenerado. Actualizá la URL del webhook en chatealo.')
    return redirect(reverse('chatbot:panel') + '#tab-api')


# 'message_created' guía el árbol de menú; los 'conversation_*' dan de alta /
# actualizan la fila local de la conversación (estado, contacto, inbox) para
# poder interactuar con ella después; 'message_updated' solo se usa para
# registrar cuando un envío saliente falla (status=failed).
SUBSCRIPCIONES_WEBHOOK = [
    'message_created', 'message_updated',
    'conversation_created', 'conversation_status_changed',
]


def _url_webhook_actual(request, config):
    return request.build_absolute_uri(
        reverse('chatbot:webhook_chatealo', kwargs={'secret': config.webhook_secret})
    )


@login_required
@require_POST
def webhooks_listar(request):
    """Trae los webhooks registrados en la cuenta de chatealo (API de chatealo)."""
    _check(request, 'gestionar_token_chatbot')
    config = ConfiguracionChatbot.obtener()
    try:
        webhooks = chatealo_client.listar_webhooks(config)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=200)
    return JsonResponse({
        'ok': True,
        'webhooks': webhooks,
        'url_actual': _url_webhook_actual(request, config),
        'subscripciones': SUBSCRIPCIONES_WEBHOOK,
    })


@login_required
@require_POST
def webhooks_registrar(request):
    """Registra la URL actual del webhook en la cuenta de chatealo."""
    _check(request, 'gestionar_token_chatbot')
    config = ConfiguracionChatbot.obtener()
    url_actual = _url_webhook_actual(request, config)
    try:
        existentes = chatealo_client.listar_webhooks(config)
        if any((w.get('url') or '').rstrip('/') == url_actual.rstrip('/') for w in existentes):
            return JsonResponse({'ok': False, 'error': 'Ese webhook ya está registrado en chatealo.'}, status=200)
        creado = chatealo_client.crear_webhook(config, url_actual, SUBSCRIPCIONES_WEBHOOK)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=200)
    UserLog.objects.create(
        usuario=request.user,
        accion=f'Registró el webhook de Chatbot en chatealo ({url_actual})',
    )
    return JsonResponse({'ok': True, 'webhook': creado})


@login_required
@require_POST
def editar_inbox(request, pk):
    """Asocia nombre / tipo de fuente / responder_bot a una bandeja de chatealo
    (las bandejas se dan de alta solas cuando llegan eventos al webhook)."""
    _check(request, 'gestionar_inboxes_chatbot')
    inbox = get_object_or_404(InboxChatealo, pk=pk)
    form = InboxChatealoForm(request.POST, instance=inbox, prefix=f'inbox{inbox.pk}')
    if form.is_valid():
        form.save()
        UserLog.objects.create(
            usuario=request.user,
            accion=f'Editó la bandeja de Chatbot "{inbox}" (inbox {inbox.inbox_id})',
        )
        messages.success(request, f'Bandeja #{inbox.inbox_id} guardada.')
    else:
        messages.error(request, 'No se pudo guardar la bandeja.')
    return redirect(reverse('chatbot:panel') + '#tab-api')


@login_required
def gestionar_opcion(request, pk=None):
    """Sirve el formulario de alta/edición como fragmento HTML (para el modal
    del árbol) y responde JSON al guardar."""
    _check(request, 'gestionar_menu_chatbot')
    opcion = get_object_or_404(MenuOpcion, pk=pk) if pk else None
    creando = opcion is None

    if request.method == 'POST':
        form = MenuOpcionForm(request.POST, instance=opcion)
        if form.is_valid():
            obj = form.save(commit=False)
            if creando:
                obj.creado_por = request.user
                # Se arrastra para ordenar, no se tipea: nuevas opciones van
                # al final de la lista de su padre (o de la raíz).
                ultimo = MenuOpcion.objects.filter(parent=obj.parent).aggregate(models.Max('orden'))
                obj.orden = (ultimo['orden__max'] or 0) + 1
            obj.save()
            UserLog.objects.create(
                usuario=request.user,
                accion=f'{"Creó" if creando else "Editó"} la opción de menú Chatbot #{obj.pk} ({obj.texto})',
            )
            return JsonResponse({'ok': True, 'id': obj.pk, 'parent_id': obj.parent_id})
        return render(request, 'chatbot/_form_opcion.html', {
            'form': form, 'creando': creando, 'opcion': opcion,
        }, status=400)

    initial = {}
    if creando:
        if request.GET.get('tipo'):
            initial['tipo'] = request.GET['tipo']
        if request.GET.get('parent'):
            initial['parent'] = request.GET['parent']
    form = MenuOpcionForm(instance=opcion, initial=initial)
    return render(request, 'chatbot/_form_opcion.html', {
        'form': form, 'creando': creando, 'opcion': opcion,
    })


@login_required
@require_POST
def reordenar_opciones(request):
    """Endpoint AJAX usado por el árbol de arrastrar-y-soltar: reordena y/o
    re-parenta un conjunto de opciones dentro de un mismo padre."""
    _check(request, 'gestionar_menu_chatbot')
    try:
        data = json.loads(request.body or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    parent_id = data.get('parent_id')
    orden = data.get('orden') or []

    parent = None
    if parent_id is not None:
        parent = get_object_or_404(MenuOpcion, pk=parent_id)
        if parent.tipo != 'SUBMENU':
            return JsonResponse({'error': 'El destino no es un menú.'}, status=400)
        ancestro = parent
        while ancestro is not None:
            if str(ancestro.pk) in [str(i) for i in orden]:
                return JsonResponse({'error': 'Ese movimiento crearía un ciclo.'}, status=400)
            ancestro = ancestro.parent

    validos = set(MenuOpcion.objects.filter(pk__in=orden).values_list('pk', flat=True))
    with transaction.atomic():
        for i, oid in enumerate(orden):
            try:
                oid = int(oid)
            except (TypeError, ValueError):
                continue
            if oid in validos:
                MenuOpcion.objects.filter(pk=oid).update(parent_id=parent_id, orden=i)

    UserLog.objects.create(
        usuario=request.user,
        accion=f'Reordenó opciones de menú Chatbot (padre={parent_id or "raíz"})',
    )
    return JsonResponse({'ok': True})


@login_required
def eliminar_opcion(request, pk):
    """Confirmación de borrado también como fragmento para el modal."""
    _check(request, 'gestionar_menu_chatbot')
    opcion = get_object_or_404(MenuOpcion, pk=pk)
    if request.method == 'POST':
        texto = opcion.texto
        opcion.delete()
        UserLog.objects.create(
            usuario=request.user,
            accion=f'Eliminó la opción de menú Chatbot "{texto}" (y las opciones que contenía)',
        )
        return JsonResponse({'ok': True})
    return render(request, 'chatbot/_confirmar_eliminar_opcion.html', {'opcion': opcion})


@login_required
def lista_archivos(request):
    _check(request, 'ver_panel_chatbot')
    archivos = ArchivoChatbot.objects.all()
    form = ArchivoChatbotForm()
    return render(request, 'chatbot/lista_archivos.html', {'archivos': archivos, 'form': form})


@login_required
def subir_archivo(request):
    _check(request, 'gestionar_archivos_chatbot')
    if request.method == 'POST':
        form = ArchivoChatbotForm(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.subido_por = request.user
            obj.save()
            UserLog.objects.create(
                usuario=request.user,
                accion=f'Subió el archivo Chatbot "{obj.nombre}"',
            )
            messages.success(request, 'Archivo subido correctamente.')
            return redirect('chatbot:lista_archivos')
        messages.error(request, 'No se pudo subir el archivo. Revisá los datos.')
    return redirect('chatbot:lista_archivos')


@login_required
def eliminar_archivo(request, pk):
    _check(request, 'gestionar_archivos_chatbot')
    archivo = get_object_or_404(ArchivoChatbot, pk=pk)
    if request.method == 'POST':
        nombre = archivo.nombre
        archivo.delete()
        UserLog.objects.create(
            usuario=request.user,
            accion=f'Eliminó el archivo Chatbot "{nombre}"',
        )
        messages.success(request, 'Archivo eliminado.')
        return redirect('chatbot:lista_archivos')
    return render(request, 'chatbot/confirmar_eliminar_archivo.html', {'archivo': archivo})


@login_required
def lista_conversaciones(request):
    _check(request, 'ver_panel_chatbot')
    conversaciones = Conversacion.objects.select_related('menu_actual').all()[:200]
    return render(request, 'chatbot/lista_conversaciones.html', {'conversaciones': conversaciones})


@login_required
def detalle_conversacion(request, pk):
    _check(request, 'ver_panel_chatbot')
    conversacion = get_object_or_404(Conversacion, pk=pk)
    logs = conversacion.logs.select_related('opcion').all()
    return render(request, 'chatbot/detalle_conversacion.html', {
        'conversacion': conversacion, 'logs': logs,
    })


@login_required
@xframe_options_sameorigin
def lista_horarios(request):
    _check(request, 'ver_panel_chatbot')
    celdas_iniciales = []
    for h in HorarioAtencion.objects.filter(activo=True):
        for hora in expandir_rango_a_horas(h.hora_inicio, h.hora_fin):
            celdas_iniciales.append(f'{h.dia_semana}-{hora.strftime("%H:%M")}')
    return render(request, 'chatbot/lista_horarios.html', {
        'celdas_iniciales': celdas_iniciales,
        'slots': [s.strftime('%H:%M') for s in generar_slots_grilla()],
        'dias': DIA_SEMANA_CHOICES,
        **_ctx_embed(request),
    })


@login_required
@require_POST
def guardar_horarios_grilla(request):
    """Guarda la grilla semanal completa: reemplaza todos los HorarioAtencion
    por los rangos que resultan de fusionar las celdas pintadas."""
    _check(request, 'gestionar_horarios_chatbot')
    try:
        data = json.loads(request.body or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    por_dia = {}
    for celda in data.get('celdas') or []:
        try:
            dia_str, hora_str = celda.split('-', 1)
            dia = int(dia_str)
            hora = dt.datetime.strptime(hora_str, '%H:%M').time()
        except (ValueError, IndexError):
            continue
        por_dia.setdefault(dia, []).append(hora)

    with transaction.atomic():
        HorarioAtencion.objects.all().delete()
        for dia, horas in por_dia.items():
            for inicio, fin in merge_horas_a_rangos(horas):
                HorarioAtencion.objects.create(dia_semana=dia, hora_inicio=inicio, hora_fin=fin, activo=True)

    UserLog.objects.create(usuario=request.user, accion='Actualizó la grilla de horarios de atención de Chatbot')
    return JsonResponse({'ok': True})


RANGO_DIAS_ESPECIFICOS_MAX = 14


@login_required
@xframe_options_sameorigin
def calendario_excepciones(request):
    _check(request, 'ver_panel_chatbot')
    hoy = timezone.localdate()
    try:
        anio = int(request.GET.get('anio', hoy.year))
        mes = int(request.GET.get('mes', hoy.month))
        dt.date(anio, mes, 1)
    except (ValueError, TypeError):
        anio, mes = hoy.year, hoy.month

    if not sincronizar_feriados(anio):
        messages.warning(request, 'No se pudo consultar la API de feriados nacionales en este momento.')

    semanas = calendar.Calendar(firstweekday=0).monthdatescalendar(anio, mes)
    excepciones = {
        e.fecha: e for e in DiaExcepcional.objects.filter(fecha__year=anio, fecha__month=mes)
    }
    mes_anterior = (dt.date(anio, mes, 1) - dt.timedelta(days=1))
    mes_siguiente = (dt.date(anio, mes, 28) + dt.timedelta(days=7)).replace(day=1)
    proximas = DiaExcepcional.objects.filter(fecha__gte=hoy)

    # Editor de rango (un día o una semana): ?desde=YYYY-MM-DD&hasta=YYYY-MM-DD
    dias_editor = None
    desde_str, hasta_str = request.GET.get('desde'), request.GET.get('hasta')
    if desde_str:
        try:
            desde = dt.date.fromisoformat(desde_str)
            hasta = dt.date.fromisoformat(hasta_str) if hasta_str else desde
        except ValueError:
            desde = hasta = None
        if desde and hasta and desde <= hasta and (hasta - desde).days < RANGO_DIAS_ESPECIFICOS_MAX:
            existentes = {
                e.fecha: e for e in DiaExcepcional.objects.filter(fecha__range=(desde, hasta))
            }
            dias_editor = []
            n = (hasta - desde).days + 1
            for i in range(n):
                fecha = desde + dt.timedelta(days=i)
                exc = existentes.get(fecha)
                celdas = []
                if exc and exc.tipo == 'HORARIO_REDUCIDO' and exc.hora_inicio and exc.hora_fin:
                    celdas = [h.strftime('%H:%M') for h in expandir_rango_a_horas(exc.hora_inicio, exc.hora_fin)]
                dias_editor.append({
                    'fecha': fecha,
                    'tipo': exc.tipo if exc else 'NORMAL',
                    'celdas': celdas,
                })

    feriado_inicial = {'fecha': hoy}
    editar_str = request.GET.get('editar')
    if editar_str:
        try:
            existente = DiaExcepcional.objects.filter(
                fecha=dt.date.fromisoformat(editar_str), tipo__in=['FERIADO', 'ASUETO'],
            ).first()
            if existente:
                feriado_inicial = {
                    'fecha': existente.fecha, 'tipo': existente.tipo, 'descripcion': existente.descripcion,
                }
        except ValueError:
            pass

    return render(request, 'chatbot/calendario_excepciones.html', {
        'semanas': semanas,
        'excepciones': excepciones,
        'mes_actual': dt.date(anio, mes, 1),
        'mes_anterior': mes_anterior,
        'mes_siguiente': mes_siguiente,
        'hoy': hoy,
        'proximas': proximas,
        'dias_editor': dias_editor,
        'slots': [s.strftime('%H:%M') for s in generar_slots_grilla()],
        'desde': desde_str or hoy.isoformat(),
        'hasta': hasta_str or hoy.isoformat(),
        'feriado_form': DiaExcepcionalRapidoForm(initial=feriado_inicial),
        **_ctx_embed(request),
    })


@login_required
def agregar_feriado_rapido(request):
    """Alta rápida de un feriado/asueto puntual (con descripción), sin pasar
    por el editor de rango/grilla de horas — es un flujo aparte y simple,
    no comparte la lógica de fusión de celdas de `guardar_dias_especificos`."""
    _check(request, 'gestionar_horarios_chatbot')
    if request.method != 'POST':
        return redirect('chatbot:calendario_excepciones')

    # `fecha` es unique=True: si ya existe un DiaExcepcional para esa fecha,
    # hay que pasarlo como instance= para que la validación de unicidad no
    # rechace el form contra sí mismo (si no, "guardar" en una fecha ya
    # cargada fallaba en silencio y el registro viejo quedaba intacto).
    instancia_existente = None
    fecha_posteada = request.POST.get('fecha')
    if fecha_posteada:
        instancia_existente = DiaExcepcional.objects.filter(fecha=fecha_posteada).first()
    form = DiaExcepcionalRapidoForm(request.POST, instance=instancia_existente)
    anio = request.POST.get('anio') or timezone.localdate().year
    mes = request.POST.get('mes') or timezone.localdate().month
    if form.is_valid():
        obj = form.save(commit=False)
        obj.hora_inicio = None
        obj.hora_fin = None
        obj.sincronizado = False
        obj.save()
        UserLog.objects.create(
            usuario=request.user,
            accion=f'{"Editó" if instancia_existente else "Agregó"} el día excepcional {obj.fecha} ({obj.tipo}) en Chatbot',
        )
        messages.success(request, 'Día excepcional guardado.')
    else:
        messages.error(request, 'No se pudo guardar. Revisá la fecha.')
    return redirect(f"{reverse('chatbot:calendario_excepciones')}?anio={anio}&mes={mes}")


@login_required
@require_POST
def guardar_dias_especificos(request):
    """Guarda el editor de rango de días excepcionales: por cada fecha del
    rango, aplica NORMAL (borra la excepción), FERIADO/ASUETO (cierra todo el
    día) u HORARIO_REDUCIDO (fusiona las celdas pintadas en un único rango)."""
    _check(request, 'gestionar_horarios_chatbot')
    try:
        data = json.loads(request.body or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    dias = data.get('dias') or {}
    with transaction.atomic():
        for fecha_str, info in dias.items():
            try:
                fecha = dt.date.fromisoformat(fecha_str)
            except ValueError:
                continue
            tipo = info.get('tipo')

            if tipo not in ('FERIADO', 'ASUETO', 'HORARIO_REDUCIDO'):
                DiaExcepcional.objects.filter(fecha=fecha).delete()
                continue

            if tipo == 'HORARIO_REDUCIDO':
                horas = []
                for celda in info.get('celdas') or []:
                    try:
                        horas.append(dt.datetime.strptime(celda, '%H:%M').time())
                    except ValueError:
                        continue
                rangos = merge_horas_a_rangos(horas)
                if not rangos:
                    DiaExcepcional.objects.filter(fecha=fecha).delete()
                    continue
                hora_inicio, hora_fin = rangos[0][0], rangos[-1][1]
                DiaExcepcional.objects.update_or_create(
                    fecha=fecha,
                    defaults={
                        'tipo': 'HORARIO_REDUCIDO', 'hora_inicio': hora_inicio, 'hora_fin': hora_fin,
                        'sincronizado': False,
                    },
                )
            else:
                DiaExcepcional.objects.update_or_create(
                    fecha=fecha,
                    defaults={'tipo': tipo, 'hora_inicio': None, 'hora_fin': None, 'sincronizado': False},
                )

    UserLog.objects.create(usuario=request.user, accion='Editó días excepcionales de Chatbot (editor de rango)')
    return JsonResponse({'ok': True})


@login_required
def eliminar_dia_excepcional(request, pk):
    _check(request, 'gestionar_horarios_chatbot')
    dia = get_object_or_404(DiaExcepcional, pk=pk)
    if request.method == 'POST':
        fecha = dia.fecha
        dia.delete()
        UserLog.objects.create(usuario=request.user, accion=f'Eliminó el día excepcional {fecha} de Chatbot')
        messages.success(request, 'Día excepcional eliminado.')
    return redirect('chatbot:calendario_excepciones')
