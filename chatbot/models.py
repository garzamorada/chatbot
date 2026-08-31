import logging
import os
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

logger = logging.getLogger('chatbot.webhook')


class ConfiguracionChatbot(models.Model):
    """Configuración singleton de la integración con chatealo.io (su API real,
    no la Deploy API del producto Typebot.io). `webhook_secret` es el segmento secreto que
    va en la URL que se registra en chatealo como webhook (no soportan headers
    custom, así que la protección es el propio segmento de la URL). Los
    campos `chatealo_*` son las credenciales que ELLOS nos dieron para poder
    llamar a su API (enviar mensajes, poner etiquetas)."""
    webhook_secret = models.CharField(max_length=64, unique=True, editable=False, default='')

    webhook_firma_secret = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Secreto de firma del webhook',
        help_text='Secreto que da chatealo para verificar la firma de los eventos entrantes.',
    )
    webhook_firma_enforce = models.BooleanField(
        default=False, verbose_name='Rechazar eventos con firma inválida',
        help_text='Si está apagado, una firma que no valida sólo se registra en el log '
                   '(modo observación); si está prendido, se responde 401 y no se procesa.',
    )

    chatealo_base_url = models.URLField(
        max_length=200, default='https://client.chatealo.io',
        verbose_name='URL base de la API de chatealo',
    )
    chatealo_account_id = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Account ID de chatealo',
    )
    chatealo_authorization = models.CharField(
        max_length=300, blank=True, default='', verbose_name='Header Authorization',
        help_text='Valor del header "Authorization" que pide la API de chatealo.',
    )
    chatealo_admin_id = models.CharField(
        max_length=100, blank=True, default='', verbose_name='Header X-Chatealo-Admin-Id',
    )
    chatealo_user_token = models.CharField(
        max_length=100, blank=True, default='', verbose_name='Header X-AppChatealo-User-Token',
    )

    mensaje_bienvenida = models.TextField(
        blank=True, verbose_name='Mensaje de bienvenida (primer contacto)',
        help_text='Se manda una sola vez, la primera vez que el contacto escribe, antes del menú. '
                   'Sirve para aclarar que del otro lado hay un asistente automático. '
                   'Variable disponible: {nombre}. Si se deja vacío se usa uno por defecto.',
    )
    plantilla_saludo_inicial = models.TextField(
        blank=True, verbose_name='Saludo del menú principal',
        help_text='Encabezado del menú principal (antes del listado de opciones). '
                   'Variable disponible: {nombre}. Si se deja vacío se usa uno por defecto.',
    )
    plantilla_saludo_area = models.TextField(
        blank=True, verbose_name='Saludo de cada menú',
        help_text='Encabezado al entrar a un menú. Variables: {area} (nombre del menú, sin número) '
                   'y {nombre} (nombre del contacto). Si se deja vacío se usa uno por defecto.',
    )

    segundos_remostrar_menu = models.PositiveIntegerField(
        default=60, verbose_name='Segundos para volver a mostrar el menú tras una respuesta',
        help_text='Después de contestar una opción de tipo Respuesta, esperar estos segundos y '
                   'volver a mostrar el menú donde estaba el contacto. 0 = desactivado.',
    )
    minutos_inactividad_cierre = models.PositiveIntegerField(
        default=0, verbose_name='Minutos de inactividad para cerrar la conversación',
        help_text='Si el contacto no escribe durante esta cantidad de minutos, el bot manda un '
                   'mensaje de cierre y termina la conversación. 0 = desactivado.',
    )
    mensaje_cierre_inactividad = models.TextField(
        blank=True, verbose_name='Mensaje de cierre por inactividad',
        help_text='Se manda al cerrar una conversación por inactividad. Variable disponible: {nombre}. '
                   'Si se deja vacío se usa uno por defecto.',
    )

    mensaje_despedida = models.TextField(
        blank=True, verbose_name='Mensaje de despedida',
        help_text='Texto único que mandan TODAS las opciones tipo "Terminar", sin importar en qué '
                   'menú estén. Variable disponible: {nombre}. '
                   'Si se deja vacío se usa un mensaje genérico por defecto.',
    )
    archivo_despedida = models.ForeignKey(
        'ArchivoChatbot', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Archivo adjunto al despedirse',
    )

    activo = models.BooleanField(
        default=True, verbose_name='Bot activo',
        help_text='Interruptor general. Si se apaga, el bot NO responde ningún mensaje '
                   '(los eventos se siguen recibiendo pero se ignoran). Útil mientras se '
                   'configura el menú.',
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    ultimo_uso = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.webhook_secret:
            self.webhook_secret = secrets.token_hex(20)
        super().save(*args, **kwargs)

    def regenerar_webhook_secret(self):
        self.webhook_secret = secrets.token_hex(20)
        self.save(update_fields=['webhook_secret', 'actualizado'])

    def __str__(self):
        return f"Configuración chatealo ({'activa' if self.activo else 'inactiva'})"

    @classmethod
    def obtener(cls):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    class Meta:
        verbose_name = 'Configuración de Chatbot'
        verbose_name_plural = 'Configuración de Chatbot'
        permissions = [
            ('ver_panel_chatbot', 'Ver panel de configuración de Chatbot'),
            ('gestionar_token_chatbot', 'Editar la configuración y regenerar el secreto del webhook de Chatbot'),
        ]


TIPO_OPCION_CHOICES = (
    ('SUBMENU', 'Menú — agrupa otras opciones'),
    ('RESPUESTA', 'Respuesta directa — el bot contesta un texto'),
    ('DERIVACION', 'Derivación — se deriva a un agente/área'),
    ('VOLVER', 'Volver al menú anterior'),
    ('INICIO', 'Ir al menú principal'),
    ('TERMINAR', 'Terminar la conversación'),
)


class MenuOpcion(models.Model):
    """Nodo del árbol de menús del bot de atención por WhatsApp.
    `slug` se usa para armar las etiquetas 'menu-<slug>' / 'equipo-<slug>'
    que se aplican en la conversación de chatealo."""
    texto = models.CharField(max_length=200, verbose_name='Texto de la opción')
    slug = models.SlugField(
        max_length=100, unique=True, blank=True,
        help_text='Se autogenera del texto si se deja vacío. Se usa para las etiquetas menu-<slug> / equipo-<slug>.',
    )
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True,
        related_name='hijos', verbose_name='Dentro del menú',
    )
    orden = models.IntegerField(default=0, verbose_name='Orden')
    tipo = models.CharField(
        max_length=12, choices=TIPO_OPCION_CHOICES, default='SUBMENU', verbose_name='Tipo',
    )

    # tipo = RESPUESTA / VOLVER / INICIO / TERMINAR (mensaje opcional)
    respuesta_texto = models.TextField(
        blank=True, verbose_name='Texto de respuesta',
        help_text='Texto que el bot devuelve al usuario cuando elige esta opción. '
                   'Solo aplica al tipo Respuesta: Menú/Volver/Inicio navegan sin mensaje propio, '
                   'y Terminar usa el mensaje único de ConfiguracionChatbot.mensaje_despedida.',
    )

    # tipo = DERIVACION — el área/equipo que recibe la derivación se resuelve
    # del lado de chatealo (reglas de automatización sobre la etiqueta
    # 'equipo-<slug>'); acá solo se aplica la etiqueta y se deja constancia en el log.
    mensaje_derivacion = models.TextField(
        blank=True, verbose_name='Mensaje al derivar',
        help_text='Texto que el bot muestra al usuario al derivarlo (ej: horario de atención del área).',
    )

    archivo = models.ForeignKey(
        'ArchivoChatbot', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='Archivo adjunto',
        help_text='Se incluye su URL pública en la respuesta que recibe el bot.',
    )

    activo = models.BooleanField(default=True)

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.texto)[:90] or 'opcion'
            slug = base_slug
            n = 1
            while MenuOpcion.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base_slug}-{n}'
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def label_menu(self):
        return f'menu-{self.slug}'

    def __str__(self):
        return self.texto

    class Meta:
        verbose_name = 'Opción de menú'
        verbose_name_plural = 'Opciones de menú'
        ordering = ['orden', 'texto']
        permissions = [
            ('gestionar_menu_chatbot', 'Crear, editar y eliminar el menú de Chatbot'),
        ]


