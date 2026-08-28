from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

urlpatterns = [
    path("", lambda request: redirect("chatbot:panel")),
    path("admin/", admin.site.urls),
    path("chatbot/", include("chatbot.urls")),
]
