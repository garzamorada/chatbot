"""Suite de tests del chatbot (unitarios + integración).

Correr:
    python manage.py test chatbot --settings=config.settings_test
"""
import datetime as dt
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone


from chatbot import api_views, defaults, firma
from chatbot.forms import SaludosChatbotForm, TiemposChatbotForm
from chatbot.horarios import expandir_rango_a_horas, merge_horas_a_rangos
from chatbot.labels import aplicar_transicion_labels, etiquetas_posibles
from chatbot.models import (
    ConfiguracionChatbot, Conversacion, ConversacionLog, HorarioAtencion,
    InboxChatealo, MenuOpcion,
)

User = get_user_model()

PERMISOS_CHATBOT = [
    'ver_panel_chatbot', 'gestionar_menu_chatbot', 'gestionar_token_chatbot',
    'gestionar_archivos_chatbot', 'gestionar_horarios_chatbot', 'gestionar_inboxes_chatbot',
]


def crear_usuario(username, permisos=()):
    u = User.objects.create_user(username=username, password='x')
    if permisos:
        u.user_permissions.add(*Permission.objects.filter(
            codename__in=permisos, content_type__app_label='chatbot'))
    return User.objects.get(pk=u.pk)  # refresca el cache de permisos


def crear_usuario_admin_chatbot():
    return crear_usuario('op_chatbot', PERMISOS_CHATBOT)


# --------------------------------------------------------------------------- #
#  UNITARIOS — helpers de menú / texto                                         #
# --------------------------------------------------------------------------- #
class LimpiarTextoOpcionTests(TestCase):
    def test_saca_prefijo_de_numeracion_manual(self):
        casos = {
            '1 - Beneficios': 'Beneficios',
            '2) Turismo': 'Turismo',
            '5- Gremiales': 'Gremiales',
            '3. Afiliaciones': 'Afiliaciones',
            '10 — Obra Social': 'Obra Social',
        }
        for entrada, esperado in casos.items():
            self.assertEqual(api_views._limpiar_texto_opcion(entrada), esperado)

    def test_no_toca_texto_sin_prefijo_numerico(self):
        self.assertEqual(api_views._limpiar_texto_opcion('24/7 Guardia'), '24/7 Guardia')
        self.assertEqual(api_views._limpiar_texto_opcion('Sin número'), 'Sin número')

    def test_nombre_area_root_es_menu_principal(self):
        self.assertEqual(api_views._nombre_area(None), 'Menú principal')

    def test_nombre_area_saca_numeracion(self):
        op = MenuOpcion(texto='4 - Obra Social', tipo='SUBMENU')
        self.assertEqual(api_views._nombre_area(op), 'Obra Social')


class TextoMenuTests(TestCase):
    def test_numera_1_indexado_con_formato_n_guion(self):
        opciones = [MenuOpcion(texto='1 - Uno'), MenuOpcion(texto='Dos'), MenuOpcion(texto='Tres')]
        self.assertEqual(api_views._texto_menu(opciones), '1 - Uno\n2 - Dos\n3 - Tres')


class MatchOpcionTests(TestCase):
    def setUp(self):
        self.opciones = [
            MenuOpcion(texto='Beneficios', slug='beneficios'),
            MenuOpcion(texto='Turismo', slug='turismo'),
        ]

    def test_match_por_numero(self):
        self.assertIs(api_views._match_opcion('2', self.opciones), self.opciones[1])

    def test_match_por_linea_completa(self):
        self.assertIs(api_views._match_opcion('1 - Beneficios', self.opciones), self.opciones[0])

    def test_match_por_texto_exacto_case_insensitive(self):
        self.assertIs(api_views._match_opcion('  TURISMO ', self.opciones), self.opciones[1])

    def test_match_por_slug(self):
        self.assertIs(api_views._match_opcion('beneficios', self.opciones), self.opciones[0])

    def test_sin_match_devuelve_none(self):
        self.assertIsNone(api_views._match_opcion('hola qué tal', self.opciones))

    def test_numero_fuera_de_rango(self):
        self.assertIsNone(api_views._match_opcion('9', self.opciones))


