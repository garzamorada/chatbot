import requests

URL = 'https://api.argentinadatos.com/v1/feriados/{anio}'
TIMEOUT = 5


def obtener_feriados(anio):
    """Lista de {'fecha': 'YYYY-MM-DD', 'tipo': ..., 'nombre': ...} para ese año."""
    r = requests.get(URL.format(anio=anio), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()
