"""
Chat con los datos: la gerente pregunta en español y el modelo responde en texto.

Cómo se conecta el modelo a los datos
-------------------------------------
No se le deja generar ni ejecutar código: eso sería darle permiso de correr
cualquier cosa sobre el servidor a cambio de muy poco. En vez de eso se le pasa
**la tabla de alertas ya calculada** dentro del prompt y se le pide que responda
solo con eso.

Es la opción correcta acá porque el dataset es chico (unas 90 líneas, ~3.000
tokens): entra entero en el contexto. Los números que ve el modelo son exactamente
los que calculó `logica.py`, así que el chat no puede contradecir al dashboard.
Si mañana fueran 50.000 líneas habría que resumir o buscar antes de preguntar.

Usa la API de Groq (free tier, sin tarjeta) por HTTP directo, sin SDK: una
dependencia menos que pueda romper el despliegue. `requests` ya viene con Streamlit.

Como el resto de la lógica, este módulo no importa Streamlit: la clave se recibe
por parámetro.
"""

from __future__ import annotations

import pandas as pd
import requests

import logica as L

URL_CHAT = "https://api.groq.com/openai/v1/chat/completions"
URL_MODELOS = "https://api.groq.com/openai/v1/models"

# Modelo por defecto. Se puede cambiar sin tocar código con la clave
# `GROQ_MODELO` en los secrets, por si Groq retira este del free tier.
MODELO_POR_DEFECTO = "llama-3.3-70b-versatile"

TIMEOUT_SEGUNDOS = 45
MAX_TOKENS_RESPUESTA = 700
TEMPERATURA = 0.2  # baja: se quiere precisión sobre los números, no creatividad

COLUMNAS_CONTEXTO = [
    "sucursal", "nombre", "proveedor", "tipo", "formatos_pedidos",
    "formatos_recomendados", "delta_formatos", "formato_compra",
    "es_perecedero", "consumo_proyectado", "stock_actual", "unidad_base",
]

# Encabezados en castellano: si el modelo solo ve nombres legibles, no puede
# contestarle a la gerente con "el delta_formatos es -7".
NOMBRES_LEGIBLES = {
    "nombre": "insumo",
    "tipo": "alerta",
    "formatos_pedidos": "pidio (formatos)",
    "formatos_recomendados": "necesita (formatos)",
    "delta_formatos": "diferencia (formatos)",
    "formato_compra": "formato",
    "es_perecedero": "perecedero",
    "consumo_proyectado": "consumo proyectado (semana)",
    "stock_actual": "stock actual",
    "unidad_base": "unidad",
}

INSTRUCCIONES = """\
Sos el asistente de la gerente de compras de Barrio Pizza, una cadena de pizzerías \
en Panamá. Respondés en español rioplatense neutro, en tono directo y breve.

Reglas:
- Respondé ÚNICAMENTE con los datos de la tabla que viene abajo. No inventes \
sucursales, insumos ni cantidades.
- Si la respuesta no está en los datos, decilo con claridad en una línea y sugerí \
qué se podría mirar en el dashboard.
- Las cantidades se piden en FORMATOS (sacos, cajas, latas). Cuando des un número \
aclarás de qué formato hablás.
- "diferencia (formatos)" es lo pedido menos lo que necesita: positivo = pidió de \
más, negativo = se va a quedar corto.
- La columna "alerta" ya viene en castellano ("Riesgo de quiebre", "Olvido", \
"Exceso", "Sin historial", "No verificable", "OK"). Usá esas palabras tal cual; \
nunca escribas códigos internos en mayúsculas.
- NUNCA menciones nombres de columnas ni de variables. Hablás como le hablarías a \
la gerente: "a Costa del Este le faltan 7 sacos de harina", no "el campo \
diferencia es -7".
- Si la pregunta es sobre plata o precios, aclarás que el sistema todavía no tiene \
los costos cargados.
- Nada de tablas markdown largas ni listas de más de 6 puntos: la gerente quiere \
la respuesta, no un reporte.
"""


class ErrorChat(Exception):
    """Falla al consultar el modelo, con un mensaje que se le puede mostrar al usuario."""


