"""Temporizadores del chatbot — se corre por cron cada minuto.

  * * * * * python manage.py procesar_temporizadores_chatbot

Dos tareas:
1. Tras responder una opción (RESPUESTA), pasados `segundos_remostrar_menu`,
   volver a mostrar el menú donde estaba el contacto.
2. Si el contacto no escribe en `minutos_inactividad_cierre` minutos, mandar el
   mensaje de cierre y terminar la conversación.
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from chatbot import chatealo_client, defaults
from chatbot.api_views import _menu_con_encabezado, _opciones_de
from chatbot.defaults import aplicar_variables
from chatbot.models import ConfiguracionChatbot, Conversacion, ConversacionLog

logger = logging.getLogger('chatbot.webhook')


class Command(BaseCommand):
    help = 'Re-muestra el menú tras una respuesta y cierra conversaciones inactivas del chatbot.'

    def handle(self, *args, **options):
        config = ConfiguracionChatbot.obtener()
        ahora = timezone.now()
        n_menu = self._remostrar_menus(config, ahora)
        n_cierre = self._cerrar_inactivas(config, ahora)
        self.stdout.write(
            f'menús re-mostrados: {n_menu} | cerradas por inactividad: {n_cierre}'
        )

    def _remostrar_menus(self, config, ahora):
        pendientes = Conversacion.objects.filter(
            finalizado=False,
            remostrar_menu_en__isnull=False,
            remostrar_menu_en__lte=ahora,
        ).select_related('menu_actual')
        n = 0
        for c in pendientes:
            opciones = _opciones_de(c.menu_actual)
            texto = _menu_con_encabezado(config, c.menu_actual, opciones, c.nombre_contacto)
            detalle = 'menú re-mostrado por temporizador'
            try:
                chatealo_client.enviar_mensaje(config, c.conversation_id, texto)
            except Exception as exc:
                logger.exception('re-mostrar menú de conversación %s', c.conversation_id)
                detalle += f'; error: {exc}'
            c.remostrar_menu_en = None
            c.save(update_fields=['remostrar_menu_en', 'actualizado'])
            ConversacionLog.objects.create(conversacion=c, accion='MENU', detalle=detalle)
            n += 1
        return n

    def _cerrar_inactivas(self, config, ahora):
        minutos = config.minutos_inactividad_cierre or 0
        if minutos <= 0:
            return 0
        limite = ahora - timedelta(minutes=minutos)
        inactivas = Conversacion.objects.filter(
            finalizado=False,
            ultima_actividad__isnull=False,
            ultima_actividad__lt=limite,
        )
        plantilla = (config.mensaje_cierre_inactividad or '').strip() or defaults.MENSAJE_CIERRE_INACTIVIDAD
        n = 0
        for c in inactivas:
            errores = []
            mensaje = aplicar_variables(plantilla, nombre=c.nombre_contacto)
            try:
                chatealo_client.enviar_mensaje(config, c.conversation_id, mensaje)
            except Exception as exc:
                logger.exception('cierre por inactividad (mensaje) conversación %s', c.conversation_id)
                errores.append(str(exc))
            try:
                chatealo_client.cambiar_estado_conversacion(config, c.conversation_id, 'resolved')
            except Exception as exc:
                logger.exception('cierre por inactividad (resolved) conversación %s', c.conversation_id)
                errores.append(str(exc))
            c.finalizado = True
            c.chatealo_resuelta = True
            c.remostrar_menu_en = None
            c.save(update_fields=['finalizado', 'chatealo_resuelta', 'remostrar_menu_en', 'actualizado'])
            detalle = f'cerrada tras {minutos} min sin actividad del contacto'
            if errores:
                detalle += '; ' + '; '.join(errores)
            ConversacionLog.objects.create(conversacion=c, accion='INACTIVIDAD', detalle=detalle)
            n += 1
        return n
