"""
Detección de órdenes raras comparando cada sucursal contra las demás.

Las alertas de `logica.py` miran cada sucursal por separado: comparan lo que pidió
contra lo que ella misma va a consumir. Este módulo mira otra cosa: si una sucursal
está pidiendo un insumo de forma **distinta al resto de la cadena**.

Son dos preguntas diferentes y una no reemplaza a la otra. Una sucursal puede estar
pidiendo coherente con su propio consumo (alerta OK) y aun así ser la única que
compra el triple de cobertura que sus pares, lo que casi siempre significa que su
consumo histórico ya venía inflado o que alguien pide "por si acaso".

La comparación se hace sobre la **cobertura**:

    cobertura = pedido_base / consumo_proyectado

o sea cuántas semanas de consumo cubre lo que pidió. Es un número sin unidades, así
que se puede comparar harina contra albahaca sin que los kilos distorsionen nada.

Como el resto del proyecto, no depende de Streamlit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import logica as L

# Cuánto se tiene que desviar una sucursal de la mediana de sus pares para que
# valga la pena mirarla. 0.6 = pide 60% más (o menos) que el resto.
UMBRAL_DESVIO = 0.6

# Con menos de 3 sucursales comparables, la "mediana de los pares" es una opinión,
# no una referencia: no se marca nada.
MIN_SUCURSALES = 3

# Un desvío que en la práctica no cambia la compra no es un hallazgo.
MIN_FORMATOS_DIFERENCIA = 1.0

COLUMNAS = [
    "sucursal", "ingrediente_id", "nombre", "proveedor", "formato_compra",
    "unidad_base", "formatos_pedidos", "cobertura", "cobertura_pares",
    "veces_vs_pares", "formatos_segun_pares", "diferencia_formatos",
    "direccion", "tipo_alerta", "ya_alertado", "mensaje",
]


def _mensaje(fila: pd.Series) -> str:
    veces = fila["veces_vs_pares"]
    formato = L._plural_formato(fila["formato_compra"], fila["formatos_pedidos"])
    diferencia = abs(fila["diferencia_formatos"])
    formato_dif = L._plural_formato(fila["formato_compra"], diferencia)

    if fila["direccion"] == "por encima":
        return (
            f"🔎 {fila['sucursal']} pidió {L._fmt(fila['formatos_pedidos'])} {formato} "
            f"de {fila['nombre']}: {L._fmt(veces, 1)}× lo que piden las otras sucursales "
            f"para su propio consumo. Con el criterio del resto de la cadena le "
            f"alcanzarían {L._fmt(fila['formatos_segun_pares'])} "
            f"({L._fmt(diferencia)} {formato_dif} de más)."
        )
    return (
        f"🔎 {fila['sucursal']} pidió {L._fmt(fila['formatos_pedidos'])} {formato} "
        f"de {fila['nombre']}: {L._fmt(veces, 1)}× lo que piden las otras sucursales "
        f"para su propio consumo. Con el criterio del resto de la cadena pediría "
        f"{L._fmt(fila['formatos_segun_pares'])} "
        f"({L._fmt(diferencia)} {formato_dif} de menos)."
    )


def ordenes_raras(alertas: pd.DataFrame,
                  umbral_desvio: float = UMBRAL_DESVIO,
                  min_sucursales: int = MIN_SUCURSALES,
                  min_formatos: float = MIN_FORMATOS_DIFERENCIA) -> pd.DataFrame:
    """Filas donde una sucursal se sale del patrón de las demás.

    Se compara contra la **mediana de las otras** sucursales (no contra el promedio
    del grupo entero) por dos motivos: la mediana no se deja arrastrar por un solo
    valor extremo, y excluir a la propia sucursal evita que una sucursal muy rara
    se compare contra un promedio que ella misma infló.
    """
    vacio = pd.DataFrame(columns=COLUMNAS)
    if alertas.empty:
        return vacio

    df = alertas[
        alertas["tipo"].ne(L.DATO_RARO)
        & alertas["consumo_proyectado"].notna()
        & (alertas["consumo_proyectado"] > 0)
        & alertas["pedido_base"].notna()
    ].copy()
    if df.empty:
        return vacio

    df["cobertura"] = df["pedido_base"] / df["consumo_proyectado"]

    filas = []
    for ingrediente, grupo in df.groupby("ingrediente_id"):
        if len(grupo) < min_sucursales:
            continue
        for indice, fila in grupo.iterrows():
            pares = grupo.drop(index=indice)["cobertura"]
            mediana = float(np.median(pares))
            # Si los pares directamente no piden, no hay referencia contra la cual
            # comparar y cualquier división daría un número infinito.
            if not np.isfinite(mediana) or mediana <= 0:
                continue

            cobertura = float(fila["cobertura"])
            desvio = (cobertura - mediana) / mediana
            if abs(desvio) < umbral_desvio:
                continue

            base_segun_pares = mediana * float(fila["consumo_proyectado"])
            formatos_pares = L.base_a_formatos(base_segun_pares,
                                               float(fila["unidad_base_por_formato"]))
            diferencia = float(fila["formatos_pedidos"]) - formatos_pares
            if abs(diferencia) < min_formatos:
                continue

            filas.append({
                "sucursal": fila["sucursal"],
                "ingrediente_id": ingrediente,
                "nombre": fila["nombre"],
                "proveedor": fila["proveedor"],
                "formato_compra": fila["formato_compra"],
                "unidad_base": fila["unidad_base"],
                "formatos_pedidos": float(fila["formatos_pedidos"]),
                "cobertura": cobertura,
                "cobertura_pares": mediana,
                "veces_vs_pares": cobertura / mediana,
                "formatos_segun_pares": float(formatos_pares),
                "diferencia_formatos": diferencia,
                "direccion": "por encima" if desvio > 0 else "por debajo",
                # Sirve para distinguir lo que este análisis aporta de verdad:
                # si la línea ya tenía alerta propia, esto la explica desde otro
                # ángulo; si no la tenía, es un hallazgo que solo se ve comparando.
                "tipo_alerta": fila["tipo"],
                "ya_alertado": fila["tipo"] != L.OK,
            })

    if not filas:
        return vacio

    resultado = pd.DataFrame(filas)
    resultado["mensaje"] = resultado.apply(_mensaje, axis=1)
    # Primero lo que NO tiene alerta propia (solo se ve comparando sucursales) y
    # dentro de eso, lo más desalineado, que es donde hay más plata en juego.
    resultado["orden"] = resultado["veces_vs_pares"].sub(1).abs()
    return (resultado.sort_values(["ya_alertado", "orden"], ascending=[True, False])
            .drop(columns="orden")[COLUMNAS]
            .reset_index(drop=True))


def resumen_por_sucursal(raras: pd.DataFrame) -> pd.DataFrame:
    """Cuántas rarezas acumula cada sucursal, para ver si hay una que se repite."""
    if raras.empty:
        return pd.DataFrame(columns=["sucursal", "hallazgos", "por_encima", "por_debajo"])
    resumen = raras.groupby("sucursal").agg(
        hallazgos=("ingrediente_id", "count"),
        por_encima=("direccion", lambda s: int((s == "por encima").sum())),
        por_debajo=("direccion", lambda s: int((s == "por debajo").sum())),
    ).reset_index()
    return resumen.sort_values("hallazgos", ascending=False).reset_index(drop=True)
