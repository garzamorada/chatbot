from django.contrib import admin

from .models import UserLog


@admin.register(UserLog)
class UserLogAdmin(admin.ModelAdmin):
    list_display = ["fecha", "usuario", "accion"]
    list_filter = ["fecha"]
    search_fields = ["accion", "usuario__username"]
    readonly_fields = ["fecha", "usuario", "accion"]

    def has_add_permission(self, request):
        return False