def contexto_de_alertas(alertas: pd.DataFrame, metodo: str = L.METODO_PROMEDIO) -> str:
    """La tabla de alertas en CSV, lista para meter en el prompt."""
    if alertas.empty:
        return "No hay ninguna línea de orden para analizar."

    columnas = [c for c in COLUMNAS_CONTEXTO if c in alertas.columns]
    tabla = alertas[columnas].copy()
    for columna in ("consumo_proyectado", "stock_actual"):
        if columna in tabla.columns:
            tabla[columna] = tabla[columna].round(1)
    # El modelo no ve los códigos internos, así que no los puede repetir en la
    # respuesta: la gerente no tiene por qué leer "PIDE_MENOS".
    if "tipo" in tabla.columns:
        tabla["tipo"] = tabla["tipo"].map(L.ETIQUETAS_TIPO).fillna(tabla["tipo"])
    tabla = tabla.rename(columns=NOMBRES_LEGIBLES)

    resumen = L.resumen_kpis(alertas)
    encabezado = (
        f"Método de proyección en uso: {L.ETIQUETAS_METODO.get(metodo, metodo)}.\n"
        f"Líneas revisadas: {len(alertas)}. "
        f"Alertas: {resumen['total_alertas']} "
        f"({resumen['quiebres']} quiebres, {resumen['excesos']} excesos, "
        f"{resumen['olvidos']} olvidos, {resumen['no_verificables']} no verificables).\n"
    )
    return f"{encabezado}\nTabla de líneas (CSV):\n{tabla.to_csv(index=False)}"


def _traducir_error(respuesta: requests.Response) -> str:
    if respuesta.status_code == 401:
        return "La clave de Groq no es válida o expiró. Revisala en los secrets de la app."
    if respuesta.status_code == 429:
        return ("Se agotó la cuota gratuita de Groq por ahora. "
                "Esperá unos minutos y volvé a intentar.")
    if respuesta.status_code == 404:
        return ("El modelo configurado ya no existe en Groq. "
                "Cambiá `GROQ_MODELO` en los secrets por uno vigente.")
    try:
        detalle = respuesta.json().get("error", {}).get("message", "")
    except ValueError:
        detalle = respuesta.text[:200]
    return f"Groq respondió {respuesta.status_code}: {detalle}"


def preguntar(pregunta: str,
              alertas: pd.DataFrame,
              api_key: str,
              metodo: str = L.METODO_PROMEDIO,
              modelo: str = MODELO_POR_DEFECTO,
              historial: list[dict] | None = None) -> str:
    """Le hace la pregunta al modelo con las alertas como contexto."""
    if not api_key:
        raise ErrorChat("Falta la clave de Groq.")
    if not pregunta.strip():
        raise ErrorChat("La pregunta está vacía.")

    mensajes = [{"role": "system",
                 "content": f"{INSTRUCCIONES}\n\n{contexto_de_alertas(alertas, metodo)}"}]
    # Solo los últimos intercambios: alcanza para dar continuidad sin inflar el
    # prompt (el contexto de datos ya ocupa lo suyo).
    for mensaje in (historial or [])[-6:]:
        if mensaje.get("role") in ("user", "assistant") and mensaje.get("content"):
            mensajes.append({"role": mensaje["role"], "content": mensaje["content"]})
    mensajes.append({"role": "user", "content": pregunta.strip()})

    try:
        respuesta = requests.post(
            URL_CHAT,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": modelo, "messages": mensajes,
                  "temperature": TEMPERATURA, "max_tokens": MAX_TOKENS_RESPUESTA},
            timeout=TIMEOUT_SEGUNDOS,
        )
    except requests.Timeout as error:
        raise ErrorChat("El modelo tardó demasiado en responder.") from error
    except requests.RequestException as error:
        raise ErrorChat(f"No se pudo conectar con Groq: {error}") from error

    if respuesta.status_code != 200:
        raise ErrorChat(_traducir_error(respuesta))

    try:
        return respuesta.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as error:
        raise ErrorChat("Groq devolvió una respuesta que no se pudo leer.") from error


def modelos_disponibles(api_key: str) -> list[str]:
    """Los modelos que la clave puede usar. Sirve para diagnosticar un 404."""
    try:
        respuesta = requests.get(
            URL_MODELOS,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if respuesta.status_code != 200:
            return []
        return sorted(m["id"] for m in respuesta.json().get("data", []))
    except (requests.RequestException, ValueError, KeyError):
        return []


PREGUNTAS_SUGERIDAS = [
    "¿Qué sucursal está pidiendo demasiado queso?",
    "¿Dónde me puedo quedar sin producto esta semana?",
    "¿Qué perecederos están pedidos de más?",
    "Resumime en 3 líneas qué tengo que corregir hoy.",
]