class EncabezadoMenuTests(TestCase):
    def setUp(self):
        self.config = ConfiguracionChatbot.obtener()

    def test_default_raiz(self):
        self.assertEqual(
            api_views._encabezado_menu(self.config, None),
            defaults.SALUDO_INICIAL,
        )

    def test_default_area_reemplaza_placeholder(self):
        op = MenuOpcion(texto='2 - Beneficios', tipo='SUBMENU')
        self.assertIn('"Beneficios"', api_views._encabezado_menu(self.config, op))

    def test_variable_nombre(self):
        self.config.plantilla_saludo_inicial = 'Hola {nombre}, elegí una opción'
        self.assertEqual(
            api_views._encabezado_menu(self.config, None, nombre='Ana'),
            'Hola Ana, elegí una opción',
        )

    def test_menu_con_encabezado_incluye_listado(self):
        op = MenuOpcion(texto='Beneficios', tipo='SUBMENU')
        hijos = [MenuOpcion(texto='Kits'), MenuOpcion(texto='Turnos')]
        salida = api_views._menu_con_encabezado(self.config, op, hijos)
        self.assertIn('1 - Kits', salida)
        self.assertIn('2 - Turnos', salida)


class AplicarVariablesTests(TestCase):
    def test_reemplaza_area_y_nombre(self):
        self.assertEqual(
            defaults.aplicar_variables('{nombre} está en {area}', area='Turismo', nombre='Leo'),
            'Leo está en Turismo',
        )

    def test_faltantes_quedan_vacios(self):
        self.assertEqual(defaults.aplicar_variables('Hola {nombre}!'), 'Hola !')


class TransicionLabelsTests(TestCase):
    def test_archiva_la_anterior_y_agrega_la_nueva(self):
        out = aplicar_transicion_labels(['equipo-beneficios', 'urgente'], 'equipo-beneficios', 'equipo-turismo')
        self.assertIn('equipo-turismo', out)
        self.assertIn('hist-equipo-beneficios', out)
        self.assertIn('urgente', out)
        self.assertNotIn('equipo-beneficios', out)

    def test_sin_cambio_solo_asegura_presencia(self):
        self.assertEqual(
            aplicar_transicion_labels(['equipo-x'], 'equipo-x', 'equipo-x'),
            ['equipo-x'],
        )


# --------------------------------------------------------------------------- #
#  UNITARIOS — firma del webhook                                               #
# --------------------------------------------------------------------------- #
class FirmaWebhookTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_sin_secreto_devuelve_none(self):
        ok, _ = firma.verificar(self.rf.post('/x/', data=b'{}', content_type='application/json'), '')
        self.assertIsNone(ok)

    def test_hmac_sha256_hex_en_header_conocido(self):
        import hashlib
        import hmac
        body = b'{"event":"x"}'
        secreto = 'clave-test'
        sig = hmac.new(secreto.encode(), body, hashlib.sha256).hexdigest()
        req = self.rf.post('/x/', data=body, content_type='application/json',
                           HTTP_X_CHATEALO_SIGNATURE=sig)
        ok, detalle = firma.verificar(req, secreto)
        self.assertTrue(ok, detalle)

    def test_firma_invalida(self):
        req = self.rf.post('/x/', data=b'{}', content_type='application/json',
                           HTTP_X_CHATEALO_SIGNATURE='deadbeef')
        ok, _ = firma.verificar(req, 'clave-test')
        self.assertFalse(ok)


