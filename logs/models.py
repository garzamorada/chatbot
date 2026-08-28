"""Modelo de auditoría mínimo.

Stand-in del app `logs` del sistema original. La app `chatbot` sólo usa
`UserLog.objects.create(usuario=..., accion=...)` para dejar constancia de las
acciones de configuración del panel.
"""
from django.conf import settings
from django.db import models


class UserLog(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    fecha = models.DateTimeField(auto_now_add=True)
    accion = models.CharField(max_length=255)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "Registro de acción"
        verbose_name_plural = "Registros de acciones"

    def __str__(self):
        return f"{self.fecha:%Y-%m-%d %H:%M} · {self.accion}"
