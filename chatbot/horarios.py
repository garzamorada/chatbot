import datetime as dt
import logging

from django.utils import timezone

from . import feriados_client
from .models import DiaExcepcional, HorarioAtencion

logger = logging.getLogger('chatbot.feriados')

HORIZONTE_DIAS = 60
GRANULARIDAD_MIN = 30
HORA_GRILLA_INICIO = dt.time(7, 0)
HORA_GRILLA_FIN = dt.time(22, 0)


def generar_slots_grilla():
    """Horas (datetime.time) de la grilla visual, cada GRANULARIDAD_MIN minutos."""
    slots = []
    actual = dt.datetime.combine(dt.date.min, HORA_GRILLA_INICIO)
    fin = dt.datetime.combine(dt.date.min, HORA_GRILLA_FIN)
    while actual < fin:
        slots.append(actual.time())
        actual += dt.timedelta(minutes=GRANULARIDAD_MIN)
    return slots


def expandir_rango_a_horas(hora_inicio, hora_fin):
    """Horas de inicio de cada slot de GRANULARIDAD_MIN cubiertas por [hora_inicio, hora_fin)."""
    horas = []
    actual = dt.datetime.combine(dt.date.min, hora_inicio)
    fin = dt.datetime.combine(dt.date.min, hora_fin)
    while actual < fin:
        horas.append(actual.time())
        actual += dt.timedelta(minutes=GRANULARIDAD_MIN)
    return horas


def merge_horas_a_rangos(horas):
    """A partir de horas de inicio de slot, arma rangos [(inicio, fin), ...] contiguos."""
    horas = sorted(set(horas))
    if not horas:
        return []
    rangos = []
    inicio = horas[0]
    anterior = horas[0]
    for h in horas[1:]:
        esperado = (dt.datetime.combine(dt.date.min, anterior) + dt.timedelta(minutes=GRANULARIDAD_MIN)).time()
        if h == esperado:
            anterior = h
        else:
            fin = (dt.datetime.combine(dt.date.min, anterior) + dt.timedelta(minutes=GRANULARIDAD_MIN)).time()
            rangos.append((inicio, fin))
            inicio = h
            anterior = h
    fin = (dt.datetime.combine(dt.date.min, anterior) + dt.timedelta(minutes=GRANULARIDAD_MIN)).time()
    rangos.append((inicio, fin))
    return rangos


def _rangos_del_dia(fecha):
    """Lista de (hora_inicio, hora_fin) vigentes para esa fecha, ya resolviendo
    si hay una excepción de calendario que pisa el horario semanal."""
    excepcion = DiaExcepcional.objects.filter(fecha=fecha).first()
    if excepcion:
        if excepcion.tipo == 'HORARIO_REDUCIDO' and excepcion.hora_inicio and excepcion.hora_fin:
            return [(excepcion.hora_inicio, excepcion.hora_fin)]
        return []  # FERIADO o ASUETO: cerrado todo el día

    qs = HorarioAtencion.objects.filter(dia_semana=fecha.weekday(), activo=True).order_by('hora_inicio')
    return [(h.hora_inicio, h.hora_fin) for h in qs]


def esta_en_horario(momento=None):
    """Si todavía no se configuró ningún horario semanal, no se restringe
    nada (comportamiento previo: la derivación siempre está disponible)."""
    if not HorarioAtencion.objects.exists():
        return True
    momento = momento or timezone.localtime()
    for inicio, fin in _rangos_del_dia(momento.date()):
        if inicio <= momento.time() <= fin:
            return True
    return False


def proxima_apertura(momento=None):
    """Devuelve el próximo datetime (aware) en que arranca una franja de
    atención, buscando hacia adelante. None si no hay ningún horario cargado
    dentro del horizonte de búsqueda."""
    momento = momento or timezone.localtime()
    tz = timezone.get_current_timezone()

    for offset in range(HORIZONTE_DIAS):
        fecha = momento.date() + dt.timedelta(days=offset)
        for inicio, fin in _rangos_del_dia(fecha):
            inicio_dt = timezone.make_aware(dt.datetime.combine(fecha, inicio), tz)
            fin_dt = timezone.make_aware(dt.datetime.combine(fecha, fin), tz)
            if offset == 0 and momento > fin_dt:
                continue  # esa franja de hoy ya pasó
            if offset == 0 and inicio_dt <= momento <= fin_dt:
                return momento  # ya está dentro de horario ahora mismo
            return inicio_dt
    return None


def sincronizar_feriados(anio):
    """Trae los feriados/no laborables nacionales de ese año y los carga como
    DiaExcepcional tipo FERIADO. No pisa filas que un humano haya cargado o
    editado a mano (sincronizado=False) — solo crea las que faltan y
    actualiza las que la propia sincronización generó antes."""
    try:
        feriados = feriados_client.obtener_feriados(anio)
    except Exception:
        logger.exception('No se pudo sincronizar feriados del año %s', anio)
        return False

    for f in feriados:
        try:
            fecha = dt.date.fromisoformat(f['fecha'])
        except (KeyError, ValueError, TypeError):
            continue
        nombre = f.get('nombre') or 'Feriado nacional'

        obj, created = DiaExcepcional.objects.get_or_create(
            fecha=fecha,
            defaults={'tipo': 'FERIADO', 'descripcion': nombre, 'sincronizado': True},
        )
        if not created and obj.sincronizado and (obj.tipo != 'FERIADO' or obj.descripcion != nombre):
            obj.tipo = 'FERIADO'
            obj.descripcion = nombre
            obj.save(update_fields=['tipo', 'descripcion'])
    return True


def mensaje_fuera_de_horario(momento=None):
    momento = momento or timezone.localtime()
    proxima = proxima_apertura(momento)
    if proxima is None or proxima <= momento:
        return ''
    dias_es = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
    dia_txt = dias_es[proxima.weekday()]
    return (
        'En este momento estamos fuera de nuestro horario de atención para hablar con un operador. '
        f'El próximo horario disponible es el {dia_txt} {proxima:%d/%m} a las {proxima:%H:%M}hs.'
    )
