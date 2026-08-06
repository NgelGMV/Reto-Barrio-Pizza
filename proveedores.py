"""
Pedido corregido agrupado por proveedor.

A cada proveedor se le manda una orden aparte, así que después de corregir las
alertas hay que rearmar el pedido por proveedor y no por sucursal. Este módulo
toma el DataFrame de alertas de `logica.construir_alertas` y devuelve tablas
listas para enviar.

Como el resto de la lógica, no depende de Streamlit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import logica as L

COLUMNAS_PEDIDO = [
    "proveedor",
    "ingrediente_id",
    "nombre",
    "formato_compra",
    "unidad_base",
    "unidad_base_por_formato",
    "sucursal",
    "formatos_pedidos",
    "formatos_a_pedir",
    "cambio_vs_pedido",
    "total_unidad_base",
    "origen",
]

ORIGEN_CALCULADO = "Calculado por la proyección"
ORIGEN_SIN_HISTORIAL = "Sin histórico: se respeta lo pedido"


def pedido_corregido(alertas: pd.DataFrame) -> pd.DataFrame:
    """Lo que cada sucursal *debería* pedir, con su proveedor.

    Criterios:
    - Se usa `formatos_recomendados`, no lo que la sucursal pidió: el objetivo es
      la orden corregida.
    - Sin histórico no hay recomendación posible, así que se respeta la cantidad
      pedida y se marca el origen para que la gerente sepa que no está validada.
    - Los ingredientes fuera de catálogo se excluyen: no tienen proveedor ni
      factor de conversión. Se revisan aparte (quedan como alerta no verificable).
    """
    vacio = pd.DataFrame(columns=COLUMNAS_PEDIDO)
    if alertas.empty:
        return vacio

    df = alertas[alertas["tipo"] != L.DATO_RARO].copy()
    if df.empty:
        return vacio

    recomendado = pd.to_numeric(df["formatos_recomendados"], errors="coerce")
    pedido = pd.to_numeric(df["formatos_pedidos"], errors="coerce").fillna(0.0)

    df["formatos_a_pedir"] = recomendado.where(recomendado.notna(), pedido).fillna(0.0)
    df["origen"] = np.where(recomendado.notna(), ORIGEN_CALCULADO, ORIGEN_SIN_HISTORIAL)
    df["formatos_pedidos"] = pedido
    df["cambio_vs_pedido"] = df["formatos_a_pedir"] - pedido
    df["total_unidad_base"] = df["formatos_a_pedir"] * df["unidad_base_por_formato"]

    # Lo que no hay que comprar no va en la orden.
    df = df[df["formatos_a_pedir"] > 0]
    if df.empty:
        return vacio

    return (df[COLUMNAS_PEDIDO]
            .sort_values(["proveedor", "nombre", "sucursal"])
            .reset_index(drop=True))


def resumen_proveedores(pedido: pd.DataFrame) -> pd.DataFrame:
    """Una fila por proveedor: cuántos insumos y cuántos formatos le tocan."""
    if pedido.empty:
        return pd.DataFrame(columns=["proveedor", "insumos", "formatos", "sucursales", "correcciones"])
    resumen = pedido.groupby("proveedor").agg(
        insumos=("ingrediente_id", "nunique"),
        formatos=("formatos_a_pedir", "sum"),
        sucursales=("sucursal", "nunique"),
        correcciones=("cambio_vs_pedido", lambda s: int((s != 0).sum())),
    ).reset_index()
    return resumen.sort_values("formatos", ascending=False).reset_index(drop=True)


def matriz_proveedor(pedido: pd.DataFrame, proveedor: str) -> pd.DataFrame:
    """Tabla para mandarle a un proveedor: insumos en filas, sucursales en columnas.

    Es el formato en que se arma una orden real: el proveedor ve cuánto entregar
    en cada local y el total a facturar.
    """
    df = pedido[pedido["proveedor"] == proveedor]
    if df.empty:
        return pd.DataFrame()

    tabla = df.pivot_table(
        index=["nombre", "formato_compra"],
        columns="sucursal",
        values="formatos_a_pedir",
        aggfunc="sum",
        fill_value=0,
    )
    tabla.columns.name = None
    tabla["TOTAL formatos"] = tabla.sum(axis=1)
    tabla.index.names = ["Insumo", "Formato"]
    return tabla.reset_index().sort_values("TOTAL formatos", ascending=False)


def comparar_con_lo_pedido(pedido: pd.DataFrame) -> pd.DataFrame:
    """Solo las líneas que cambian respecto de la orden original."""
    if pedido.empty:
        return pedido
    return pedido[pedido["cambio_vs_pedido"] != 0].copy()


def a_csv(df: pd.DataFrame, incluir_indice: bool = False) -> bytes:
    """CSV listo para descargar (con BOM, para que Excel no rompa los acentos)."""
    return df.to_csv(index=incluir_indice).encode("utf-8-sig")