# --------------------------------------------------------------------------- #
#  UNITARIOS — inbox / etiquetas / horarios                                    #
# --------------------------------------------------------------------------- #
class InboxTests(TestCase):
    def test_registrar_desde_payload_crea_y_actualiza(self):
        InboxChatealo.registrar_desde_payload(188, 'Cuenta principal', 'Channel::Whatsapp', 28)
        ib = InboxChatealo.objects.get(inbox_id=188)
        self.assertEqual(ib.eventos_recibidos, 1)
        self.assertEqual(ib.channel_detectado, 'Channel::Whatsapp')
        InboxChatealo.registrar_desde_payload(188, 'Cuenta principal', 'Channel::Whatsapp', 28)
        ib.refresh_from_db()
        self.assertEqual(ib.eventos_recibidos, 2)

    def test_registrar_sin_inbox_id_no_hace_nada(self):
        self.assertIsNone(InboxChatealo.registrar_desde_payload(None))
        self.assertEqual(InboxChatealo.objects.count(), 0)

    def test_tipo_fuente_sugerido(self):
        self.assertEqual(InboxChatealo(channel_detectado='Channel::Whatsapp').tipo_fuente_sugerido, 'WHATSAPP')
        self.assertEqual(InboxChatealo(channel_detectado='Channel::WebWidget').tipo_fuente_sugerido, 'WEBCHAT')
        self.assertEqual(InboxChatealo(channel_detectado='Channel::Api').tipo_fuente_sugerido, '')

    def test_extraer_inbox_tolerante(self):
        self.assertEqual(
            api_views._extraer_inbox({'inbox': {'id': 45, 'name': 'wpp'}, 'conversation': {'channel': 'Channel::Api'}}),
            (45, 'wpp', 'Channel::Api'),
        )
        self.assertEqual(
            api_views._extraer_inbox({'inbox_id': 46, 'channel': 'Channel::WebWidget'}),
            (46, '', 'Channel::WebWidget'),
        )


class EtiquetasPosiblesTests(TestCase):
    def test_lista_menu_y_equipo(self):
        beneficios = MenuOpcion.objects.create(texto='Beneficios', tipo='SUBMENU', slug='beneficios')
        MenuOpcion.objects.create(texto='Operador', tipo='DERIVACION', parent=beneficios, slug='op-benef')
        MenuOpcion.objects.create(texto='Operador raíz', tipo='DERIVACION', slug='op-raiz')

        et = etiquetas_posibles()
        labels_menu = [e['label'] for e in et['menu']]
        self.assertIn('menu-raiz', labels_menu)
        self.assertIn('menu-beneficios', labels_menu)
        labels_equipo = [e['label'] for e in et['equipo']]
        self.assertIn('equipo-beneficios', labels_equipo)
        self.assertIn('equipo-general', labels_equipo)


class HorariosHelpersTests(TestCase):
    def test_expandir_rango_a_horas(self):
        horas = expandir_rango_a_horas(dt.time(9, 0), dt.time(10, 30))
        self.assertEqual(horas, [dt.time(9, 0), dt.time(9, 30), dt.time(10, 0)])

    def test_merge_horas_a_rangos(self):
        horas = [dt.time(9, 0), dt.time(9, 30), dt.time(10, 0), dt.time(15, 0)]
        rangos = merge_horas_a_rangos(horas)
        self.assertEqual(rangos, [(dt.time(9, 0), dt.time(10, 30)), (dt.time(15, 0), dt.time(15, 30))])


class DefaultsFormTests(TestCase):
    def test_form_muestra_default_cuando_campo_vacio(self):
        cfg = ConfiguracionChatbot.obtener()
        form = SaludosChatbotForm(instance=cfg)
        self.assertEqual(form.initial['mensaje_bienvenida'], defaults.MENSAJE_BIENVENIDA)

    def test_guardar_default_sin_cambios_lo_deja_en_blanco(self):
        cfg = ConfiguracionChatbot.obtener()
        form = SaludosChatbotForm({
            'mensaje_bienvenida': defaults.MENSAJE_BIENVENIDA,
            'plantilla_saludo_inicial': defaults.SALUDO_INICIAL,
            'plantilla_saludo_area': 'Estás en {area}',
        }, instance=cfg)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertEqual(obj.mensaje_bienvenida, '')
        self.assertEqual(obj.plantilla_saludo_area, 'Estás en {area}')

    def test_tiempos_form_valida_enteros(self):
        cfg = ConfiguracionChatbot.obtener()
        form = TiemposChatbotForm(
            {'segundos_remostrar_menu': 45, 'minutos_inactividad_cierre': 10, 'mensaje_cierre_inactividad': ''},
            instance=cfg,
        )
        self.assertTrue(form.is_valid(), form.errors)


