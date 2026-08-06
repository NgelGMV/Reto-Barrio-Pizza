"""
Lógica de negocio del dashboard de órdenes de compra de Barrio Pizza.

Este módulo NO sabe nada de Streamlit: se puede importar y testear solo.
Responsabilidades:

1. Cargar y limpiar los 4 CSV de `datos/` (BOM, espacios, tipos, duplicados).
2. Convertir formatos de compra <-> unidad base usando el factor del catálogo.
3. Proyectar el consumo de la próxima semana (promedio simple o método inteligente).
4. Calcular la necesidad real y los formatos recomendados.
5. Clasificar cada par (sucursal, ingrediente) en un tipo de alerta y redactar
   la frase accionable que ve la gerente de compras.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Configuración (todo lo ajustable vive acá, no disperso en el código)
# ---------------------------------------------------------------------------

DIR_DATOS = Path(__file__).resolve().parent / "datos"
ENCODING_CSV = "utf-8-sig"  # los CSV del reto vienen con BOM

# Colchón de seguridad sobre el consumo proyectado. 0.0 = calzar exactamente con
# el enunciado. Subirlo (ej. 0.10) pide un 10% extra para absorber imprevistos.
BUFFER = 0.0

# Un punto es atípico si |x - mediana| > UMBRAL_MAD * MAD.
UMBRAL_MAD = 3.0

# Con menos de estos puntos limpios no vale la pena ajustar una recta.
MIN_PUNTOS_TENDENCIA = 3

# Una pendiente se acepta como tendencia real solo si cumple DOS condiciones:
#  (a) es económicamente relevante: el cambio semanal supera este % del nivel
#      medio de la serie (un +0.01 kg/semana no cambia ninguna decisión); y
#  (b) es estadísticamente distinguible de cero: |pendiente / error estándar|
#      supera este t. Con 5-6 puntos ruidosos casi cualquier serie plana da una
#      pendiente distinta de cero por azar; extrapolarla empeora la proyección.
UMBRAL_PENDIENTE_RELATIVA = 0.005  # 0.5% semanal
UMBRAL_T_PENDIENTE = 2.0

METODO_PROMEDIO = "promedio_simple"
METODO_INTELIGENTE = "inteligente"

ETIQUETAS_METODO = {
    METODO_PROMEDIO: "Promedio simple",
    METODO_INTELIGENTE: "Proyección inteligente",
}

# Tipos de alerta
PIDE_MENOS = "PIDE_MENOS"
OLVIDO = "OLVIDO"
PIDE_MAS = "PIDE_MAS"
SIN_HISTORIAL = "SIN_HISTORIAL"
DATO_RARO = "DATO_RARO"
OK = "OK"

# Orden de severidad: primero lo que rompe el servicio, último lo informativo.
SEVERIDAD = {
    PIDE_MENOS: 0,
    OLVIDO: 1,
    PIDE_MAS: 2,
    SIN_HISTORIAL: 3,
    DATO_RARO: 4,
    OK: 5,
}

# Solo estos tipos se cuentan como "alerta" en los KPIs; OK es ruido de fondo.
TIPOS_ALERTA = (PIDE_MENOS, OLVIDO, PIDE_MAS, SIN_HISTORIAL, DATO_RARO)

ETIQUETAS_TIPO = {
    PIDE_MENOS: "Riesgo de quiebre",
    OLVIDO: "Olvido",
    PIDE_MAS: "Exceso",
    SIN_HISTORIAL: "Sin historial",
    DATO_RARO: "No verificable",
    OK: "OK",
}

COLORES_TIPO = {
    PIDE_MENOS: "#d64545",  # rojo
    OLVIDO: "#e08a2e",      # naranja
    PIDE_MAS: "#e0c02e",    # amarillo
    SIN_HISTORIAL: "#5b8fc9",  # azul
    DATO_RARO: "#9aa0a6",   # gris
    OK: "#4c9a5c",          # verde
}

ICONOS_TIPO = {
    PIDE_MENOS: "🔴",
    OLVIDO: "🟠",
    PIDE_MAS: "🟡",
    SIN_HISTORIAL: "🔵",
    DATO_RARO: "⚪",
    OK: "🟢",
}

COLUMNAS_ALERTAS = [
    "sucursal",
    "ingrediente_id",
    "nombre",
    "proveedor",
    "unidad_base",
    "formato_compra",
    "unidad_base_por_formato",
    "es_perecedero",
    "consumo_proyectado",
    "stock_actual",
    "necesidad_base",
    "formatos_recomendados",
    "formatos_pedidos",
    "pedido_base",
    "delta_formatos",
    "tipo",
    "severidad",
    "mensaje",
    "detalle_proyeccion",
    "semanas_usadas",
    "semanas_descartadas",
]


# ---------------------------------------------------------------------------
# Carga y limpieza
# ---------------------------------------------------------------------------


@dataclass
class Datos:
    """Los 4 CSV ya limpios y normalizados."""

    catalogo: pd.DataFrame
    consumo: pd.DataFrame
    inventario: pd.DataFrame
    orden: pd.DataFrame
    avisos: list[str] = field(default_factory=list)


def _limpiar_texto(serie: pd.Series) -> pd.Series:
    """Pasa a str, saca espacios sobrantes y normaliza los vacíos a NaN."""
    limpia = serie.astype("string").str.strip()
    return limpia.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})


def _a_numero(serie: pd.Series) -> pd.Series:
    """Convierte a float tolerando basura ('12,5', ' 3 ', 'N/A' -> NaN)."""
    if serie.dtype == object or str(serie.dtype) == "string":
        serie = (
            serie.astype("string")
            .str.strip()
            .str.replace(",", ".", regex=False)
        )
    return pd.to_numeric(serie, errors="coerce")


def _leer_csv(ruta: Path, columnas_requeridas: Sequence[str]) -> pd.DataFrame:
    if not ruta.exists():
        raise FileNotFoundError(f"No se encontró el archivo de datos: {ruta}")
    df = pd.read_csv(ruta, encoding=ENCODING_CSV)
    df.columns = [str(c).strip() for c in df.columns]
    faltantes = [c for c in columnas_requeridas if c not in df.columns]
    if faltantes:
        raise ValueError(
            f"{ruta.name}: faltan columnas obligatorias {faltantes}. "
            f"Se encontraron: {list(df.columns)}"
        )
    return df


def limpiar_catalogo(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    avisos: list[str] = []
    df = df.copy()
    for col in ("ingrediente_id", "nombre", "proveedor", "unidad_base",
                "formato_compra", "es_perecedero"):
        df[col] = _limpiar_texto(df[col])
    df["unidad_base_por_formato"] = _a_numero(df["unidad_base_por_formato"])

    sin_id = df["ingrediente_id"].isna()
    if sin_id.any():
        avisos.append(f"Catálogo: {int(sin_id.sum())} fila(s) sin ingrediente_id, descartadas.")
        df = df[~sin_id]

    # Un factor nulo, cero o negativo haría imposible convertir formatos.
    factor_malo = df["unidad_base_por_formato"].isna() | (df["unidad_base_por_formato"] <= 0)
    if factor_malo.any():
        ids = ", ".join(df.loc[factor_malo, "ingrediente_id"].astype(str))
        avisos.append(f"Catálogo: factor de conversión inválido en [{ids}]; se tratan como no verificables.")
        df = df[~factor_malo]

    duplicados = df["ingrediente_id"].duplicated(keep="first")
    if duplicados.any():
        avisos.append(f"Catálogo: {int(duplicados.sum())} ingrediente_id duplicado(s); se usa la primera aparición.")
        df = df[~duplicados]

    df["es_perecedero_bool"] = (
        df["es_perecedero"].fillna("").str.lower().str[:1].isin(["s", "y", "1"])
    )
    df["nombre"] = df["nombre"].fillna(df["ingrediente_id"])
    df["unidad_base"] = df["unidad_base"].fillna("und")
    df["formato_compra"] = df["formato_compra"].fillna("formato")
    df["proveedor"] = df["proveedor"].fillna("Sin proveedor")
    return df.reset_index(drop=True), avisos


def _numero_de_semana(serie: pd.Series) -> pd.Series:
    """'S3' -> 3. Si no hay dígitos, deja NaN (se completa luego por orden)."""
    extraido = serie.astype("string").str.extract(r"(\d+)", expand=False)
    return pd.to_numeric(extraido, errors="coerce")


def limpiar_consumo(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    avisos: list[str] = []
    df = df.copy()
    for col in ("sucursal", "ingrediente_id", "semana"):
        df[col] = _limpiar_texto(df[col])
    df["consumo_unidad_base"] = _a_numero(df["consumo_unidad_base"])

    invalidas = df["sucursal"].isna() | df["ingrediente_id"].isna()
    if invalidas.any():
        avisos.append(f"Consumo: {int(invalidas.sum())} fila(s) sin sucursal o ingrediente, descartadas.")
        df = df[~invalidas]

    sin_valor = df["consumo_unidad_base"].isna()
    if sin_valor.any():
        avisos.append(f"Consumo: {int(sin_valor.sum())} fila(s) con consumo ilegible, descartadas.")
        df = df[~sin_valor]

    negativos = df["consumo_unidad_base"] < 0
    if negativos.any():
        avisos.append(f"Consumo: {int(negativos.sum())} valor(es) negativo(s), descartados.")
        df = df[~negativos]

    df["semana_num"] = _numero_de_semana(df["semana"])
    sin_numero = df["semana_num"].isna()
    if sin_numero.any():
        avisos.append(f"Consumo: {int(sin_numero.sum())} semana(s) sin número reconocible; se ordenan al final.")
        maximo = df["semana_num"].max()
        base = 0.0 if pd.isna(maximo) else float(maximo)
        df.loc[sin_numero, "semana_num"] = base + np.arange(1, int(sin_numero.sum()) + 1)

    # Semanas repetidas para el mismo par: se suman (dos registros del mismo consumo).
    agregado = (
        df.groupby(["sucursal", "ingrediente_id", "semana_num"], as_index=False)
        .agg(consumo_unidad_base=("consumo_unidad_base", "sum"),
             semana=("semana", "first"))
    )
    if len(agregado) != len(df):
        avisos.append(f"Consumo: {len(df) - len(agregado)} registro(s) duplicado(s) por semana; se sumaron.")
    return agregado.sort_values(["sucursal", "ingrediente_id", "semana_num"]).reset_index(drop=True), avisos


def limpiar_inventario(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    avisos: list[str] = []
    df = df.copy()
    for col in ("sucursal", "ingrediente_id"):
        df[col] = _limpiar_texto(df[col])
    df["stock_actual_unidad_base"] = _a_numero(df["stock_actual_unidad_base"])

    invalidas = df["sucursal"].isna() | df["ingrediente_id"].isna()
    if invalidas.any():
        avisos.append(f"Inventario: {int(invalidas.sum())} fila(s) sin sucursal o ingrediente, descartadas.")
        df = df[~invalidas]

    # Stock ilegible o negativo -> 0: es el supuesto conservador (asumir que no
    # hay stock puede hacer pedir de más, pero nunca provoca un quiebre).
    problemas = df["stock_actual_unidad_base"].isna() | (df["stock_actual_unidad_base"] < 0)
    if problemas.any():
        avisos.append(f"Inventario: {int(problemas.sum())} stock(s) ilegible(s) o negativo(s); se asumen 0.")
        df.loc[problemas, "stock_actual_unidad_base"] = 0.0

    agregado = df.groupby(["sucursal", "ingrediente_id"], as_index=False)[
        "stock_actual_unidad_base"
    ].sum()
    if len(agregado) != len(df):
        avisos.append(f"Inventario: {len(df) - len(agregado)} fila(s) duplicada(s); se sumó el stock.")
    return agregado, avisos


def limpiar_orden(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    avisos: list[str] = []
    df = df.copy()
    for col in ("sucursal", "ingrediente_id"):
        df[col] = _limpiar_texto(df[col])
    df["cantidad_formatos"] = _a_numero(df["cantidad_formatos"])

    invalidas = df["sucursal"].isna() | df["ingrediente_id"].isna()
    if invalidas.any():
        avisos.append(f"Orden: {int(invalidas.sum())} fila(s) sin sucursal o ingrediente, descartadas.")
        df = df[~invalidas]

    # Una cantidad ilegible o negativa NO se puede asumir como 0 (eso inventaría
    # un olvido que no existe): queda NaN y se reporta como no verificable.
    negativas = df["cantidad_formatos"] < 0
    if negativas.any():
        avisos.append(f"Orden: {int(negativas.sum())} cantidad(es) negativa(s); se marcan como no verificables.")
        df.loc[negativas, "cantidad_formatos"] = np.nan

    agregado = df.groupby(["sucursal", "ingrediente_id"], as_index=False).agg(
        cantidad_formatos=("cantidad_formatos", lambda s: s.sum(min_count=1)),
        lineas=("cantidad_formatos", "size"),
    )
    repetidas = int((agregado["lineas"] > 1).sum())
    if repetidas:
        avisos.append(f"Orden: {repetidas} ingrediente(s) repetido(s) en la misma sucursal; se sumaron las cantidades.")
    return agregado.drop(columns="lineas"), avisos


def cargar_datos(dir_datos: str | Path | None = None,
                 orden_df: pd.DataFrame | None = None) -> Datos:
    """Lee y limpia los 4 CSV.

    `orden_df` permite reemplazar la orden del CSV por una subida desde la UI.
    """
    carpeta = Path(dir_datos) if dir_datos is not None else DIR_DATOS

    catalogo_raw = _leer_csv(
        carpeta / "ingredientes.csv",
        ["ingrediente_id", "nombre", "proveedor", "unidad_base",
         "formato_compra", "unidad_base_por_formato", "es_perecedero"],
    )
    consumo_raw = _leer_csv(
        carpeta / "consumo_historico.csv",
        ["sucursal", "ingrediente_id", "semana", "consumo_unidad_base"],
    )
    inventario_raw = _leer_csv(
        carpeta / "inventario_actual.csv",
        ["sucursal", "ingrediente_id", "stock_actual_unidad_base"],
    )
    if orden_df is None:
        orden_raw = _leer_csv(
            carpeta / "orden_compra_semana.csv",
            ["sucursal", "ingrediente_id", "cantidad_formatos"],
        )
    else:
        orden_raw = orden_df.copy()
        orden_raw.columns = [str(c).strip() for c in orden_raw.columns]
        faltantes = [c for c in ("sucursal", "ingrediente_id", "cantidad_formatos")
                     if c not in orden_raw.columns]
        if faltantes:
            raise ValueError(f"La orden cargada no tiene las columnas {faltantes}.")

    catalogo, av1 = limpiar_catalogo(catalogo_raw)
    consumo, av2 = limpiar_consumo(consumo_raw)
    inventario, av3 = limpiar_inventario(inventario_raw)
    orden, av4 = limpiar_orden(orden_raw)

    return Datos(catalogo, consumo, inventario, orden, av1 + av2 + av3 + av4)


def leer_orden_subida(archivo) -> pd.DataFrame:
    """Lee un CSV de orden subido desde la UI (file-like o ruta)."""
    return pd.read_csv(archivo, encoding=ENCODING_CSV)


# ---------------------------------------------------------------------------
# Conversión de unidades
# ---------------------------------------------------------------------------


def formatos_a_base(cantidad_formatos: float, unidad_base_por_formato: float) -> float:
    """Formatos -> unidad base. El factor puede ser decimal (lata de 2.55 kg)."""
    return float(cantidad_formatos) * float(unidad_base_por_formato)


def base_a_formatos(cantidad_base: float, unidad_base_por_formato: float) -> int:
    """Unidad base -> formatos COMPLETOS.

    Se usa `ceil` porque no existe medio saco: hay que comprar el formato entero.
    Como consecuencia, todo excedente menor a un formato es redondeo normal y no
    debe reportarse como sobre-pedido (ver `clasificar`).
    """
    factor = float(unidad_base_por_formato)
    if not np.isfinite(factor) or factor <= 0:
        raise ValueError("El factor de conversión debe ser un número positivo.")
    return int(math.ceil(float(cantidad_base) / factor))


# ---------------------------------------------------------------------------
# Proyección del consumo de la próxima semana
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Proyeccion:
    valor: float
    metodo: str
    semanas_usadas: tuple[float, ...]
    semanas_descartadas: tuple[float, ...]
    pendiente: float | None
    detalle: str


def _promedio(valores: np.ndarray) -> float:
    return float(np.mean(valores)) if valores.size else 0.0


def detectar_outliers(valores: Sequence[float], umbral: float = UMBRAL_MAD) -> np.ndarray:
    """Máscara booleana: True = punto atípico.

    Distancia a la mediana en unidades de MAD (desviación absoluta mediana).
    Se usa mediana/MAD y no media/desvío porque el propio outlier contamina la
    media: una semana de 150 sobre un consumo de 30 movería el desvío tanto que
    dejaría de ser detectable. Si MAD = 0 (serie casi constante) no se descarta
    nada, para no borrar media serie por diferencias de decimales.
    """
    arr = np.asarray(valores, dtype=float)
    if arr.size == 0:
        return np.zeros(0, dtype=bool)
    mediana = float(np.median(arr))
    mad = float(np.median(np.abs(arr - mediana)))
    if mad <= 0:
        return np.zeros(arr.size, dtype=bool)
    return np.abs(arr - mediana) > umbral * mad


def _pendiente_es_tendencia(x: np.ndarray, y: np.ndarray, pendiente: float,
                            ordenada: float, nivel: float) -> bool:
    """¿La pendiente ajustada es una tendencia real o ruido?

    Ver UMBRAL_PENDIENTE_RELATIVA / UMBRAL_T_PENDIENTE: tiene que mover la aguja
    en términos de negocio Y no ser explicable por el ruido de la serie.
    """
    if abs(pendiente) < UMBRAL_PENDIENTE_RELATIVA * max(abs(nivel), 1e-9):
        return False

    grados_libertad = y.size - 2
    sxx = float(np.sum((x - x.mean()) ** 2))
    if grados_libertad <= 0 or sxx <= 0:
        return False

    residuos = y - (pendiente * x + ordenada)
    sse = float(np.sum(residuos ** 2))
    if sse <= 1e-12:
        return True  # ajuste perfecto: la tendencia es incuestionable

    error_pendiente = math.sqrt(sse / grados_libertad / sxx)
    return abs(pendiente) / error_pendiente >= UMBRAL_T_PENDIENTE


def proyectar(semanas: Sequence[float],
              consumos: Sequence[float],
              metodo: str = METODO_PROMEDIO,
              umbral_mad: float = UMBRAL_MAD) -> Proyeccion:
    """Proyecta el consumo de la semana siguiente a la última observada."""
    x = np.asarray(semanas, dtype=float)
    y = np.asarray(consumos, dtype=float)
    validos = np.isfinite(x) & np.isfinite(y)
    x, y = x[validos], y[validos]

    if y.size == 0:
        return Proyeccion(0.0, metodo, (), (), None, "Sin histórico de consumo.")

    if metodo == METODO_PROMEDIO:
        return Proyeccion(
            valor=_promedio(y),
            metodo=metodo,
            semanas_usadas=tuple(x.tolist()),
            semanas_descartadas=(),
            pendiente=None,
            detalle=f"Promedio de {y.size} semana{'s' if y.size != 1 else ''} de histórico.",
        )

    # --- método inteligente -------------------------------------------------
    atipicos = detectar_outliers(y, umbral_mad)
    x_limpio, y_limpio = x[~atipicos], y[~atipicos]
    descartadas = tuple(x[atipicos].tolist())

    if y_limpio.size == 0:  # todo atípico: no descartamos nada
        x_limpio, y_limpio, descartadas = x, y, ()

    if descartadas:
        etiquetas = ", ".join("S" + str(int(s)) for s in descartadas)
        nota_outliers = (f"Se descartó 1 semana atípica ({etiquetas}). "
                         if len(descartadas) == 1
                         else f"Se descartaron {len(descartadas)} semanas atípicas ({etiquetas}). ")
    else:
        nota_outliers = ""
    promedio_limpio = _promedio(y_limpio)

    if y_limpio.size < MIN_PUNTOS_TENDENCIA or np.unique(x_limpio).size < 2:
        return Proyeccion(
            valor=promedio_limpio,
            metodo=metodo,
            semanas_usadas=tuple(x_limpio.tolist()),
            semanas_descartadas=descartadas,
            pendiente=None,
            detalle=nota_outliers + "Muy pocos puntos para una tendencia: se usa el promedio limpio.",
        )

    pendiente, ordenada = np.polyfit(x_limpio, y_limpio, 1)
    horizonte = float(np.max(x)) + 1.0  # la semana siguiente a la última observada

    if not _pendiente_es_tendencia(x_limpio, y_limpio, pendiente, ordenada, promedio_limpio):
        return Proyeccion(
            valor=promedio_limpio,
            metodo=metodo,
            semanas_usadas=tuple(x_limpio.tolist()),
            semanas_descartadas=descartadas,
            pendiente=float(pendiente),
            detalle=nota_outliers + "Sin tendencia relevante: se usa el promedio limpio.",
        )

    prediccion = float(pendiente) * horizonte + float(ordenada)
    prediccion = max(prediccion, 0.0)  # una tendencia bajista no puede pedir negativo
    signo = "creciente" if pendiente > 0 else "decreciente"
    return Proyeccion(
        valor=prediccion,
        metodo=metodo,
        semanas_usadas=tuple(x_limpio.tolist()),
        semanas_descartadas=descartadas,
        pendiente=float(pendiente),
        detalle=(nota_outliers +
                 f"Tendencia {signo} de {pendiente:+.1f} por semana proyectada a S{int(horizonte)}."),
    )


# ---------------------------------------------------------------------------
# Necesidad real y formatos recomendados
# ---------------------------------------------------------------------------


def necesidad_real(consumo_proyectado: float, stock_actual: float,
                   buffer: float = BUFFER) -> float:
    """Lo que falta comprar, en unidad base. Nunca negativo: si sobra stock, la
    necesidad es 0 (no se puede "devolver" inventario)."""
    objetivo = float(consumo_proyectado) * (1.0 + float(buffer))
    return max(objetivo - float(stock_actual), 0.0)


# ---------------------------------------------------------------------------
# Clasificación de alertas
# ---------------------------------------------------------------------------


def _fmt(valor: float, decimales: int = 1) -> str:
    """Formatea números para los mensajes: 200.0 -> '200', 10.2 -> '10.2'."""
    if valor is None or (isinstance(valor, float) and not np.isfinite(valor)):
        return "?"
    redondeado = round(float(valor), decimales)
    if abs(redondeado - round(redondeado)) < 1e-9:
        return str(int(round(redondeado)))
    return f"{redondeado:g}"


def _plural_formato(formato: str, cantidad: float) -> str:
    """'Saco 25 kg' + 3 -> 'sacos de 25 kg'. Cosmético, para que la frase suene natural."""
    texto = str(formato).strip()
    partes = texto.split(" ", 1)
    cabeza = partes[0]
    resto = f" de {partes[1]}" if len(partes) > 1 else ""
    if cantidad == 1:
        return f"{cabeza.lower()}{resto}"
    plural = cabeza + ("es" if cabeza.lower().endswith(("l", "r", "d", "n")) else "s")
    return f"{plural.lower()}{resto}"


def redactar_mensaje(fila: dict) -> str:
    """Frase accionable para la gerente de compras."""
    tipo = fila["tipo"]
    suc = fila["sucursal"]
    nombre = fila.get("nombre") or fila["ingrediente_id"]
    unidad = fila.get("unidad_base") or "und"
    formato = fila.get("formato_compra") or "formato"
    pedidos = fila.get("formatos_pedidos")
    recomendados = fila.get("formatos_recomendados")
    delta = fila.get("delta_formatos")

    if tipo == DATO_RARO:
        motivo = fila.get("motivo_dato_raro") or "no está en el catálogo"
        cantidad = "una cantidad ilegible" if pd.isna(pedidos) else f"{_fmt(pedidos)} formatos"
        return (f'⚪ {suc}: pidió {cantidad} de "{fila["ingrediente_id"]}", que {motivo}. '
                f"No verificable → revisar manualmente.")

    if tipo == SIN_HISTORIAL:
        return (f"🔵 {suc}: pidió {_fmt(pedidos)} {_plural_formato(formato, pedidos)} de {nombre}, "
                f"pero no hay consumo histórico para proyectar. Stock actual: "
                f"{_fmt(fila.get('stock_actual', 0))} {unidad} → validar con la sucursal.")

    if tipo == OLVIDO:
        return (f"🟠 {suc}: no pidió {nombre}, pero lo consume y el stock "
                f"({_fmt(fila.get('stock_actual', 0))} {unidad}) no cubre la proyección "
                f"({_fmt(fila.get('consumo_proyectado', 0))} {unidad}). "
                f"Debería pedir ~{_fmt(recomendados)} {_plural_formato(formato, recomendados)}.")

    if tipo == PIDE_MENOS:
        falta = abs(delta)
        return (f"🔴 {suc}: pidió {_fmt(pedidos)} {_plural_formato(formato, pedidos)} de {nombre}; "
                f"se proyecta necesidad de {_fmt(recomendados)} {_plural_formato(formato, recomendados)}. "
                f"Faltan {_fmt(falta)} → riesgo de quiebre.")

    if tipo == PIDE_MAS:
        extra = " · PERECEDERO, riesgo de merma" if fila.get("es_perecedero_bool") else ""
        return (f"🟡 {suc}: pidió {_fmt(pedidos)} {_plural_formato(formato, pedidos)} de {nombre}; "
                f"con {_fmt(recomendados)} {_plural_formato(formato, recomendados)} alcanza. "
                f"Sobran {_fmt(delta)}{extra}.")

    return (f"🟢 {suc}: {nombre} está bien pedido "
            f"({_fmt(pedidos)} {_plural_formato(formato, pedidos)}).")


def construir_alertas(datos: Datos,
                      metodo: str = METODO_PROMEDIO,
                      buffer: float = BUFFER,
                      umbral_mad: float = UMBRAL_MAD) -> pd.DataFrame:
    """Recorre la UNIÓN de (sucursal, ingrediente) que se consumen y que se
    pidieron, y clasifica cada par. Devuelve un DataFrame ordenado por severidad.
    """
    catalogo = datos.catalogo.set_index("ingrediente_id", drop=False)
    catalogo_dict = catalogo.to_dict(orient="index")

    stock_dict = {
        (r.sucursal, r.ingrediente_id): float(r.stock_actual_unidad_base)
        for r in datos.inventario.itertuples()
    }
    orden_dict = {
        (r.sucursal, r.ingrediente_id): r.cantidad_formatos
        for r in datos.orden.itertuples()
    }
    historico: dict[tuple[str, str], pd.DataFrame] = {
        clave: grupo for clave, grupo in datos.consumo.groupby(["sucursal", "ingrediente_id"])
    }

    # Universo = lo que la sucursal consume U lo que pidió. Un ingrediente que no
    # consume ni pidió simplemente no le aplica.
    pares = sorted(set(historico.keys()) | set(orden_dict.keys()))

    filas: list[dict] = []
    for sucursal, ingrediente_id in pares:
        info = catalogo_dict.get(ingrediente_id)
        pedidos = orden_dict.get((sucursal, ingrediente_id), np.nan)
        esta_en_orden = (sucursal, ingrediente_id) in orden_dict
        stock = stock_dict.get((sucursal, ingrediente_id), 0.0)
        grupo = historico.get((sucursal, ingrediente_id))
        tiene_historico = grupo is not None and len(grupo) > 0

        fila = {
            "sucursal": sucursal,
            "ingrediente_id": ingrediente_id,
            "nombre": info["nombre"] if info else ingrediente_id,
            "proveedor": info["proveedor"] if info else "Desconocido",
            "unidad_base": info["unidad_base"] if info else "?",
            "formato_compra": info["formato_compra"] if info else "formato",
            "unidad_base_por_formato": float(info["unidad_base_por_formato"]) if info else np.nan,
            "es_perecedero": info["es_perecedero"] if info else "?",
            "es_perecedero_bool": bool(info["es_perecedero_bool"]) if info else False,
            "stock_actual": stock,
            "formatos_pedidos": float(pedidos) if pd.notna(pedidos) else np.nan,
            "consumo_proyectado": np.nan,
            "necesidad_base": np.nan,
            "formatos_recomendados": np.nan,
            "pedido_base": np.nan,
            "delta_formatos": np.nan,
            "detalle_proyeccion": "",
            "semanas_usadas": "",
            "semanas_descartadas": "",
            "motivo_dato_raro": "",
        }

        # 1) DATO_RARO: sin catálogo no hay factor de conversión ni forma de validar.
        if info is None:
            fila["tipo"] = DATO_RARO
            fila["motivo_dato_raro"] = "no está en el catálogo"
        elif esta_en_orden and pd.isna(pedidos):
            # La cantidad vino ilegible: tampoco es verificable.
            fila["tipo"] = DATO_RARO
            fila["motivo_dato_raro"] = "tiene una cantidad ilegible en la orden"
        else:
            factor = fila["unidad_base_por_formato"]
            if tiene_historico:
                proy = proyectar(grupo["semana_num"].tolist(),
                                 grupo["consumo_unidad_base"].tolist(),
                                 metodo=metodo, umbral_mad=umbral_mad)
                fila["consumo_proyectado"] = proy.valor
                fila["detalle_proyeccion"] = proy.detalle
                fila["semanas_usadas"] = ", ".join("S" + str(int(s)) for s in proy.semanas_usadas)
                fila["semanas_descartadas"] = ", ".join("S" + str(int(s)) for s in proy.semanas_descartadas)
                fila["necesidad_base"] = necesidad_real(proy.valor, stock, buffer)
                fila["formatos_recomendados"] = float(base_a_formatos(fila["necesidad_base"], factor))

            if esta_en_orden:
                fila["pedido_base"] = formatos_a_base(pedidos, factor)

            if not esta_en_orden:
                # 2) OLVIDO: lo consume, no lo pidió y el stock no alcanza.
                if fila["formatos_recomendados"] >= 1:
                    fila["tipo"] = OLVIDO
                    fila["formatos_pedidos"] = 0.0
                    fila["pedido_base"] = 0.0
                    fila["delta_formatos"] = -fila["formatos_recomendados"]
                else:
                    # No lo pidió pero el stock ya cubre la proyección: está bien.
                    fila["tipo"] = OK
                    fila["formatos_pedidos"] = 0.0
                    fila["pedido_base"] = 0.0
                    fila["delta_formatos"] = 0.0
            elif not tiene_historico:
                # 3) SIN_HISTORIAL: se puede convertir, pero no proyectar con confianza.
                fila["tipo"] = SIN_HISTORIAL
            else:
                # 4) delta sobre FORMATOS (el ceil ya absorbió el sobrante sub-formato).
                delta = float(pedidos) - fila["formatos_recomendados"]
                fila["delta_formatos"] = delta
                if delta > 0:
                    fila["tipo"] = PIDE_MAS
                elif delta < 0:
                    fila["tipo"] = PIDE_MENOS
                else:
                    fila["tipo"] = OK

        fila["severidad"] = SEVERIDAD[fila["tipo"]]
        fila["mensaje"] = redactar_mensaje(fila)
        filas.append(fila)

    alertas = pd.DataFrame(filas)
    if alertas.empty:
        return pd.DataFrame(columns=COLUMNAS_ALERTAS + ["es_perecedero_bool", "motivo_dato_raro"])

    # Orden: severidad primero, y dentro de cada tipo, el desvío más grande arriba.
    alertas["_magnitud"] = alertas["delta_formatos"].abs().fillna(0.0)
    alertas = alertas.sort_values(
        ["severidad", "_magnitud", "sucursal", "nombre"],
        ascending=[True, False, True, True],
    ).drop(columns="_magnitud").reset_index(drop=True)
    return alertas


# ---------------------------------------------------------------------------
# Resúmenes para el dashboard
# ---------------------------------------------------------------------------


def solo_alertas(alertas: pd.DataFrame) -> pd.DataFrame:
    """Descarta las filas OK: la gerente solo quiere ver lo que está mal."""
    if alertas.empty:
        return alertas
    return alertas[alertas["tipo"].isin(TIPOS_ALERTA)]


def resumen_kpis(alertas: pd.DataFrame) -> dict:
    conteos = alertas["tipo"].value_counts().to_dict() if not alertas.empty else {}
    problemas = solo_alertas(alertas)
    peor_sucursal, peor_conteo = "—", 0
    if not problemas.empty:
        ranking = problemas["sucursal"].value_counts()
        peor_sucursal, peor_conteo = str(ranking.index[0]), int(ranking.iloc[0])
    return {
        "total_alertas": int(len(problemas)),
        "quiebres": int(conteos.get(PIDE_MENOS, 0)),
        "excesos": int(conteos.get(PIDE_MAS, 0)),
        "olvidos": int(conteos.get(OLVIDO, 0)),
        "no_verificables": int(conteos.get(DATO_RARO, 0)),
        "sin_historial": int(conteos.get(SIN_HISTORIAL, 0)),
        "ok": int(conteos.get(OK, 0)),
        "revisados": int(len(alertas)),
        "sucursal_mas_alertas": peor_sucursal,
        "alertas_peor_sucursal": peor_conteo,
    }


def filtrar(alertas: pd.DataFrame,
            sucursales: Iterable[str] | None = None,
            tipos: Iterable[str] | None = None,
            proveedores: Iterable[str] | None = None) -> pd.DataFrame:
    df = alertas
    if df.empty:
        return df
    if sucursales is not None:
        df = df[df["sucursal"].isin(list(sucursales))]
    if tipos is not None:
        df = df[df["tipo"].isin(list(tipos))]
    if proveedores is not None:
        df = df[df["proveedor"].isin(list(proveedores))]
    return df
