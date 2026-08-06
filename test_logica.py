"""
Verificaciones de aceptación de `logica.py`.

Cubren los 6 casos del brief (sección 7) sobre los datos reales del reto, más un
bloque de pruebas defensivas con datos sintéticos (BOM, espacios, valores
ilegibles, ingredientes sin histórico) para confirmar que la app no se cae.

Correr:  python -m pytest test_logica.py -v
   o:    python test_logica.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import logica as L

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def datos() -> L.Datos:
    return L.cargar_datos()


@pytest.fixture(scope="module")
def alertas_promedio(datos) -> pd.DataFrame:
    return L.construir_alertas(datos, metodo=L.METODO_PROMEDIO)


@pytest.fixture(scope="module")
def alertas_inteligente(datos) -> pd.DataFrame:
    return L.construir_alertas(datos, metodo=L.METODO_INTELIGENTE)


def fila(alertas: pd.DataFrame, sucursal: str, ingrediente: str) -> pd.Series:
    """Devuelve la única fila de alerta de ese par (falla si no existe)."""
    sel = alertas[(alertas["sucursal"] == sucursal) &
                  (alertas["ingrediente_id"] == ingrediente)]
    assert len(sel) == 1, f"Se esperaba 1 fila para {sucursal}/{ingrediente}, hay {len(sel)}"
    return sel.iloc[0]


def serie_consumo(datos: L.Datos, sucursal: str, ingrediente: str):
    g = datos.consumo[(datos.consumo["sucursal"] == sucursal) &
                      (datos.consumo["ingrediente_id"] == ingrediente)]
    return g["semana_num"].tolist(), g["consumo_unidad_base"].tolist()


# ---------------------------------------------------------------------------
# Caso 1 — Conversión de unidades con factores decimales
# ---------------------------------------------------------------------------


def test_caso1_conversion_con_factores_decimales(datos):
    """4 latas de salsa pelatti = 10.2 kg. El factor NO se redondea ni se hardcodea:
    se lee del catálogo."""
    catalogo = datos.catalogo.set_index("ingrediente_id")

    factor_salsa = catalogo.loc["salsa_pelatti", "unidad_base_por_formato"]
    assert factor_salsa == pytest.approx(2.55)
    assert L.formatos_a_base(4, factor_salsa) == pytest.approx(10.2)

    factor_albahaca = catalogo.loc["albahaca", "unidad_base_por_formato"]
    assert factor_albahaca == pytest.approx(0.25)
    assert L.formatos_a_base(11, factor_albahaca) == pytest.approx(2.75)
    assert catalogo.loc["arugula", "unidad_base_por_formato"] == pytest.approx(0.25)

    # La vuelta: unidad base -> formatos completos, sin perder los decimales.
    assert L.base_a_formatos(10.2, 2.55) == 4       # entra justo
    assert L.base_a_formatos(10.21, 2.55) == 5      # un gramo de más ya son 5 latas
    assert L.base_a_formatos(2.6, 0.25) == 11       # 2.6 / 0.25 = 10.4 -> 11 paquetes


def test_caso1_conversion_usa_el_factor_del_catalogo(datos, alertas_promedio):
    """El pedido en unidad base de cada fila = formatos * factor del catálogo."""
    con_pedido = alertas_promedio[alertas_promedio["pedido_base"].notna()]
    esperado = con_pedido["formatos_pedidos"] * con_pedido["unidad_base_por_formato"]
    assert np.allclose(con_pedido["pedido_base"], esperado)

    salsa = fila(alertas_promedio, "Brisas del Golf", "salsa_pelatti")
    assert salsa["pedido_base"] == pytest.approx(26 * 2.55)  # 66.3 kg


# ---------------------------------------------------------------------------
# Caso 2 — Olvido: Brisas del Golf no pidió mozzarella
# ---------------------------------------------------------------------------


def test_caso2_olvido_mozzarella_brisas_del_golf(datos, alertas_promedio):
    orden = datos.orden
    esta_en_orden = ((orden["sucursal"] == "Brisas del Golf") &
                     (orden["ingrediente_id"] == "mozzarella")).any()
    assert not esta_en_orden, "El dato de entrada cambió: la orden ya incluye mozzarella"

    r = fila(alertas_promedio, "Brisas del Golf", "mozzarella")
    assert r["tipo"] == L.OLVIDO
    assert r["consumo_proyectado"] == pytest.approx(199.5, abs=1.0)   # ~200 kg
    assert r["stock_actual"] == pytest.approx(22.0)
    assert r["formatos_recomendados"] == 18                            # cajas de 10 kg
    assert r["formatos_pedidos"] == 0
    assert "no pidió" in r["mensaje"] and "Mozzarella" in r["mensaje"]


def test_caso2_no_pedir_no_es_olvido_si_el_stock_alcanza():
    """Si la sucursal no pidió algo pero el stock ya cubre la proyección, es OK."""
    proyeccion, stock = 5.0, 40.0
    necesidad = L.necesidad_real(proyeccion, stock)
    assert necesidad == 0.0
    assert L.base_a_formatos(necesidad, 10) == 0  # 0 formatos -> no es olvido


# ---------------------------------------------------------------------------
# Caso 3 — Dato raro: aji_chombo no está en el catálogo
# ---------------------------------------------------------------------------


def test_caso3_dato_raro_aji_chombo(datos, alertas_promedio, alertas_inteligente):
    assert "aji_chombo" not in set(datos.catalogo["ingrediente_id"])

    for alertas in (alertas_promedio, alertas_inteligente):
        r = fila(alertas, "Costa del Este", "aji_chombo")
        assert r["tipo"] == L.DATO_RARO
        assert r["formatos_pedidos"] == 3
        assert pd.isna(r["consumo_proyectado"])          # no se puede proyectar
        assert pd.isna(r["formatos_recomendados"])       # ni convertir
        assert "no está en el catálogo" in r["mensaje"]


def test_caso3_el_dato_raro_no_rompe_el_resto(alertas_promedio):
    """La app sigue procesando todo lo demás: 88 pares válidos + el aji_chombo."""
    assert len(alertas_promedio) == 89
    assert alertas_promedio["tipo"].isin(L.SEVERIDAD).all()
    assert alertas_promedio["mensaje"].notna().all()
    # Los KPIs se calculan igual, sin excepciones ni NaN propagados.
    kpis = L.resumen_kpis(alertas_promedio)
    assert kpis["no_verificables"] == 1
    assert kpis["total_alertas"] == sum(
        kpis[k] for k in ("quiebres", "excesos", "olvidos", "no_verificables", "sin_historial")
    )


# ---------------------------------------------------------------------------
# Caso 4 — Pide de menos con tendencia: Costa del Este / harina
# ---------------------------------------------------------------------------


def test_caso4_costa_del_este_harina_pide_menos(datos, alertas_promedio, alertas_inteligente):
    semanas, consumos = serie_consumo(datos, "Costa del Este", "harina")
    assert consumos == [240, 255, 268, 284, 300, 316]  # serie creciente del brief

    simple = fila(alertas_promedio, "Costa del Este", "harina")
    assert simple["tipo"] == L.PIDE_MENOS
    assert simple["formatos_pedidos"] == 6
    assert simple["consumo_proyectado"] == pytest.approx(277.17, abs=0.01)
    assert simple["formatos_recomendados"] == 10
    assert simple["delta_formatos"] == -4

    listo = fila(alertas_inteligente, "Costa del Este", "harina")
    assert listo["tipo"] == L.PIDE_MENOS
    # El método inteligente capta la tendencia y proyecta más alto...
    assert listo["consumo_proyectado"] > simple["consumo_proyectado"]
    assert listo["consumo_proyectado"] == pytest.approx(330.27, abs=0.5)
    assert listo["formatos_recomendados"] == 13
    # ...así que la brecha se ve AÚN MAYOR.
    assert listo["delta_formatos"] == -7
    assert abs(listo["delta_formatos"]) > abs(simple["delta_formatos"])


def test_caso4_la_tendencia_se_detecta_como_significativa(datos):
    semanas, consumos = serie_consumo(datos, "Costa del Este", "harina")
    proy = L.proyectar(semanas, consumos, metodo=L.METODO_INTELIGENTE)
    assert proy.pendiente == pytest.approx(15.17, abs=0.1)  # ~+15 kg por semana
    assert proy.semanas_descartadas == ()                   # no hay outliers acá
    assert "Tendencia creciente" in proy.detalle


# ---------------------------------------------------------------------------
# Caso 5 — Semana atípica: Marbella / pepperoni
# ---------------------------------------------------------------------------


def test_caso5_marbella_pepperoni_semana_atipica(datos, alertas_promedio, alertas_inteligente):
    semanas, consumos = serie_consumo(datos, "Marbella", "pepperoni")
    assert consumos == [28, 30, 150, 27, 29, 31]  # S3 es la semana atípica

    # Con promedio simple, el 150 infla la proyección y dispara un quiebre falso.
    simple = fila(alertas_promedio, "Marbella", "pepperoni")
    assert simple["consumo_proyectado"] == pytest.approx(49.17, abs=0.01)
    assert simple["tipo"] == L.PIDE_MENOS
    assert simple["formatos_recomendados"] == 9
    assert simple["delta_formatos"] == -4

    # Con el método inteligente se descarta S3 y la necesidad vuelve a lo normal.
    listo = fila(alertas_inteligente, "Marbella", "pepperoni")
    assert listo["semanas_descartadas"] == "S3"
    assert listo["consumo_proyectado"] == pytest.approx(29.0, abs=1.0)
    assert listo["formatos_recomendados"] == 5
    # La alerta desaparece: pidió exactamente lo que necesita.
    assert listo["tipo"] == L.OK
    assert listo["delta_formatos"] == 0

    # El contraste entre métodos tiene que ser visible al mover el toggle.
    assert simple["consumo_proyectado"] > 1.5 * listo["consumo_proyectado"]


def test_caso5_deteccion_de_outliers_por_mad():
    """La regla |x - mediana| > 3*MAD marca el 150 y nada más."""
    serie = [28, 30, 150, 27, 29, 31]
    atipicos = L.detectar_outliers(serie)
    assert atipicos.tolist() == [False, False, True, False, False, False]


def test_caso5_si_mad_es_cero_no_se_descarta_nada():
    """Serie constante: MAD = 0. No hay que borrar media serie."""
    assert not L.detectar_outliers([10, 10, 10, 10, 10, 10]).any()
    assert not L.detectar_outliers([10, 10, 10, 10, 10, 40]).all()


# ---------------------------------------------------------------------------
# Caso 6 — Regla de redondeo: un sobrante sub-formato NO es sobre-pedido
# ---------------------------------------------------------------------------


def test_caso6_sobrante_menor_a_un_formato_no_es_sobre_pedido(alertas_promedio):
    """Brisas del Golf / harina: necesita 244 kg y pidió 250 kg (10 sacos de 25).
    Sobran 6 kg, pero no existe medio saco -> es redondeo normal, no exceso."""
    r = fila(alertas_promedio, "Brisas del Golf", "harina")
    assert r["necesidad_base"] == pytest.approx(244.0)
    assert r["pedido_base"] == pytest.approx(250.0)
    sobrante = r["pedido_base"] - r["necesidad_base"]
    assert 0 < sobrante < r["unidad_base_por_formato"]   # menos de un saco
    assert r["tipo"] == L.OK
    assert r["delta_formatos"] == 0


def test_caso6_ningun_ok_esconde_un_exceso_de_un_formato_entero(alertas_promedio):
    """Regla general: si algo quedó OK, su sobrante siempre es < 1 formato."""
    ok = alertas_promedio[(alertas_promedio["tipo"] == L.OK) &
                          alertas_promedio["necesidad_base"].notna() &
                          (alertas_promedio["formatos_pedidos"] > 0)]
    sobrante = ok["pedido_base"] - ok["necesidad_base"]
    assert (sobrante >= -1e-9).all()
    assert (sobrante < ok["unidad_base_por_formato"]).all()


def test_caso6_el_delta_se_mide_en_formatos_no_en_unidad_base():
    """Pedir 3 sacos cuando se necesitan 2.1 sacos NO es exceso (ceil(2.1) = 3)."""
    necesidad, factor = 52.5, 25.0            # 2.1 sacos
    recomendados = L.base_a_formatos(necesidad, factor)
    assert recomendados == 3
    assert 3 - recomendados == 0              # OK, aunque sobren 22.5 kg


# ---------------------------------------------------------------------------
# Proyección: comportamiento base
# ---------------------------------------------------------------------------


def test_promedio_simple_es_el_promedio_de_las_6_semanas():
    proy = L.proyectar([1, 2, 3, 4, 5, 6], [10, 20, 30, 40, 50, 60], metodo=L.METODO_PROMEDIO)
    assert proy.valor == pytest.approx(35.0)
    assert proy.semanas_descartadas == ()


def test_serie_plana_con_ruido_no_extrapola_tendencia():
    """Sin esto, cualquier serie estable generaría quiebres o excesos fantasma."""
    serie = [100, 103, 98, 101, 99, 102]
    simple = L.proyectar([1, 2, 3, 4, 5, 6], serie, metodo=L.METODO_PROMEDIO)
    listo = L.proyectar([1, 2, 3, 4, 5, 6], serie, metodo=L.METODO_INTELIGENTE)
    assert listo.valor == pytest.approx(simple.valor, abs=1.5)
    assert "Sin tendencia relevante" in listo.detalle


def test_tendencia_decreciente_nunca_proyecta_negativo():
    proy = L.proyectar([1, 2, 3, 4, 5, 6], [60, 50, 40, 30, 20, 10],
                       metodo=L.METODO_INTELIGENTE)
    assert proy.valor >= 0.0


def test_proyeccion_sin_historico_no_falla():
    proy = L.proyectar([], [], metodo=L.METODO_INTELIGENTE)
    assert proy.valor == 0.0
    assert "Sin histórico" in proy.detalle


def test_pocos_puntos_caen_al_promedio():
    proy = L.proyectar([1, 2], [10, 30], metodo=L.METODO_INTELIGENTE)
    assert proy.valor == pytest.approx(20.0)
    assert "Muy pocos puntos" in proy.detalle


def test_necesidad_real_nunca_es_negativa():
    assert L.necesidad_real(10, 40) == 0.0
    assert L.necesidad_real(40, 10) == pytest.approx(30.0)
    assert L.necesidad_real(100, 0, buffer=0.10) == pytest.approx(110.0)


def test_base_a_formatos_rechaza_factor_invalido():
    for factor in (0, -5, float("nan")):
        with pytest.raises(ValueError):
            L.base_a_formatos(10, factor)


# ---------------------------------------------------------------------------
# Robustez: datos incompletos o raros (sintéticos)
# ---------------------------------------------------------------------------


def _escribir_dataset(carpeta: Path, catalogo: str, consumo: str,
                      inventario: str, orden: str) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    # Se escribe con BOM a propósito: es como vienen los CSV del reto.
    for nombre, contenido in (("ingredientes.csv", catalogo),
                              ("consumo_historico.csv", consumo),
                              ("inventario_actual.csv", inventario),
                              ("orden_compra_semana.csv", orden)):
        (carpeta / nombre).write_text(contenido, encoding="utf-8-sig")
    return carpeta


@pytest.fixture
def dataset_raro(tmp_path) -> L.Datos:
    """Dataset mínimo con todas las trampas de formato que se nos ocurrieron."""
    catalogo = (
        "ingrediente_id,nombre,proveedor,unidad_base,formato_compra,unidad_base_por_formato,es_perecedero\n"
        " harina ,Harina 00, Molinos Central ,kg,Saco 25 kg,25,No\n"          # espacios
        "mozzarella,Mozzarella,Bella Italia,kg,Caja 10 kg,10,Si\n"
        "trufa,Trufa negra,Deli Gourmet,kg,Frasco 0.05 kg,0.05,Si\n"          # sin histórico
        "roto,Ingrediente roto,Nadie,kg,Caja,,No\n"                           # factor vacío
    )
    consumo = (
        "sucursal,ingrediente_id,semana,consumo_unidad_base\n"
        "  Marbella  , harina ,S1,100\n"
        "Marbella,harina,S2,abc\n"                                            # ilegible
        "Marbella,harina,S3,110\n"
        "Marbella,harina,S4,105\n"
        "Marbella,mozzarella,S1,50\n"
        "Marbella,mozzarella,S2,50\n"
        "Marbella,mozzarella,S3,50\n"
        ",huerfano,S1,10\n"                                                   # sin sucursal
    )
    inventario = (
        "sucursal,ingrediente_id,stock_actual_unidad_base\n"
        "Marbella,harina,-5\n"                                                # negativo
        "Marbella,mozzarella,\n"                                              # vacío
        "Marbella,trufa,0.1\n"
    )
    orden = (
        "sucursal,ingrediente_id,cantidad_formatos\n"
        "Marbella,harina,2\n"
        "Marbella,trufa,4\n"
        "Marbella,mozzarella,-3\n"                                            # negativo
        "Marbella,polvo_magico,1\n"                                           # fuera de catálogo
        "Marbella,roto,2\n"                                                   # factor inválido
    )
    carpeta = _escribir_dataset(tmp_path / "datos", catalogo, consumo, inventario, orden)
    return L.cargar_datos(carpeta)


def test_carga_tolera_bom_espacios_y_valores_ilegibles(dataset_raro):
    assert "harina" in set(dataset_raro.catalogo["ingrediente_id"])       # sin espacios
    assert "Marbella" in set(dataset_raro.consumo["sucursal"])            # sin espacios
    assert dataset_raro.consumo["consumo_unidad_base"].notna().all()      # 'abc' descartado
    assert (dataset_raro.inventario["stock_actual_unidad_base"] >= 0).all()
    assert dataset_raro.avisos, "La carga debería reportar los problemas encontrados"


def test_alertas_con_datos_raros_no_lanzan_excepcion(dataset_raro):
    alertas = L.construir_alertas(dataset_raro, metodo=L.METODO_INTELIGENTE)
    assert not alertas.empty
    assert alertas["mensaje"].notna().all()

    # Ingrediente en la orden pero fuera del catálogo -> no verificable.
    assert fila(alertas, "Marbella", "polvo_magico")["tipo"] == L.DATO_RARO
    # Factor de conversión inválido: se descarta del catálogo -> tampoco verificable.
    assert fila(alertas, "Marbella", "roto")["tipo"] == L.DATO_RARO
    # Cantidad negativa: no se puede asumir 0 (inventaría un olvido) -> no verificable.
    assert fila(alertas, "Marbella", "mozzarella")["tipo"] == L.DATO_RARO


def test_ingrediente_sin_historico_se_informa(dataset_raro):
    """Está en catálogo y en la orden, pero nunca se consumió: se puede convertir,
    no proyectar. Se informa en vez de inventar un número."""
    alertas = L.construir_alertas(dataset_raro)
    r = fila(alertas, "Marbella", "trufa")
    assert r["tipo"] == L.SIN_HISTORIAL
    assert r["pedido_base"] == pytest.approx(4 * 0.05)   # sí se convierte
    assert pd.isna(r["consumo_proyectado"])              # no se proyecta
    assert "no hay consumo histórico" in r["mensaje"]


def test_stock_faltante_se_asume_cero(dataset_raro):
    """Sin fila de inventario, el supuesto conservador es stock = 0."""
    alertas = L.construir_alertas(dataset_raro)
    sin_stock = alertas[alertas["ingrediente_id"] == "polvo_magico"].iloc[0]
    assert sin_stock["stock_actual"] == 0.0


def test_orden_vacia_convierte_todo_el_consumo_en_olvidos(datos):
    """Caso extremo: una sucursal no manda nada. Todo lo que consume y no tiene
    stock suficiente debe aparecer como olvido, sin romper nada."""
    vacia = L.Datos(datos.catalogo, datos.consumo, datos.inventario,
                    datos.orden.iloc[0:0], [])
    alertas = L.construir_alertas(vacia)
    assert set(alertas["tipo"]) <= {L.OLVIDO, L.OK}
    assert (alertas["tipo"] == L.OLVIDO).sum() > 50


# ---------------------------------------------------------------------------
# Salida para el dashboard
# ---------------------------------------------------------------------------


def test_las_alertas_vienen_ordenadas_por_severidad(alertas_promedio):
    severidades = alertas_promedio["severidad"].tolist()
    assert severidades == sorted(severidades)
    primeros = alertas_promedio.head(2)["tipo"].tolist()
    assert primeros == [L.PIDE_MENOS, L.PIDE_MENOS]  # los quiebres van arriba


def test_los_mensajes_son_accionables_y_en_espanol(alertas_promedio):
    problemas = L.solo_alertas(alertas_promedio)
    assert len(problemas) == 6
    for _, r in problemas.iterrows():
        assert r["sucursal"] in r["mensaje"]
        assert r["mensaje"][0] in "🔴🟠🟡🔵⚪"


def test_los_filtros_del_dashboard_funcionan(alertas_promedio):
    solo_costa = L.filtrar(alertas_promedio, sucursales=["Costa del Este"])
    assert set(solo_costa["sucursal"]) == {"Costa del Este"}
    solo_quiebres = L.filtrar(alertas_promedio, tipos=[L.PIDE_MENOS])
    assert set(solo_quiebres["tipo"]) == {L.PIDE_MENOS}
    vacio = L.filtrar(alertas_promedio, sucursales=["Sucursal Inexistente"])
    assert vacio.empty
    assert L.resumen_kpis(vacio)["total_alertas"] == 0  # KPIs no fallan con 0 filas


def test_el_toggle_de_proyeccion_cambia_el_resultado(alertas_promedio, alertas_inteligente):
    """Si ambos métodos dieran lo mismo, el toggle no tendría sentido."""
    kpis_simple = L.resumen_kpis(alertas_promedio)
    kpis_listo = L.resumen_kpis(alertas_inteligente)
    assert kpis_simple["quiebres"] == 2
    assert kpis_listo["quiebres"] == 1  # se cae el falso quiebre de Marbella


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