def archivo_chatbot_path(instance, filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    nombre = f'{instance.slug}.{ext}' if ext else instance.slug
    return os.path.join('chatbot/archivos', nombre)


class ArchivoChatbot(models.Model):
    """Repositorio de archivos (imágenes, planillas, docs, PDFs) para enlazar
    desde las opciones del menú. El archivo se guarda como <slug>.<extension>
    y se expone públicamente vía MEDIA_URL."""
    nombre = models.CharField(max_length=200, verbose_name='Nombre')
    slug = models.SlugField(max_length=220, unique=True, blank=True, editable=False)
    archivo = models.FileField(upload_to=archivo_chatbot_path, max_length=255, verbose_name='Archivo')

    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    fecha_subida = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.nombre)
            slug = base_slug
            n = 1
            while ArchivoChatbot.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base_slug}-{n}'
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.archivo.delete(save=False)
        super().delete(*args, **kwargs)

    @property
    def extension(self):
        return self.archivo.name.rsplit('.', 1)[-1].lower() if '.' in self.archivo.name else ''

    def __str__(self):
        return self.nombre

    class Meta:
        verbose_name = 'Archivo de Chatbot'
        verbose_name_plural = 'Archivos de Chatbot'
        ordering = ['-fecha_subida']
        permissions = [
            ('gestionar_archivos_chatbot', 'Subir y eliminar archivos del repositorio de Chatbot'),
        ]


