from django import forms
from django.core.validators import FileExtensionValidator

from . import defaults
from .models import ArchivoChatbot, ConfiguracionChatbot, DiaExcepcional, InboxChatealo, MenuOpcion


class DefaultsMixin:
    """Para los campos de texto opcionales que tienen un valor por defecto:
    muestra el default en el form cuando el campo está vacío, y al guardar lo
    vuelve a dejar en blanco si el texto no se modificó (así sigue 'atado' al
    default y se actualiza solo si cambia en el código)."""
    DEFAULTS = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo, texto in self.DEFAULTS.items():
            if campo in self.fields and not (getattr(self.instance, campo, '') or '').strip():
                self.initial[campo] = texto

    def clean(self):
        cleaned = super().clean()
        for campo, texto in self.DEFAULTS.items():
            if campo in cleaned and (cleaned.get(campo) or '').strip() == texto.strip():
                cleaned[campo] = ''
        return cleaned

EXTENSIONES_PERMITIDAS = [
    'jpg', 'jpeg', 'png', 'gif', 'webp',
    'xls', 'xlsx', 'csv', 'ods',
    'doc', 'docx', 'odt', 'txt',
    'pdf',
]


class MenuOpcionForm(forms.ModelForm):
    class Meta:
        model = MenuOpcion
        # 'orden' no se edita a mano: se define arrastrando en el árbol.
        fields = [
            'texto', 'slug', 'parent', 'tipo',
            'respuesta_texto',
            'mensaje_derivacion',
            'archivo', 'activo',
        ]
        widgets = {
            'respuesta_texto': forms.Textarea(attrs={'rows': 4}),
            'mensaje_derivacion': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = MenuOpcion.objects.filter(tipo='SUBMENU')
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields['parent'].queryset = qs
        self.fields['parent'].empty_label = 'Menú principal'


class ArchivoChatbotForm(forms.ModelForm):
    archivo = forms.FileField(
        validators=[FileExtensionValidator(allowed_extensions=EXTENSIONES_PERMITIDAS)],
    )

    class Meta:
        model = ArchivoChatbot
        fields = ['nombre', 'archivo']


class ConfiguracionChatbotForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionChatbot
        fields = [
            'chatealo_base_url', 'chatealo_account_id',
            'chatealo_authorization', 'chatealo_admin_id', 'chatealo_user_token',
            'webhook_firma_secret', 'webhook_firma_enforce',
        ]
        widgets = {
            'chatealo_authorization': forms.PasswordInput(render_value=True),
            'chatealo_admin_id': forms.PasswordInput(render_value=True),
            'chatealo_user_token': forms.PasswordInput(render_value=True),
            'webhook_firma_secret': forms.PasswordInput(render_value=True),
        }


class DespedidaChatbotForm(DefaultsMixin, forms.ModelForm):
    DEFAULTS = {'mensaje_despedida': defaults.MENSAJE_DESPEDIDA}

    class Meta:
        model = ConfiguracionChatbot
        fields = ['mensaje_despedida', 'archivo_despedida']
        widgets = {
            'mensaje_despedida': forms.Textarea(attrs={'rows': 3}),
        }


class SaludosChatbotForm(DefaultsMixin, forms.ModelForm):
    DEFAULTS = {
        'mensaje_bienvenida': defaults.MENSAJE_BIENVENIDA,
        'plantilla_saludo_inicial': defaults.SALUDO_INICIAL,
        'plantilla_saludo_area': defaults.SALUDO_AREA,
    }

    class Meta:
        model = ConfiguracionChatbot
        fields = ['mensaje_bienvenida', 'plantilla_saludo_inicial', 'plantilla_saludo_area']
        widgets = {
            'mensaje_bienvenida': forms.Textarea(attrs={'rows': 2}),
            'plantilla_saludo_inicial': forms.Textarea(attrs={'rows': 2}),
            'plantilla_saludo_area': forms.Textarea(attrs={'rows': 2}),
        }


class InboxChatealoForm(forms.ModelForm):
    class Meta:
        model = InboxChatealo
        fields = ['nombre', 'tipo_fuente', 'responder_bot']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'tipo_fuente': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'responder_bot': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class TiemposChatbotForm(DefaultsMixin, forms.ModelForm):
    DEFAULTS = {'mensaje_cierre_inactividad': defaults.MENSAJE_CIERRE_INACTIVIDAD}

    class Meta:
        model = ConfiguracionChatbot
        fields = ['segundos_remostrar_menu', 'minutos_inactividad_cierre', 'mensaje_cierre_inactividad']
        widgets = {
            'segundos_remostrar_menu': forms.NumberInput(attrs={'min': 0, 'class': 'form-control form-control-sm', 'style': 'max-width:120px'}),
            'minutos_inactividad_cierre': forms.NumberInput(attrs={'min': 0, 'class': 'form-control form-control-sm', 'style': 'max-width:120px'}),
            'mensaje_cierre_inactividad': forms.Textarea(attrs={'rows': 2}),
        }


class DiaExcepcionalRapidoForm(forms.ModelForm):
    """Alta rápida de un feriado/asueto puntual: sin pasar por la grilla de
    horas (esa sigue existiendo para horario reducido)."""
    tipo = forms.ChoiceField(
        choices=[('FERIADO', 'Feriado'), ('ASUETO', 'Asueto')], label='Tipo',
        widget=forms.Select(attrs={'class': 'form-select form-select-sm'}),
    )

    class Meta:
        model = DiaExcepcional
        fields = ['fecha', 'tipo', 'descripcion']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'descripcion': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
        }
