"""Crea el grupo `admin_chatbot` con todos los permisos para administrar el
chatbot (mismo patrón que `admin_catalogo`, `admin_sorteo`, etc.)."""
from django.db import migrations

GRUPO = 'admin_chatbot'

PERMISOS_CHATBOT = [
    'ver_panel_chatbot',
    'gestionar_menu_chatbot',
    'gestionar_token_chatbot',
    'gestionar_archivos_chatbot',
    'gestionar_horarios_chatbot',
    'gestionar_inboxes_chatbot',
]


def _asegurar_permisos_creados():
    # `create_permissions` de post_migrate corre recién al final del `migrate`;
    # si esta migración corre en un alta desde cero, los permisos custom todavía
    # no existen. Se fuerza su creación acá.
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    app_config = global_apps.get_app_config('chatbot')
    create_permissions(app_config, verbosity=0)


def crear_grupo(apps, schema_editor):
    _asegurar_permisos_creados()
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    grupo, _ = Group.objects.get_or_create(name=GRUPO)
    permisos = Permission.objects.filter(
        codename__in=PERMISOS_CHATBOT, content_type__app_label='chatbot',
    )
    grupo.permissions.add(*permisos)


def borrar_grupo(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name=GRUPO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0013_configuracionchatbot_mensaje_cierre_inactividad_and_more'),
        ('auth', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(crear_grupo, borrar_grupo),
    ]