ESTADO_CONVERSACION_CHOICES = (
    ('open', 'Abierta'),
    ('pending', 'Pendiente'),
    ('resolved', 'Resuelta'),
    ('snoozed', 'Pospuesta'),
)


class Conversacion(models.Model):
    """Estado local de una conversación de chatealo (1 fila por conversation_id
    real de chatealo). Además de guiar el árbol de menú, refleja las etiquetas
    'menu-<slug>' / 'equipo-<slug>' que se van aplicando del lado de chatealo."""
    conversation_id = models.PositiveIntegerField(unique=True)
    account_id = models.PositiveIntegerField(null=True, blank=True)
    inbox_id = models.PositiveIntegerField(null=True, blank=True, verbose_name='Inbox ID')
    contacto = models.CharField(max_length=100, blank=True, verbose_name='Contacto')
    nombre_contacto = models.CharField(max_length=150, blank=True, verbose_name='Nombre del contacto')
    estado = models.CharField(
        max_length=20, blank=True, choices=ESTADO_CONVERSACION_CHOICES,
        help_text="Último 'status' informado por chatealo (conversation_created / _status_changed).",
    )

    menu_actual = models.ForeignKey(
        MenuOpcion, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Menú actual',
    )
    label_equipo_actual = models.CharField(
        max_length=110, blank=True,
        help_text="Última etiqueta 'equipo-<slug>' aplicada, para poder reemplazarla por 'hist-...' si cambia.",
    )
    finalizado = models.BooleanField(default=False, help_text='True una vez derivada a un equipo o terminada.')
    menu_mostrado = models.BooleanField(
        default=False,
        help_text='True una vez que el bot le mostró el menú al contacto al menos una vez. '
                   'Mientras sea False, un mensaje que no matchea recibe el saludo de bienvenida '
                   '(no "No entendí esa opción").',
    )
    chatealo_resuelta = models.BooleanField(
        default=False,
        help_text='True si chatealo informó que la conversación quedó "resolved" después de '
                   'derivarla/terminarla. Si el contacto vuelve a escribir, el bot se reactiva y '
                   'muestra de nuevo el menú.',
    )

    ultimo_message_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='id del último mensaje de chatealo procesado, para ignorar reintentos duplicados del webhook.',
    )
    ultima_actividad = models.DateTimeField(
        null=True, blank=True,
        help_text='Último mensaje entrante del contacto. Base para el cierre por inactividad.',
    )
    remostrar_menu_en = models.DateTimeField(
        null=True, blank=True,
        help_text='Momento en que el temporizador debe volver a mostrar el menú actual '
                   '(se agenda al responder una opción; se cancela si el contacto escribe antes).',
    )

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Conversación #{self.conversation_id}'

    class Meta:
        verbose_name = 'Conversación'
        verbose_name_plural = 'Conversaciones'
        ordering = ['-actualizado']


