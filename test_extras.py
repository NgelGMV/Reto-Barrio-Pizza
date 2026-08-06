"""
Verificaciones de los módulos opcionales: `proveedores.py`, `anomalias.py` y la
posibilidad de reemplazar la orden desde la interfaz.

Correr:  python -m pytest test_extras.py -v
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

import anomalias as AN
import chat as CH
import logica as L
import proveedores as P

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def datos() -> L.Datos:
    return L.cargar_datos()


@pytest.fixture(scope="module")
def alertas(datos) -> pd.DataFrame:
    return L.construir_alertas(datos, metodo=L.METODO_INTELIGENTE)


# ---------------------------------------------------------------------------
# Pedido corregido por proveedor
# ---------------------------------------------------------------------------


def test_pedido_usa_lo_recomendado_y_no_lo_pedido(alertas):
    """La orden corregida debe traer lo que hay que comprar, no lo que se pidió."""
    pedido = P.pedido_corregido(alertas)
    albahaca = pedido[(pedido["sucursal"] == "Via Argentina") &
                      (pedido["ingrediente_id"] == "albahaca")]
    assert len(albahaca) == 1
    fila = albahaca.iloc[0]
    assert fila["formatos_pedidos"] == 20      # lo que pidió la sucursal
    assert fila["formatos_a_pedir"] < 20       # lo que realmente necesita
    assert fila["cambio_vs_pedido"] < 0


def test_pedido_excluye_lo_que_no_esta_en_catalogo(alertas):
    """Sin catálogo no hay proveedor ni factor: no puede entrar en una orden."""
    pedido = P.pedido_corregido(alertas)
    assert "aji_chombo" not in set(pedido["ingrediente_id"])


def test_pedido_no_incluye_lineas_en_cero(alertas):
    pedido = P.pedido_corregido(alertas)
    assert (pedido["formatos_a_pedir"] > 0).all()


def test_matriz_por_proveedor_suma_bien(alertas):
    """El total de la fila tiene que ser la suma de las sucursales."""
    pedido = P.pedido_corregido(alertas)
    proveedor = pedido["proveedor"].iloc[0]
    matriz = P.matriz_proveedor(pedido, proveedor)
    columnas_sucursal = [c for c in matriz.columns
                         if c not in ("Insumo", "Formato", "TOTAL formatos")]
    esperado = matriz[columnas_sucursal].sum(axis=1)
    assert (matriz["TOTAL formatos"] == esperado).all()

    del_proveedor = pedido[pedido["proveedor"] == proveedor]
    assert matriz["TOTAL formatos"].sum() == del_proveedor["formatos_a_pedir"].sum()


def test_pedido_con_alertas_vacias_no_rompe():
    vacio = pd.DataFrame(columns=L.COLUMNAS_ALERTAS)
    assert P.pedido_corregido(vacio).empty
    assert P.resumen_proveedores(P.pedido_corregido(vacio)).empty


# ---------------------------------------------------------------------------
# Detección de órdenes raras
# ---------------------------------------------------------------------------


def test_detecta_la_sucursal_que_pide_fuera_de_patron(alertas):
    """Via Argentina pide 20 paquetes de albahaca; sus pares piden muchísimo menos."""
    raras = AN.ordenes_raras(alertas)
    fila = raras[(raras["sucursal"] == "Via Argentina") &
                 (raras["ingrediente_id"] == "albahaca")]
    assert len(fila) == 1
    assert fila.iloc[0]["direccion"] == "por encima"
    assert fila.iloc[0]["veces_vs_pares"] > 2


def test_no_marca_a_quien_pide_igual_que_sus_pares(alertas):
    """Marbella pide cebolla como el resto: no puede aparecer como rara."""
    raras = AN.ordenes_raras(alertas)
    assert raras[(raras["sucursal"] == "Marbella") &
                 (raras["ingrediente_id"] == "cebolla")].empty


def test_no_compara_con_menos_de_tres_sucursales(alertas):
    """Con dos sucursales la 'mediana de los pares' no es una referencia."""
    dos = alertas[alertas["sucursal"].isin(["Marbella", "Via Argentina"])]
    assert AN.ordenes_raras(dos).empty


def test_ordenes_raras_con_datos_vacios_no_rompe():
    vacio = pd.DataFrame(columns=L.COLUMNAS_ALERTAS)
    assert AN.ordenes_raras(vacio).empty
    assert AN.resumen_por_sucursal(AN.ordenes_raras(vacio)).empty


def test_ordenes_raras_ignora_lo_no_verificable(alertas):
    """Sin conversión ni proyección no hay cobertura que comparar."""
    raras = AN.ordenes_raras(alertas)
    assert "aji_chombo" not in set(raras["ingrediente_id"])


# ---------------------------------------------------------------------------
# Reemplazar la orden desde la interfaz
# ---------------------------------------------------------------------------


def test_corregir_la_orden_apaga_la_alerta(datos):
    """Si se pide lo que falta, el olvido de mozzarella tiene que desaparecer."""
    original = L.construir_alertas(datos)
    olvido = original[(original["sucursal"] == "Brisas del Golf") &
                      (original["ingrediente_id"] == "mozzarella")].iloc[0]
    assert olvido["tipo"] == L.OLVIDO

    nueva_orden = pd.concat([
        datos.orden,
        pd.DataFrame([{"sucursal": "Brisas del Golf",
                       "ingrediente_id": "mozzarella",
                       "cantidad_formatos": olvido["formatos_recomendados"]}]),
    ], ignore_index=True)

    corregidos = L.construir_alertas(L.cargar_datos(orden_df=nueva_orden))
    fila = corregidos[(corregidos["sucursal"] == "Brisas del Golf") &
                      (corregidos["ingrediente_id"] == "mozzarella")].iloc[0]
    assert fila["tipo"] == L.OK


def test_orden_subida_viaja_como_csv(datos):
    """La UI serializa la orden a texto para usarla de clave de caché."""
    texto = datos.orden.to_csv(index=False)
    recargados = L.cargar_datos(orden_df=pd.read_csv(io.StringIO(texto)))
    assert len(recargados.orden) == len(datos.orden)
    assert (L.construir_alertas(recargados)["tipo"].value_counts().to_dict()
            == L.construir_alertas(datos)["tipo"].value_counts().to_dict())


def test_orden_sin_las_columnas_necesarias_avisa_claro():
    mala = pd.DataFrame({"sucursal": ["Marbella"], "cantidad": [3]})
    with pytest.raises(ValueError, match="ingrediente_id"):
        L.cargar_datos(orden_df=mala)


def test_orden_vacia_no_rompe_la_app(datos):
    """Una orden sin líneas: todo lo que se consume pasa a ser olvido, sin errores."""
    vacia = pd.DataFrame(columns=["sucursal", "ingrediente_id", "cantidad_formatos"])
    alertas = L.construir_alertas(L.cargar_datos(orden_df=vacia))
    assert not alertas.empty
    assert set(alertas["tipo"]) <= {L.OLVIDO, L.OK}


# ---------------------------------------------------------------------------
# Chat con los datos (sin llamar a la API)
# ---------------------------------------------------------------------------


def test_el_contexto_lleva_los_numeros_reales(alertas):
    """El modelo tiene que ver exactamente lo que calculó logica.py."""
    contexto = CH.contexto_de_alertas(alertas, metodo=L.METODO_INTELIGENTE)
    assert "Proyección inteligente" in contexto
    assert "Harina 00" in contexto
    # El resumen del encabezado tiene que coincidir con los KPI del dashboard.
    resumen = L.resumen_kpis(alertas)
    assert f"Alertas: {resumen['total_alertas']}" in contexto


def test_el_contexto_no_lleva_jerga_interna(alertas):
    """Si el modelo no ve los códigos ni los nombres de columna, no los puede
    repetir en la respuesta: la gerente no tiene por qué leer 'PIDE_MENOS'."""
    contexto = CH.contexto_de_alertas(alertas)
    for codigo in L.TIPOS_ALERTA:
        assert codigo not in contexto
    for columna in ("delta_formatos", "formatos_pedidos", "unidad_base"):
        assert columna not in contexto
    assert "Riesgo de quiebre" in contexto


def test_el_contexto_entra_holgado_en_el_prompt(alertas):
    """Con ~90 líneas el dataset entero cabe en el contexto sin resumir."""
    contexto = CH.contexto_de_alertas(alertas)
    assert len(contexto) < 40_000  # ~10k tokens, muy por debajo del límite


def test_contexto_sin_alertas_no_rompe():
    vacio = pd.DataFrame(columns=L.COLUMNAS_ALERTAS)
    assert "No hay ninguna línea" in CH.contexto_de_alertas(vacio)


def test_sin_clave_avisa_en_vez_de_reventar(alertas):
    with pytest.raises(CH.ErrorChat, match="clave"):
        CH.preguntar("¿qué pasa?", alertas, api_key="")


def test_pregunta_vacia_no_llega_a_la_api(alertas):
    with pytest.raises(CH.ErrorChat, match="vacía"):
        CH.preguntar("   ", alertas, api_key="gsk_falsa")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
