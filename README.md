# Chatbot

Asistente de atención por **WhatsApp** basado en un menú de opciones, integrado con
[chatealo.io](https://chatealo.io) (plataforma tipo Chatwoot). Cuando un contacto
escribe, el bot le muestra un listado numerado; según lo que elija, le responde un
texto, lo lleva a otro menú o lo deriva a una persona.

> **Este repositorio es una copia de referencia.** El código sale de un sistema
> interno más grande y acá se publica aislado, para consulta y para poder
> levantarlo en un entorno propio con Docker. **No contiene ninguna credencial,
> URL ni dato real** — toda la configuración se toma de variables de entorno. La
> instancia que está en producción es otra y sigue funcionando por su cuenta.

---

## Qué incluye

| Área | Descripción |
|---|---|
| **Árbol de menú** | Editor visual drag-and-drop de opciones (Menú, Respuesta, Derivación, Volver, Inicio, Terminar), anidables y colapsables. |
| **Mensajes** | Saludos configurables (bienvenida, menú principal, cada menú), despedida y mensaje de cierre por inactividad, con editor de formato de WhatsApp, vista previa y variables `{nombre}` / `{area}`. |
| **Webhook + API de chatealo** | Recepción de eventos (`message_created`, `message_updated`, `conversation_*`), envío de respuestas y etiquetas, verificación de firma, alta/consulta del webhook desde el panel. |
| **Bandejas** | Autodetección de las bandejas de la cuenta y asignación de nombre + tipo de fuente. |
| **Horarios** | Grilla semanal de atención + días excepcionales (feriados/asuetos), con sincronización de feriados nacionales de Argentina. |
| **Temporizadores** | Comando que re-muestra el menú tras una respuesta y cierra conversaciones inactivas (pensado para correr por cron cada minuto). |
| **Auditoría** | Registro turno a turno de cada conversación y de cada cambio de configuración. |

Manual de uso navegable en `/chatbot/manual/` (fuente en `chatbot/docs/`).

---

## Stack

- **Django 5.1** · Python 3.12
- **MySQL 8.4**
- **Docker Compose** para levantar todo aislado

Dependencias de Python (`requirements.txt`): sólo `Django`, `requests` y
`mysqlclient`.

---

## Estructura

```
.
├── config/                 # proyecto Django (settings, urls, wsgi/asgi)
│   ├── settings.py          # todo por variables de entorno, sin secretos
│   └── settings_test.py     # SQLite en memoria para los tests
├── chatbot/                # LA APP
│   ├── models.py · views.py · api_views.py   # panel + webhook
│   ├── chatealo_client.py · feriados_client.py · firma.py
│   ├── forms.py · labels.py · horarios.py · defaults.py
│   ├── management/commands/procesar_temporizadores_chatbot.py
│   ├── migrations/ · templates/ · static/ · docs/
│   └── tests.py             # ~60 tests (unitarios + integración)
├── logs/                   # modelo de auditoría mínimo (UserLog)
├── templates/base.html     # layout neutro con Bootstrap 5 por CDN
├── Dockerfile · docker-compose.yml
├── requirements.txt · .env.example
```

**Usuarios:** se usa el modelo `User` nativo de Django. La app **`logs`** es un
reemplazo mínimo de un app de auditoría del sistema original (sólo el modelo
`UserLog`, que el panel usa para registrar los cambios de configuración). La app
`chatbot` sólo se tocó para genericar textos y rutas de documentación.

El `templates/base.html` es un layout neutro (Bootstrap 5 por CDN, sin logos ni
paleta propia); en el sistema original la app extiende el `base.html` de ese
sistema.

---

## Levantarlo con Docker

```bash
cp .env.example .env
# editar .env: al menos poner un DJANGO_SECRET_KEY

docker compose up --build          # levanta db + web
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Panel: <http://localhost:8000/chatbot/> · Admin: <http://localhost:8000/admin/>

Para que un usuario administre el chatbot, asignarle el grupo **`admin_chatbot`**
(lo crea la migración `0014`) o los permisos sueltos `ver_panel_chatbot`,
`gestionar_menu_chatbot`, `gestionar_token_chatbot`, `gestionar_archivos_chatbot`,
`gestionar_horarios_chatbot`, `gestionar_inboxes_chatbot`.

### Comandos útiles

```bash
# Tests
docker compose exec web python manage.py test chatbot --settings=config.settings_test

# Temporizadores (re-mostrar menú / cierre por inactividad) — correr por cron cada minuto
docker compose exec web python manage.py procesar_temporizadores_chatbot

docker compose exec web python manage.py collectstatic --noinput
```

Ejemplo de línea de cron (en el host, apuntando al contenedor):

```
* * * * * docker compose -f /ruta/al/repo/docker-compose.yml exec -T web python manage.py procesar_temporizadores_chatbot
```

---

## Configuración

Todo se define en `.env` (ver `.env.example`). Las credenciales de la API de
chatealo, la URL base y el secreto de firma del webhook se cargan **desde el
panel** (modelo `ConfiguracionChatbot`), no desde variables de entorno — así
quedan fuera del repositorio y del control de versiones.

| Variable | Para qué |
|---|---|
| `DJANGO_SECRET_KEY` | clave de Django (obligatoria) |
| `DJANGO_DEBUG` | `True` / `False` |
| `DJANGO_ALLOWED_HOSTS` | hosts separados por coma |
| `MYSQL_DATABASE` · `MYSQL_USER` · `MYSQL_PASSWORD` · `MYSQL_ROOT_PASSWORD` | base de datos |
| `DB_HOST` · `DB_PORT` | conexión a la base (por defecto `db:3306`) |

---

## Sin Docker (opcional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# apuntar DB_HOST a un MySQL propio, o usar SQLite cambiando config/settings.py
python manage.py migrate
python manage.py runserver
```

`mysqlclient` requiere los headers de MySQL/MariaDB instalados en el sistema.

---

## Licencia

Uso interno. Publicado como referencia de código.