ACCION_CHOICES = (
    ('MENU', 'Navegó a un menú (o volver / inicio)'),
    ('RESPUESTA', 'Se respondió un texto'),
    ('DERIVACION', 'Se derivó a un equipo'),
    ('TERMINAR', 'Terminó la conversación'),
    ('FUERA_HORARIO', 'Pidió un operador fuera de horario de atención'),
    ('INVALIDA', 'Opción no reconocida'),
    ('IGNORADO', 'Evento ignorado (no era mensaje de texto entrante)'),
    ('CONVERSACION', 'Evento de conversación (creada / cambió de estado)'),
    ('MSG_FALLIDO', 'Un mensaje saliente falló (message_updated status=failed)'),
    ('REACTIVADA', 'El contacto volvió a escribir tras derivar/terminar: se reactivó el menú'),
    ('BIENVENIDA', 'Primer contacto: saludo de bienvenida + menú'),
    ('INACTIVIDAD', 'Cerrada por inactividad'),
    ('PAUSADO', 'Llegó un mensaje con el bot pausado desde el panel'),
)


class ConversacionLog(models.Model):
    """Auditoría turno a turno de cada conversación (qué mensaje llegó, qué
    se hizo, y qué etiquetas se aplicaron)."""
    conversacion = models.ForeignKey(
        Conversacion, on_delete=models.CASCADE, related_name='logs', null=True, blank=True,
    )
    mensaje_recibido = models.TextField(blank=True)
    opcion = models.ForeignKey(
        MenuOpcion, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    accion = models.CharField(max_length=15, choices=ACCION_CHOICES)
    detalle = models.TextField(blank=True, help_text='Ej: etiquetas aplicadas, errores de la API de chatealo.')
    fecha = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.fecha} - {self.conversacion_id} - {self.accion}'

    class Meta:
        verbose_name = 'Log de conversación'
        verbose_name_plural = 'Logs de conversación'
        ordering = ['-fecha']


