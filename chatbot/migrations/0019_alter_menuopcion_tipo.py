from django.db import migrations, models


def _borrar_nav_cargadas(apps, schema_editor):
    """Las opciones VOLVER / INICIO / TERMINAR ahora las agrega el bot solo al
    pie de cada menú; se quitan las que estaban cargadas a mano."""
    MenuOpcion = apps.get_model('chatbot', 'MenuOpcion')
    MenuOpcion.objects.filter(tipo__in=['VOLVER', 'INICIO', 'TERMINAR']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0018_menuopcion_boton_texto_menuopcion_boton_url'),
    ]

    operations = [
        migrations.RunPython(_borrar_nav_cargadas, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='menuopcion',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('SUBMENU', 'Menú — agrupa otras opciones'),
                    ('RESPUESTA', 'Respuesta directa — el bot contesta un texto'),
                    ('DERIVACION', 'Derivación — se deriva a un agente/área'),
                ],
                default='SUBMENU', max_length=12, verbose_name='Tipo',
            ),
        ),
    ]