# --------------------------------------------------------------------------- #
#  INTEGRACIÓN — webhook_chatealo                                              #
# --------------------------------------------------------------------------- #
@mock.patch('chatbot.api_views.chatealo_client')
class WebhookIntegrationTests(TestCase):
    def setUp(self):
        self.config = ConfiguracionChatbot.obtener()
        self.config.chatealo_account_id = 28
        self.config.save()
        self.secret = self.config.webhook_secret
        self.client = Client()

        self.beneficios = MenuOpcion.objects.create(texto='Beneficios', tipo='SUBMENU', slug='beneficios', orden=1)
        self.kits = MenuOpcion.objects.create(
            texto='Kits escolares', tipo='RESPUESTA', slug='kits',
            parent=self.beneficios, respuesta_texto='Los kits se retiran en la sede.', orden=1,
        )
        MenuOpcion.objects.create(texto='Volver', tipo='VOLVER', slug='volver-benef', parent=self.beneficios, orden=2)
        self.operador = MenuOpcion.objects.create(
            texto='Hablar con alguien', tipo='DERIVACION', slug='operador',
            mensaje_derivacion='Te derivamos con un agente.', orden=2,
        )
        MenuOpcion.objects.create(texto='Terminar', tipo='TERMINAR', slug='terminar', orden=3)

    # ---- helpers ----
    def _webhook(self, content, message_id, *, event='message_created', labels=None,
                 status='open', message_type='incoming', **top):
        if event in ('conversation_created', 'conversation_status_changed'):
            # en estos eventos el payload ES la conversación (no viene anidada)
            payload = {
                'event': event, 'id': 5001, 'status': status, 'inbox_id': 188,
                'account': {'id': 28}, 'inbox': {'id': 188, 'name': 'wpp'},
                'meta': {'sender': {'phone_number': '+5491100', 'name': 'Ana'}},
            }
        else:
            payload = {
                'event': event, 'message_type': message_type, 'private': False,
                'content_type': 'text', 'content': content, 'id': message_id,
                'account': {'id': 28}, 'inbox': {'id': 188, 'name': 'wpp'},
                'sender': {'phone_number': '+5491100', 'name': 'Ana'},
                'conversation': {'id': 5001, 'status': status, 'labels': labels or [], 'inbox_id': 188},
            }
        payload.update(top)
        return self.client.post(
            f'/chatbot/webhook/chatealo/{self.secret}/',
            data=json.dumps(payload), content_type='application/json',
        )

    def _conv(self):
        return Conversacion.objects.get(conversation_id=5001)

    def _ult_log(self):
        return ConversacionLog.objects.filter(conversacion__conversation_id=5001).first()

    # ---- casos ----
    def test_secreto_incorrecto_404(self, cli):
        r = self.client.post('/chatbot/webhook/chatealo/malo/', data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 404)

    def test_primer_contacto_manda_bienvenida_sin_no_entendi(self, cli):
        self._webhook('hola', 1)
        enviado = cli.enviar_mensaje.call_args[0][2]
        self.assertIn(defaults.MENSAJE_BIENVENIDA.split('.')[0], enviado)
        self.assertNotIn('No entendí', enviado)
        self.assertEqual(self._ult_log().accion, 'BIENVENIDA')
        self.assertTrue(self._conv().menu_mostrado)
        # el inbox se autoregistró
        self.assertTrue(InboxChatealo.objects.filter(inbox_id=188).exists())

    def test_texto_invalido_en_raiz_no_dice_no_entendi(self, cli):
        self._webhook('hola', 1)
        cli.reset_mock()
        self._webhook('cualquier cosa', 2)
        enviado = cli.enviar_mensaje.call_args[0][2]
        self.assertNotIn('No entendí', enviado)
        self.assertEqual(self._ult_log().accion, 'MENU')

    def test_navegar_submenu_no_aplica_labels_en_chatealo(self, cli):
        self._webhook('hola', 1)
        cli.reset_mock()
        self._webhook('1', 2)  # Beneficios
        self.assertEqual(self._conv().menu_actual, self.beneficios)
        cli.actualizar_labels.assert_not_called()
        log = self._ult_log()
        self.assertEqual(log.accion, 'MENU')
        self.assertIn('menu-beneficios', log.detalle)

    def test_opcion_invalida_dentro_de_submenu_dice_no_entendi(self, cli):
        self._webhook('1', 1)  # entra a Beneficios (primer contacto -> con bienvenida)
        cli.reset_mock()
        self._webhook('zzz', 2)
        enviado = cli.enviar_mensaje.call_args[0][2]
        self.assertIn('No entendí esa opción', enviado)
        self.assertEqual(self._ult_log().accion, 'INVALIDA')

    def test_respuesta_agenda_remostrar_menu(self, cli):
        self._webhook('1', 1)   # Beneficios
        self._webhook('1', 2)   # Kits escolares (RESPUESTA)
        conv = self._conv()
        self.assertEqual(self._ult_log().accion, 'RESPUESTA')
        self.assertIsNotNone(conv.remostrar_menu_en)

    def test_derivacion_aplica_equipo_y_finaliza(self, cli):
        self._webhook('hola', 1)
        cli.reset_mock()
        self._webhook('Hablar con alguien', 2)
        conv = self._conv()
        self.assertTrue(conv.finalizado)
        self.assertEqual(conv.label_equipo_actual, 'equipo-general')
        cli.actualizar_labels.assert_called_once()
        labels_enviadas = cli.actualizar_labels.call_args[0][2]
        self.assertIn('equipo-general', labels_enviadas)
        self.assertEqual(self._ult_log().accion, 'DERIVACION')

    def test_terminar_resuelve_conversacion(self, cli):
        self._webhook('hola', 1)
        self._webhook('Terminar', 2)
        conv = self._conv()
        self.assertTrue(conv.finalizado)
        self.assertTrue(conv.chatealo_resuelta)
        cli.cambiar_estado_conversacion.assert_called_with(mock.ANY, 5001, 'resolved')
        self.assertEqual(self._ult_log().accion, 'TERMINAR')

    def test_conversacion_derivada_ignora_mensajes(self, cli):
        self._webhook('hola', 1)
        self._webhook('Hablar con alguien', 2)  # deriva
        cli.reset_mock()
        self._webhook('sigo escribiendo', 3)
        cli.enviar_mensaje.assert_not_called()
        self.assertEqual(self._ult_log().accion, 'IGNORADO')

    def test_reactivacion_tras_resolver(self, cli):
        self._webhook('hola', 1)
        self._webhook('Hablar con alguien', 2)                 # deriva
        self._webhook('', 3, event='conversation_status_changed', status='resolved')
        cli.reset_mock()
        self._webhook('hola de nuevo', 4)                      # el contacto vuelve
        conv = self._conv()
        self.assertFalse(conv.finalizado)
        self.assertIsNone(conv.menu_actual)
        self.assertEqual(self._ult_log().accion, 'REACTIVADA')
        enviado = cli.enviar_mensaje.call_args[0][2]
        self.assertIn('1 - Beneficios', enviado)

    def test_idempotencia_por_message_id(self, cli):
        self._webhook('hola', 77)
        cli.reset_mock()
        self._webhook('hola', 77)  # mismo id -> se ignora
        cli.enviar_mensaje.assert_not_called()

    def test_message_updated_failed_registra_log(self, cli):
        self._webhook('hola', 1)  # crea la conversación
        r = self.client.post(
            f'/chatbot/webhook/chatealo/{self.secret}/',
            data=json.dumps({
                'event': 'message_updated', 'id': 999, 'status': 'failed',
                'content': 'respuesta que falló',
                'content_attributes': {'external_error': '(#131030) fuera de la lista'},
                'conversation': {'id': 5001},
            }),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(
            ConversacionLog.objects.filter(conversacion__conversation_id=5001, accion='MSG_FALLIDO').exists()
        )

    def test_conversation_created_registra_estado(self, cli):
        self._webhook('', 1, event='conversation_created', status='pending')
        conv = self._conv()
        self.assertEqual(conv.estado, 'pending')
        self.assertEqual(conv.inbox_id, 188)
        self.assertEqual(self._ult_log().accion, 'CONVERSACION')

    def test_firma_invalida_con_enforce_devuelve_401(self, cli):
        self.config.webhook_firma_secret = 'clave'
        self.config.webhook_firma_enforce = True
        self.config.save()
        r = self.client.post(
            f'/chatbot/webhook/chatealo/{self.secret}/',
            data=json.dumps({'event': 'message_created'}),
            content_type='application/json', HTTP_X_CHATEALO_SIGNATURE='mal',
        )
        self.assertEqual(r.status_code, 401)

    def test_bot_pausado_no_responde(self, cli):
        self.config.activo = False
        self.config.save()
        self._webhook('hola', 1)
        cli.enviar_mensaje.assert_not_called()
        cli.actualizar_labels.assert_not_called()
        self.assertEqual(self._ult_log().accion, 'PAUSADO')

    def test_bot_reactivado_vuelve_a_responder(self, cli):
        self.config.activo = False
        self.config.save()
        self._webhook('hola', 1)
        self.config.activo = True
        self.config.save()
        cli.reset_mock()
        self._webhook('hola', 2)
        cli.enviar_mensaje.assert_called_once()

    def test_fuera_de_horario_no_deriva(self, cli):
        self._webhook('hola', 1)
        with mock.patch('chatbot.api_views.esta_en_horario', return_value=False), \
             mock.patch('chatbot.api_views.mensaje_fuera_de_horario', return_value='Cerrado ahora.'):
            self._webhook('Hablar con alguien', 2)
        conv = self._conv()
        self.assertFalse(conv.finalizado)
        self.assertEqual(self._ult_log().accion, 'FUERA_HORARIO')


# --------------------------------------------------------------------------- #
#  INTEGRACIÓN — comando de temporizadores                                     #
# --------------------------------------------------------------------------- #
@mock.patch('chatbot.management.commands.procesar_temporizadores_chatbot.chatealo_client')
class TemporizadoresCommandTests(TestCase):
    def setUp(self):
        self.config = ConfiguracionChatbot.obtener()
        self.config.minutos_inactividad_cierre = 5
        self.config.save()
        self.menu = MenuOpcion.objects.create(texto='Beneficios', tipo='SUBMENU', slug='beneficios')

    def test_remuestra_menu_vencido(self, cli):
        c = Conversacion.objects.create(
            conversation_id=6001, menu_actual=self.menu,
            remostrar_menu_en=timezone.now() - dt.timedelta(seconds=10),
        )
        call_command('procesar_temporizadores_chatbot')
        c.refresh_from_db()
        self.assertIsNone(c.remostrar_menu_en)
        cli.enviar_mensaje.assert_called_once()
        self.assertTrue(ConversacionLog.objects.filter(conversacion=c, accion='MENU').exists())

    def test_cierra_conversacion_inactiva(self, cli):
        c = Conversacion.objects.create(
            conversation_id=6002,
            ultima_actividad=timezone.now() - dt.timedelta(minutes=30),
        )
        call_command('procesar_temporizadores_chatbot')
        c.refresh_from_db()
        self.assertTrue(c.finalizado)
        self.assertTrue(c.chatealo_resuelta)
        cli.cambiar_estado_conversacion.assert_called_with(mock.ANY, 6002, 'resolved')
        self.assertTrue(ConversacionLog.objects.filter(conversacion=c, accion='INACTIVIDAD').exists())

    def test_comando_no_corre_si_bot_pausado(self, cli):
        self.config.activo = False
        self.config.save()
        c = Conversacion.objects.create(
            conversation_id=6009,
            ultima_actividad=timezone.now() - dt.timedelta(days=1),
        )
        call_command('procesar_temporizadores_chatbot')
        c.refresh_from_db()
        self.assertFalse(c.finalizado)
        cli.enviar_mensaje.assert_not_called()

    def test_no_cierra_si_config_en_cero(self, cli):
        self.config.minutos_inactividad_cierre = 0
        self.config.save()
        c = Conversacion.objects.create(
            conversation_id=6003,
            ultima_actividad=timezone.now() - dt.timedelta(days=1),
        )
        call_command('procesar_temporizadores_chatbot')
        c.refresh_from_db()
        self.assertFalse(c.finalizado)


# --------------------------------------------------------------------------- #
#  INTEGRACIÓN — panel (permisos + render)                                     #
# --------------------------------------------------------------------------- #
class PanelViewsTests(TestCase):
    def setUp(self):
        self.user = crear_usuario_admin_chatbot()
        self.client = Client()
        self.client.force_login(self.user)
        ConfiguracionChatbot.obtener()

    def test_panel_requiere_permiso(self):
        otro = crear_usuario('pelado')
        c = Client()
        c.force_login(otro)
        self.assertEqual(c.get('/chatbot/').status_code, 403)

    def test_panel_ok_con_permiso(self):
        r = self.client.get('/chatbot/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Panel de Chatbot')
        self.assertContains(r, 'id="tab-menu"')
        self.assertContains(r, 'id="tab-mensajes"')

    def test_editar_saludos_guarda(self):
        r = self.client.post('/chatbot/config/saludos/', {
            'mensaje_bienvenida': 'Hola, soy el bot',
            'plantilla_saludo_inicial': defaults.SALUDO_INICIAL,
            'plantilla_saludo_area': defaults.SALUDO_AREA,
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ConfiguracionChatbot.obtener().mensaje_bienvenida, 'Hola, soy el bot')

    def test_editar_inbox(self):
        ib = InboxChatealo.objects.create(inbox_id=188, nombre_detectado='wpp')
        r = self.client.post(f'/chatbot/inboxes/{ib.pk}/editar/', {
            f'inbox{ib.pk}-nombre': 'WhatsApp',
            f'inbox{ib.pk}-tipo_fuente': 'WHATSAPP',
            f'inbox{ib.pk}-responder_bot': 'on',
        })
        self.assertEqual(r.status_code, 302)
        ib.refresh_from_db()
        self.assertEqual(ib.nombre, 'WhatsApp')
        self.assertEqual(ib.tipo_fuente, 'WHATSAPP')

    @mock.patch('chatbot.views.chatealo_client')
    def test_webhooks_listar(self, cli):
        cli.listar_webhooks.return_value = [{'id': 6, 'url': 'https://x/', 'subscriptions': ['message_created']}]
        r = self.client.post('/chatbot/webhooks/listar/')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(len(data['webhooks']), 1)

    def test_toggle_bot(self):
        r = self.client.post('/chatbot/toggle-bot/', {'activo': '0'})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['activo'])
        self.assertFalse(ConfiguracionChatbot.obtener().activo)
        r = self.client.post('/chatbot/toggle-bot/', {'activo': '1'})
        self.assertTrue(ConfiguracionChatbot.obtener().activo)

    def test_manual_html(self):
        r = self.client.get('/chatbot/manual/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/html', r['Content-Type'])
        self.assertIn(b'Manual del Chatbot', r.content)

    def test_manual_pdf(self):
        r = self.client.get('/chatbot/manual/?formato=pdf')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')

    def test_horarios_embed_usa_base_sin_navbar(self):
        r = self.client.get('/chatbot/horarios/?embed=1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['X-Frame-Options'], 'SAMEORIGIN')
        self.assertNotContains(r, 'Volver al panel')


class GrupoAdminChatbotTests(TestCase):
    """La migración de datos 0014 no corre con --settings=settings_test
    (MIGRATION_MODULES deshabilitado); este test crea el grupo a mano y
    verifica que los 6 permisos custom existen."""
    def test_permisos_custom_existen(self):
        for codename in PERMISOS_CHATBOT:
            self.assertTrue(
                Permission.objects.filter(codename=codename, content_type__app_label='chatbot').exists(),
                codename,
            )
