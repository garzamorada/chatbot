from django.contrib import admin

from .models import (
    ArchivoChatbot, ConfiguracionChatbot, Conversacion, ConversacionLog,
    DiaExcepcional, HorarioAtencion, InboxChatealo, MenuOpcion,
)


@admin.register(ConfiguracionChatbot)
class ConfiguracionChatbotAdmin(admin.ModelAdmin):
    list_display = ['chatealo_account_id', 'chatealo_base_url', 'activo', 'creado', 'ultimo_uso']
    readonly_fields = ['webhook_secret', 'creado', 'actualizado', 'ultimo_uso']

    def has_add_permission(self, request):
        return not ConfiguracionChatbot.objects.exists()


@admin.register(MenuOpcion)
class MenuOpcionAdmin(admin.ModelAdmin):
    list_display = ['texto', 'slug', 'parent', 'tipo', 'orden', 'activo']
    list_filter = ['tipo', 'activo']
    search_fields = ['texto', 'slug']


@admin.register(ArchivoChatbot)
class ArchivoChatbotAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'slug', 'archivo', 'subido_por', 'fecha_subida']
    readonly_fields = ['slug']
    search_fields = ['nombre', 'slug']


class ConversacionLogInline(admin.TabularInline):
    model = ConversacionLog
    extra = 0
    readonly_fields = ['mensaje_recibido', 'opcion', 'accion', 'detalle', 'fecha']
    can_delete = False


@admin.register(Conversacion)
class ConversacionAdmin(admin.ModelAdmin):
    list_display = [
        'conversation_id', 'contacto', 'nombre_contacto', 'inbox_id', 'estado',
        'menu_actual', 'label_equipo_actual', 'finalizado', 'actualizado',
    ]
    list_filter = ['finalizado', 'estado', 'inbox_id']
    search_fields = ['conversation_id', 'contacto', 'nombre_contacto']
    readonly_fields = [
        'conversation_id', 'account_id', 'inbox_id', 'contacto', 'nombre_contacto', 'estado',
        'menu_actual', 'label_equipo_actual', 'finalizado', 'ultimo_message_id', 'creado', 'actualizado',
    ]
    inlines = [ConversacionLogInline]

    def has_add_permission(self, request):
        return False


@admin.register(ConversacionLog)
class ConversacionLogAdmin(admin.ModelAdmin):
    list_display = ['conversacion', 'accion', 'opcion', 'fecha']
    list_filter = ['accion', 'fecha']
    readonly_fields = ['conversacion', 'mensaje_recibido', 'opcion', 'accion', 'detalle', 'fecha']

    def has_add_permission(self, request):
        return False


@admin.register(InboxChatealo)
class InboxChatealoAdmin(admin.ModelAdmin):
    list_display = [
        'inbox_id', 'nombre', 'tipo_fuente', 'responder_bot',
        'nombre_detectado', 'channel_detectado', 'eventos_recibidos', 'ultimo_evento',
    ]
    list_filter = ['tipo_fuente', 'responder_bot']
    search_fields = ['inbox_id', 'nombre', 'nombre_detectado']
    readonly_fields = [
        'inbox_id', 'nombre_detectado', 'channel_detectado', 'account_id',
        'eventos_recibidos', 'primer_evento', 'ultimo_evento',
    ]

    def has_add_permission(self, request):
        return False


@admin.register(HorarioAtencion)
class HorarioAtencionAdmin(admin.ModelAdmin):
    list_display = ['dia_semana', 'hora_inicio', 'hora_fin', 'activo']
    list_filter = ['dia_semana', 'activo']


@admin.register(DiaExcepcional)
class DiaExcepcionalAdmin(admin.ModelAdmin):
    list_display = ['fecha', 'tipo', 'hora_inicio', 'hora_fin', 'descripcion']
    list_filter = ['tipo']
    search_fields = ['descripcion']
