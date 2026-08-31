from django.urls import path

from . import api_views, views

app_name = 'chatbot'

urlpatterns = [
    # Panel front (login + permisos)
    path('', views.panel_chatbot, name='panel'),
    path('manual/', views.manual, name='manual'),
    path('toggle-bot/', views.toggle_bot, name='toggle_bot'),
    path('config/editar/', views.editar_config, name='editar_config'),
    path('config/despedida/', views.editar_despedida, name='editar_despedida'),
    path('config/saludos/', views.editar_saludos, name='editar_saludos'),
    path('config/tiempos/', views.editar_tiempos, name='editar_tiempos'),
    path('webhook-secret/regenerar/', views.regenerar_webhook_secret, name='regenerar_webhook_secret'),
    path('webhooks/listar/', views.webhooks_listar, name='webhooks_listar'),
    path('webhooks/registrar/', views.webhooks_registrar, name='webhooks_registrar'),
    path('inboxes/<int:pk>/editar/', views.editar_inbox, name='editar_inbox'),
    path('opciones/nueva/', views.gestionar_opcion, name='crear_opcion'),
    path('opciones/<int:pk>/editar/', views.gestionar_opcion, name='editar_opcion'),
    path('opciones/<int:pk>/eliminar/', views.eliminar_opcion, name='eliminar_opcion'),
    path('opciones/reordenar/', views.reordenar_opciones, name='reordenar_opciones'),
    path('archivos/', views.lista_archivos, name='lista_archivos'),
    path('archivos/subir/', views.subir_archivo, name='subir_archivo'),
    path('archivos/<int:pk>/eliminar/', views.eliminar_archivo, name='eliminar_archivo'),
    path('conversaciones/', views.lista_conversaciones, name='lista_conversaciones'),
    path('conversaciones/<int:pk>/', views.detalle_conversacion, name='detalle_conversacion'),
    path('horarios/', views.lista_horarios, name='lista_horarios'),
    path('horarios/guardar/', views.guardar_horarios_grilla, name='guardar_horarios_grilla'),
    path('calendario/', views.calendario_excepciones, name='calendario_excepciones'),
    path('calendario/feriado-rapido/', views.agregar_feriado_rapido, name='agregar_feriado_rapido'),
    path('calendario/guardar/', views.guardar_dias_especificos, name='guardar_dias_especificos'),
    path('calendario/<int:pk>/eliminar/', views.eliminar_dia_excepcional, name='eliminar_dia_excepcional'),

    # Webhook público que consume chatealo (protegido por el secreto en la URL)
    path('webhook/chatealo/<str:secret>/', api_views.webhook_chatealo, name='webhook_chatealo'),
]