DIA_SEMANA_CHOICES = (
    (0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'),
    (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo'),
)


class HorarioAtencion(models.Model):
    """Franja horaria semanal recurrente en la que un operador está
    disponible para recibir derivaciones. Puede haber varias franjas por día
    (ej. mañana y tarde)."""
    dia_semana = models.IntegerField(choices=DIA_SEMANA_CHOICES, verbose_name='Día')
    hora_inicio = models.TimeField(verbose_name='Desde')
    hora_fin = models.TimeField(verbose_name='Hasta')
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.get_dia_semana_display()} {self.hora_inicio:%H:%M}-{self.hora_fin:%H:%M}'

    class Meta:
        verbose_name = 'Horario de atención'
        verbose_name_plural = 'Horarios de atención'
        ordering = ['dia_semana', 'hora_inicio']
        permissions = [
            ('gestionar_horarios_chatbot', 'Configurar horarios y días excepcionales de atención de Chatbot'),
        ]


TIPO_DIA_EXCEPCIONAL_CHOICES = (
    ('FERIADO', 'Feriado — cerrado todo el día'),
    ('ASUETO', 'Asueto — cerrado todo el día'),
    ('HORARIO_REDUCIDO', 'Horario reducido'),
)


class DiaExcepcional(models.Model):
    """Excepción de calendario a los horarios semanales: feriados, asuetos u
    horario reducido para una fecha puntual."""
    fecha = models.DateField(unique=True)
    tipo = models.CharField(max_length=20, choices=TIPO_DIA_EXCEPCIONAL_CHOICES, default='FERIADO')
    hora_inicio = models.TimeField(null=True, blank=True, help_text='Solo para horario reducido.')
    hora_fin = models.TimeField(null=True, blank=True, help_text='Solo para horario reducido.')
    descripcion = models.CharField(max_length=200, blank=True, verbose_name='Descripción')
    sincronizado = models.BooleanField(
        default=False, editable=False,
        help_text='True si esta fila la generó la sincronización automática de feriados nacionales.',
    )

    def __str__(self):
        return f'{self.fecha} - {self.get_tipo_display()}'

    class Meta:
        verbose_name = 'Día excepcional'
        verbose_name_plural = 'Días excepcionales'
        ordering = ['fecha']


TIPO_FUENTE_CHOICES = (
    ('WHATSAPP', 'WhatsApp'),
    ('WEBCHAT', 'Webchat'),
    ('TELEGRAM', 'Telegram'),
    ('FACEBOOK', 'Facebook'),
    ('INSTAGRAM', 'Instagram'),
    ('OTRO', 'Otro'),
)

# Mapa parcial nombre-de-Channel-de-Chatwoot -> tipo de fuente. Chatealo manda
# WhatsApp como 'Channel::Api' (no 'Channel::Whatsapp'), así que 'Api' queda sin
# sugerencia a propósito: lo tiene que confirmar una persona.
_CHANNEL_A_FUENTE = (
    ('whatsapp', 'WHATSAPP'),
    ('webwidget', 'WEBCHAT'),
    ('telegram', 'TELEGRAM'),
    ('instagram', 'INSTAGRAM'),
    ('facebook', 'FACEBOOK'),
)


class InboxChatealo(models.Model):
    """Cada bandeja de entrada de la cuenta de chatealo que le pega al webhook.
    El webhook es global para TODA la cuenta (dispara con eventos de cualquier
    bandeja), así que acá se van registrando solas a medida que llegan eventos
    y una persona les asocia un nombre y el tipo de fuente desde el panel."""
    inbox_id = models.PositiveIntegerField(unique=True, verbose_name='Inbox ID de chatealo')
    nombre = models.CharField(
        max_length=120, blank=True, verbose_name='Nombre',
        help_text='Nombre con el que se identifica esta bandeja en el panel.',
    )
    tipo_fuente = models.CharField(
        max_length=12, blank=True, choices=TIPO_FUENTE_CHOICES, verbose_name='Tipo de fuente',
    )
    responder_bot = models.BooleanField(
        default=True,
        help_text='Si el bot debe responder a los eventos de esta bandeja. '
                   '(El filtrado por bandeja todavía no está aplicado en el webhook.)',
    )

    # Datos detectados solos del payload del webhook (no se editan a mano).
    nombre_detectado = models.CharField(max_length=200, blank=True, editable=False)
    channel_detectado = models.CharField(max_length=100, blank=True, editable=False)
    account_id = models.PositiveIntegerField(null=True, blank=True, editable=False)
    eventos_recibidos = models.PositiveIntegerField(default=0, editable=False)
    primer_evento = models.DateTimeField(auto_now_add=True)
    ultimo_evento = models.DateTimeField(null=True, blank=True, editable=False)

    @property
    def tipo_fuente_sugerido(self):
        low = (self.channel_detectado or '').lower()
        for fragmento, fuente in _CHANNEL_A_FUENTE:
            if fragmento in low:
                return fuente
        return ''

    @classmethod
    def registrar_desde_payload(cls, inbox_id, nombre_detectado='', channel_detectado='', account_id=None):
        """Alta/actualización idempotente al recibir un evento del webhook.
        Nunca levanta excepción: si algo falla, se loguea y sigue."""
        if not inbox_id:
            return None
        try:
            obj, _creado = cls.objects.get_or_create(inbox_id=inbox_id)
            campos = {}
            if nombre_detectado and nombre_detectado != obj.nombre_detectado:
                campos['nombre_detectado'] = nombre_detectado
            if channel_detectado and channel_detectado != obj.channel_detectado:
                campos['channel_detectado'] = channel_detectado
            if account_id and account_id != obj.account_id:
                campos['account_id'] = account_id
            cls.objects.filter(pk=obj.pk).update(
                eventos_recibidos=models.F('eventos_recibidos') + 1,
                ultimo_evento=timezone.now(),
                **campos,
            )
            return obj
        except Exception:
            logger.exception('No se pudo registrar la inbox %s del webhook', inbox_id)
            return None

    def __str__(self):
        return self.nombre or self.nombre_detectado or f'Inbox #{self.inbox_id}'

    class Meta:
        verbose_name = 'Bandeja de chatealo'
        verbose_name_plural = 'Bandejas de chatealo'
        ordering = ['nombre', 'inbox_id']
        permissions = [
            ('gestionar_inboxes_chatbot', 'Asociar nombre y tipo de fuente a las bandejas de Chatbot'),
        ]
