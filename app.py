"""
Dashboard de órdenes de compra · Barrio Pizza.

Solo presentación: toda la lógica de negocio vive en `logica.py` y `proveedores.py`.
Correr con:  streamlit run app.py
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import logica as L
import proveedores as P

st.set_page_config(
    page_title="Órdenes de compra · Barrio Pizza",
    page_icon="🍕",
    layout="wide",
)

# Mismo formateo de números que usan los mensajes de alerta, para no mostrar
# "10" en la frase y "10.0" en la tarjeta.
_fmt = L._fmt

DESCRIPCIONES_TIPO = {
    L.PIDE_MENOS: "pidió menos de lo que va a necesitar → puede quedarse sin producto",
    L.OLVIDO: "no lo pidió, lo consume y el stock no alcanza",
    L.PIDE_MAS: "pidió más de lo necesario → plata inmovilizada y riesgo de merma",
    L.SIN_HISTORIAL: "no hay consumo previo para validar la cantidad",
    L.DATO_RARO: "no está en el catálogo → hay que revisarlo a mano",
    L.OK: "la cantidad pedida coincide con lo proyectado",
}

AYUDA_METODO = {
    L.METODO_PROMEDIO: "Promedio de las 6 semanas de histórico. Simple, pero una "
                       "semana atípica lo distorsiona y no ve las tendencias.",
    L.METODO_INTELIGENTE: "Descarta las semanas atípicas (mediana ± 3·MAD) y, si "
                          "hay una tendencia real, la proyecta a la semana 7.",
}


# ---------------------------------------------------------------------------
# Datos (en caché: no se recalcula en cada interacción)
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner="Cargando datos…")
def cargar() -> L.Datos:
    return L.cargar_datos()


@st.cache_data(show_spinner=False)
def alertas_de(metodo: str, buffer: float) -> pd.DataFrame:
    return L.construir_alertas(cargar(), metodo=metodo, buffer=buffer)


@st.cache_data(show_spinner=False)
def historico_de(sucursal: str, ingrediente_id: str) -> pd.DataFrame:
    consumo = cargar().consumo
    return consumo[(consumo["sucursal"] == sucursal) &
                   (consumo["ingrediente_id"] == ingrediente_id)]


# ---------------------------------------------------------------------------
# Piezas visuales
# ---------------------------------------------------------------------------


def tinte(color_hex: str, alfa: float = 0.13) -> str:
    """Color de fondo suave, legible tanto en tema claro como oscuro."""
    h = color_hex.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alfa})"


def sin_icono(mensaje: str) -> str:
    """El icono ya va en la etiqueta de la tarjeta; no hace falta repetirlo."""
    for icono in L.ICONOS_TIPO.values():
        if mensaje.startswith(icono):
            return mensaje[len(icono):].strip()
    return mensaje


def numeros_clave(fila: pd.Series) -> list[tuple[str, str]]:
    """Los 3 números que la gerente necesita para decidir, en formatos."""
    tipo = fila["tipo"]
    if tipo == L.DATO_RARO:
        pedidos = fila["formatos_pedidos"]
        return [("Pidió", "?" if pd.isna(pedidos) else f"{_fmt(pedidos)} formatos"),
                ("Necesita", "no se puede calcular")]
    if tipo == L.SIN_HISTORIAL:
        return [("Pidió", f"{_fmt(fila['formatos_pedidos'])}"),
                ("Necesita", "sin histórico"),
                ("Stock actual", f"{_fmt(fila['stock_actual'])} {fila['unidad_base']}")]

    delta = fila["delta_formatos"]
    signo = "+" if delta > 0 else ""
    return [("Necesita", _fmt(fila["formatos_recomendados"])),
            ("Pidió", _fmt(fila["formatos_pedidos"])),
            ("Diferencia", f"{signo}{_fmt(delta)}")]


def tarjeta_html(fila: pd.Series) -> str:
    """Tarjeta de alerta. Se arma en UNA sola línea: si el HTML llevara saltos de
    línea con sangría, Markdown interpretaría esas líneas como bloque de código."""
    tipo = fila["tipo"]
    color = L.COLORES_TIPO[tipo]
    chips = "".join(
        f'<span style="margin-right:1.1rem;white-space:nowrap;">'
        f'<span style="opacity:.7;">{html.escape(etiqueta)}:</span> '
        f'<b>{html.escape(valor)}</b></span>'
        for etiqueta, valor in numeros_clave(fila)
    )
    insignia = (
        f'<span style="background:{color};color:#fff;border-radius:4px;'
        f'padding:.08rem .45rem;font-size:.72rem;margin-right:.5rem;'
        f'white-space:nowrap;">{L.ICONOS_TIPO[tipo]} '
        f'{html.escape(L.ETIQUETAS_TIPO[tipo].upper())}</span>'
    )
    encabezado = (
        '<div style="display:flex;justify-content:space-between;gap:1rem;'
        'flex-wrap:wrap;align-items:baseline;margin-bottom:.4rem;">'
        f'<span style="font-weight:600;">{insignia}'
        f'{html.escape(str(fila["sucursal"]))} · {html.escape(str(fila["nombre"]))}</span>'
        f'<span style="font-size:.78rem;opacity:.7;">'
        f'{html.escape(str(fila["proveedor"]))} · '
        f'{html.escape(str(fila["formato_compra"]))}</span></div>'
    )
    cuerpo = (
        f'<div style="font-size:.95rem;line-height:1.45;margin-bottom:.5rem;">'
        f'{html.escape(sin_icono(str(fila["mensaje"])))}</div>'
    )
    return (
        f'<div style="border-left:6px solid {color};background:{tinte(color)};'
        f'border-radius:6px;padding:.7rem 1rem;margin:.35rem 0 .1rem 0;">'
        f'{encabezado}{cuerpo}<div style="font-size:.82rem;">{chips}</div></div>'
    )


def grafico_historico(fila: pd.Series) -> None:
    """Las 6 semanas de consumo + la proyección, con la semana atípica marcada."""
    historico = historico_de(fila["sucursal"], fila["ingrediente_id"])
    if historico.empty:
        return
    descartadas = {s.strip() for s in str(fila["semanas_descartadas"]).split(",") if s.strip()}

    df = pd.DataFrame({
        "Semana": ["S" + str(int(s)) for s in historico["semana_num"]],
        "consumo": historico["consumo_unidad_base"].astype(float).values,
    })
    df["Consumo real"] = df.apply(
        lambda r: None if r["Semana"] in descartadas else r["consumo"], axis=1)
    df["Semana atípica (descartada)"] = df.apply(
        lambda r: r["consumo"] if r["Semana"] in descartadas else None, axis=1)

    proyeccion = fila["consumo_proyectado"]
    if pd.notna(proyeccion):
        siguiente = "S" + str(int(historico["semana_num"].max()) + 1)
        df = pd.concat([df, pd.DataFrame([{"Semana": siguiente, "Proyección": float(proyeccion)}])],
                       ignore_index=True)

    columnas = [c for c in ("Consumo real", "Semana atípica (descartada)", "Proyección")
                if c in df.columns and df[c].notna().any()]
    colores = {"Consumo real": "#5b8fc9",
               "Semana atípica (descartada)": "#d64545",
               "Proyección": "#4c9a5c"}
    st.bar_chart(df.set_index("Semana")[columnas],
                 color=[colores[c] for c in columnas],
                 height=200)


def detalle_calculo(fila: pd.Series, metodo: str) -> None:
    """El paso a paso del número, para que la alerta no sea una caja negra."""
    unidad = fila["unidad_base"]
    if fila["tipo"] == L.DATO_RARO:
        st.markdown(
            f"**`{fila['ingrediente_id']}` no está en el catálogo de insumos.** Sin catálogo "
            "no hay factor de conversión ni consumo histórico, así que no se puede validar "
            "la cantidad ni convertirla a kg/L/unidades.\n\n"
            "Puede ser un insumo nuevo que falta cargar, o un error de tipeo en la orden."
        )
        return

    lineas = []
    if pd.notna(fila["consumo_proyectado"]):
        lineas.append(
            f"- **Consumo proyectado:** {_fmt(fila['consumo_proyectado'], 2)} {unidad} "
            f"· _{L.ETIQUETAS_METODO[metodo]}_"
        )
        if fila["detalle_proyeccion"]:
            lineas.append(f"    - {fila['detalle_proyeccion']}")
        lineas.append(f"    - Semanas usadas: {fila['semanas_usadas'] or '—'}")
        lineas.append(f"    - Semanas descartadas: {fila['semanas_descartadas'] or 'ninguna'}")
    else:
        lineas.append("- **Consumo proyectado:** no hay histórico para este insumo.")

    lineas.append(f"- **Stock actual:** {_fmt(fila['stock_actual'], 2)} {unidad}")

    if pd.notna(fila["necesidad_base"]):
        lineas.append(
            f"- **Necesidad** = proyección − stock = **{_fmt(fila['necesidad_base'], 2)} {unidad}** "
            "_(nunca negativa: si el stock alcanza, no hay que comprar)_"
        )
        lineas.append(
            f"- **Formatos recomendados** = ⌈{_fmt(fila['necesidad_base'], 2)} ÷ "
            f"{_fmt(fila['unidad_base_por_formato'], 3)}⌉ = "
            f"**{_fmt(fila['formatos_recomendados'])}** ({fila['formato_compra']}) "
            "_(se redondea hacia arriba: no existe medio formato)_"
        )
    if pd.notna(fila["pedido_base"]):
        lineas.append(
            f"- **Lo pedido:** {_fmt(fila['formatos_pedidos'])} × "
            f"{_fmt(fila['unidad_base_por_formato'], 3)} = "
            f"{_fmt(fila['pedido_base'], 2)} {unidad}"
        )
    st.markdown("\n".join(lineas))

    if fila["tipo"] in (L.PIDE_MAS, L.PIDE_MENOS, L.OLVIDO, L.OK):
        grafico_historico(fila)


def leyenda(tipos_visibles: list[str]) -> None:
    chips = "".join(
        f'<span style="background:{tinte(L.COLORES_TIPO[t])};'
        f'border-left:4px solid {L.COLORES_TIPO[t]};border-radius:4px;'
        f'padding:.28rem .6rem;font-size:.78rem;">'
        f'<b>{L.ICONOS_TIPO[t]} {html.escape(L.ETIQUETAS_TIPO[t])}</b> — '
        f'{html.escape(DESCRIPCIONES_TIPO[t])}</span>'
        for t in tipos_visibles
    )
    st.markdown(
        f'<div style="display:flex;gap:.5rem;flex-wrap:wrap;margin:.2rem 0 1rem 0;">{chips}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Carga inicial
# ---------------------------------------------------------------------------

try:
    datos = cargar()
except (FileNotFoundError, ValueError) as error:
    st.error(f"No se pudieron cargar los datos: {error}")
    st.info("Revisá que la carpeta `datos/` tenga los 4 CSV del reto.")
    st.stop()


# ---------------------------------------------------------------------------
# Barra lateral: método de proyección y filtros
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("🍕 Barrio Pizza")
    st.subheader("Método de proyección")
    metodo = st.radio(
        "¿Cómo estimamos el consumo de la próxima semana?",
        options=[L.METODO_PROMEDIO, L.METODO_INTELIGENTE],
        format_func=lambda m: L.ETIQUETAS_METODO[m],
        index=1,
        label_visibility="collapsed",
    )
    st.caption(AYUDA_METODO[metodo])
    otro_metodo = L.METODO_INTELIGENTE if metodo == L.METODO_PROMEDIO else L.METODO_PROMEDIO

    with st.expander("⚙️ Ajustes avanzados"):
        buffer_pct = st.slider(
            "Colchón de seguridad", min_value=0, max_value=30, value=int(L.BUFFER * 100), step=5,
            format="%d%%",
            help="Pide un % extra sobre el consumo proyectado para absorber imprevistos. "
                 "0% = exactamente lo proyectado.",
        )
    buffer = buffer_pct / 100.0

# Se calculan acá porque la lista de proveedores del filtro sale de las alertas
# (incluye "Desconocido" para lo que no está en el catálogo).
alertas_completas = alertas_de(metodo, buffer)
alertas_otro_metodo = alertas_de(otro_metodo, buffer)
sucursales_disponibles = sorted(datos.consumo["sucursal"].dropna().unique())
proveedores_disponibles = sorted(alertas_completas["proveedor"].dropna().unique())

with st.sidebar:
    st.divider()
    st.subheader("Filtros")
    sucursales = st.multiselect("Sucursal", sucursales_disponibles,
                                default=sucursales_disponibles)
    tipos = st.multiselect(
        "Tipo de alerta",
        options=list(L.TIPOS_ALERTA),
        default=list(L.TIPOS_ALERTA),
        format_func=lambda t: f"{L.ICONOS_TIPO[t]} {L.ETIQUETAS_TIPO[t]}",
    )
    proveedores_sel = st.multiselect("Proveedor", proveedores_disponibles,
                                     default=proveedores_disponibles)
    mostrar_ok = st.checkbox("Mostrar también lo que está OK", value=False)

    st.divider()
    if datos.avisos:
        with st.expander(f"⚠️ Avisos de calidad de datos ({len(datos.avisos)})"):
            for aviso in datos.avisos:
                st.caption(f"• {aviso}")
    else:
        st.caption("✅ Los 4 archivos cargaron sin problemas de formato.")
    st.caption(f"Insumos en catálogo: {len(datos.catalogo)} · "
               f"Pares (sucursal, insumo) revisados: {len(alertas_completas)}")

tipos_activos = list(tipos) + ([L.OK] if mostrar_ok else [])
filtradas = L.filtrar(alertas_completas, sucursales, tipos_activos, proveedores_sel)
filtradas_otro = L.filtrar(alertas_otro_metodo, sucursales, tipos_activos, proveedores_sel)

kpis = L.resumen_kpis(filtradas)
kpis_otro = L.resumen_kpis(filtradas_otro)


# ---------------------------------------------------------------------------
# Encabezado y KPIs
# ---------------------------------------------------------------------------

st.title("Revisión de órdenes de compra")
st.caption(
    f"Semana en curso · {len(sucursales)} de {len(sucursales_disponibles)} sucursales · "
    f"Proyección: **{L.ETIQUETAS_METODO[metodo]}**"
    + (f" · Colchón de seguridad: {buffer_pct}%" if buffer_pct else "")
)


def metrica(columna, etiqueta: str, clave: str, ayuda: str) -> None:
    """KPI con la diferencia contra el otro método de proyección."""
    diferencia = kpis[clave] - kpis_otro[clave]
    columna.metric(
        etiqueta,
        kpis[clave],
        delta=None if diferencia == 0 else diferencia,
        delta_color="inverse",  # menos alertas es mejor: se muestra en verde
        help=f"{ayuda} · La flecha compara contra «{L.ETIQUETAS_METODO[otro_metodo]}».",
        border=True,
    )


# Las dos últimas columnas van más anchas: sus textos son los más largos.
c1, c2, c3, c4, c5, c6 = st.columns([1, 1, 1, 1, 1.3, 1.5])
metrica(c1, "Alertas totales", "total_alertas", "Líneas de la orden que requieren atención.")
metrica(c2, "🔴 Quiebres", "quiebres", "Pidió menos de lo necesario.")
metrica(c3, "🟡 Excesos", "excesos", "Pidió más de lo necesario.")
metrica(c4, "🟠 Olvidos", "olvidos", "No lo pidió y el stock no alcanza.")
metrica(c5, "⚪ No verificables", "no_verificables", "No están en el catálogo.")
peor = kpis["alertas_peor_sucursal"]
c6.metric("Sucursal con más alertas",
          f"{peor} alerta{'s' if peor != 1 else ''}" if peor else "—",
          delta=kpis["sucursal_mas_alertas"] if peor else None,
          delta_color="off", border=True)

st.markdown("##### Cómo leer esto")
leyenda(list(L.TIPOS_ALERTA) + ([L.OK] if mostrar_ok else []))


# ---------------------------------------------------------------------------
# Contenido principal
# ---------------------------------------------------------------------------

tab_alertas, tab_proveedores = st.tabs(["🚨 Alertas", "📦 Pedido corregido por proveedor"])

with tab_alertas:
    if filtradas.empty:
        st.success("No hay alertas con los filtros elegidos. Toda la orden está en orden 👌")
    else:
        for tipo in sorted(set(filtradas["tipo"]), key=lambda t: L.SEVERIDAD[t]):
            grupo = filtradas[filtradas["tipo"] == tipo]
            st.markdown(
                f"#### {L.ICONOS_TIPO[tipo]} {L.ETIQUETAS_TIPO[tipo]} "
                f"<span style='opacity:.55;font-size:.8em;'>({len(grupo)})</span>",
                unsafe_allow_html=True,
            )
            for _, alerta in grupo.iterrows():
                st.markdown(tarjeta_html(alerta), unsafe_allow_html=True)
                with st.expander("Ver cómo se calculó"):
                    detalle_calculo(alerta, metodo)
            st.write("")

with tab_proveedores:
    st.markdown(
        "Esta es la orden **ya corregida**: reemplaza las cantidades pedidas por las "
        "recomendadas y las reagrupa por proveedor, que es como se envían las órdenes."
    )
    # El pedido corregido se arma sobre TODO lo revisado (se ignora el filtro de
    # tipo de alerta): si no, se caerían de la orden los insumos que ya estaban
    # bien pedidos y el proveedor recibiría solo las correcciones.
    pedido = P.pedido_corregido(
        L.filtrar(alertas_completas, sucursales, None, proveedores_sel)
    )

    if pedido.empty:
        st.info("No hay nada para pedir con los filtros elegidos.")
    else:
        resumen = P.resumen_proveedores(pedido)
        cambios = P.comparar_con_lo_pedido(pedido)
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Proveedores", len(resumen), border=True)
        col_b.metric("Formatos a comprar", _fmt(pedido["formatos_a_pedir"].sum()), border=True)
        col_c.metric("Líneas corregidas", len(cambios),
                     help="Líneas donde la cantidad recomendada difiere de la pedida.",
                     border=True)

        proveedor = st.selectbox(
            "Elegí el proveedor para ver su orden",
            options=resumen["proveedor"].tolist(),
            format_func=lambda p: f"{p} — {int(resumen.loc[resumen['proveedor'] == p, 'insumos'].iloc[0])} insumos",
        )
        matriz = P.matriz_proveedor(pedido, proveedor)
        st.dataframe(matriz, hide_index=True, width="stretch")
        st.download_button(
            f"⬇️ Descargar la orden de {proveedor} (CSV)",
            data=P.a_csv(matriz),
            file_name=f"pedido_{proveedor.lower().replace(' ', '_').replace('.', '')}.csv",
            mime="text/csv",
        )

        with st.expander("Ver el detalle completo de todos los proveedores"):
            st.dataframe(
                pedido.rename(columns={
                    "proveedor": "Proveedor", "nombre": "Insumo",
                    "formato_compra": "Formato", "sucursal": "Sucursal",
                    "formatos_pedidos": "Pidió", "formatos_a_pedir": "Debería pedir",
                    "cambio_vs_pedido": "Corrección", "total_unidad_base": "Total (unidad base)",
                    "unidad_base": "Unidad", "origen": "Origen",
                })[["Proveedor", "Insumo", "Formato", "Sucursal", "Pidió",
                    "Debería pedir", "Corrección", "Total (unidad base)", "Unidad", "Origen"]],
                hide_index=True, width="stretch",
            )
            st.download_button(
                "⬇️ Descargar el pedido corregido completo (CSV)",
                data=P.a_csv(pedido),
                file_name="pedido_corregido_completo.csv",
                mime="text/csv",
            )

        sin_proveedor = filtradas[filtradas["tipo"] == L.DATO_RARO]
        if not sin_proveedor.empty:
            st.warning(
                f"Quedaron fuera {len(sin_proveedor)} línea(s) sin proveedor conocido "
                f"({', '.join(sorted(set(sin_proveedor['ingrediente_id'])))}): "
                "no están en el catálogo, hay que resolverlas a mano."
            )
