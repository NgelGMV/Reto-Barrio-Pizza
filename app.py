"""
Dashboard de órdenes de compra · Barrio Pizza.

Solo presentación: toda la lógica de negocio vive en `logica.py`, `proveedores.py`,
`anomalias.py` y `chat.py`.

Correr con:  streamlit run app.py
"""

from __future__ import annotations

import base64
import html
import io
import os
from pathlib import Path

import pandas as pd
import streamlit as st

import anomalias as AN
import chat as CH
import logica as L
import proveedores as P

CARPETA_ASSETS = Path(__file__).parent / "assets"
EXTENSIONES_IMAGEN = (".png", ".jpg", ".jpeg", ".webp", ".svg")


def buscar_imagen(base: str) -> Path | None:
    """Busca `assets/<base>.<ext>`. Los assets son opcionales: si no están, la app
    arranca igual en vez de romperse por una imagen faltante."""
    for extension in EXTENSIONES_IMAGEN:
        ruta = CARPETA_ASSETS / f"{base}{extension}"
        if ruta.is_file():
            return ruta
    return None


# Sobre fondo negro conviene la versión del logo en blanco (`icono`); la versión
# en negro solo se ve si se la monta sobre una tarjeta blanca.
ICONO = buscar_imagen("icono")
LOGO = ICONO or buscar_imagen("logo")

st.set_page_config(
    page_title="Órdenes de compra · Barrio Pizza",
    page_icon=str(ICONO) if ICONO else "🍕",
    layout="wide",
)

# Mismo formateo de números que usan los mensajes de alerta, para no mostrar
# "10" en la frase y "10.0" en la tarjeta.
_fmt = L._fmt

DESCRIPCIONES_TIPO = {
    L.PIDE_MENOS: "pidió menos de lo que va a necesitar",
    L.OLVIDO: "no lo pidió y el stock no alcanza",
    L.PIDE_MAS: "pidió más de lo necesario",
    L.SIN_HISTORIAL: "no hay consumo previo para validarlo",
    L.DATO_RARO: "no está en el catálogo",
    L.OK: "la cantidad pedida es la correcta",
}

AYUDA_METODO = {
    L.METODO_PROMEDIO: "Promedio de las 6 semanas. Una semana atípica lo distorsiona.",
    L.METODO_INTELIGENTE: "Descarta semanas atípicas y proyecta la tendencia real.",
}

GRIS = "#9A9AA2"
BORDE = "#26262B"


# ---------------------------------------------------------------------------
# Estilos
# ---------------------------------------------------------------------------

st.markdown("""
<style>
  section[data-testid="stSidebar"] { width: 340px !important; }
  .block-container { padding-top: 2.2rem; padding-bottom: 3rem; }

  /* El primario del tema es blanco, así que las etiquetas de los filtros salen
     blancas sobre blanco. Hay que forzarles el texto oscuro. */
  span[data-baseweb="tag"], span[data-baseweb="tag"] span { color:#0A0A0B !important; }
  span[data-baseweb="tag"] svg { fill:#0A0A0B !important; }

  .fila { display:flex; gap:.7rem; flex-wrap:wrap; margin-bottom:1.1rem; }

  /* Tarjetas de KPI */
  .kpi { flex:1 1 150px; background:#151517; border:1px solid #26262B;
         border-radius:12px; padding:.85rem 1rem; position:relative; overflow:hidden; }
  .kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:3px;
                 background:var(--acc, #3A3A40); }
  .kpi-num { font-size:2.1rem; font-weight:700; line-height:1.1; color:var(--acc,#F0EFEC); }
  .kpi-lab { font-size:.74rem; text-transform:uppercase; letter-spacing:.06em;
             color:#9A9AA2; margin-top:.15rem; }
  .kpi-del { font-size:.72rem; color:#8A8A92; margin-top:.35rem; }

  /* Tarjetas de sucursal */
  .suc { flex:1 1 190px; background:#151517; border:1px solid #26262B;
         border-top:3px solid var(--acc,#4FCB7B); border-radius:10px; padding:.8rem .95rem; }
  .suc-nom { font-weight:600; font-size:.95rem; margin-bottom:.15rem; }
  .suc-est { font-size:.78rem; color:var(--acc,#4FCB7B); font-weight:600;
             text-transform:uppercase; letter-spacing:.05em; }
  .suc-chips { margin-top:.55rem; display:flex; gap:.45rem; flex-wrap:wrap; }
  .suc-chip { font-size:.75rem; background:#1E1E22; border:1px solid #2E2E34;
              border-radius:20px; padding:.1rem .55rem; white-space:nowrap; }

  /* Tarjetas de alerta */
  .alerta { border-left:5px solid var(--acc); background:var(--bg);
            border-radius:10px; padding:.8rem 1rem; margin:.4rem 0 .15rem 0; }
  .alerta-top { display:flex; justify-content:space-between; gap:1rem;
                flex-wrap:wrap; align-items:baseline; margin-bottom:.45rem; }
  .alerta-tit { font-weight:600; }
  .insignia { background:var(--acc); color:#0A0A0B; border-radius:5px;
              padding:.1rem .5rem; font-size:.7rem; font-weight:700;
              margin-right:.55rem; white-space:nowrap; letter-spacing:.03em; }
  .alerta-meta { font-size:.76rem; color:#8A8A92; }
  .alerta-msg { font-size:.95rem; line-height:1.5; margin-bottom:.55rem; }
  .alerta-nums span { margin-right:1.2rem; font-size:.82rem; white-space:nowrap; }
  .alerta-nums i { font-style:normal; color:#8A8A92; }

  /* Leyenda */
  .leyenda { display:flex; gap:.45rem; flex-wrap:wrap; margin:.1rem 0 1.3rem 0; }
  .leyenda span { font-size:.76rem; border:1px solid #26262B; border-left:3px solid var(--acc);
                  border-radius:6px; padding:.25rem .6rem; color:#B8B8BE; }
  .leyenda b { color:#F0EFEC; font-weight:600; }

  .titulo-seccion { font-size:.78rem; text-transform:uppercase; letter-spacing:.09em;
                    color:#8A8A92; margin:.2rem 0 .5rem 0; font-weight:600; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Datos y secretos
# ---------------------------------------------------------------------------

CLAVE_ORDEN = "orden_csv"  # orden modificada desde la UI, si la hay


@st.cache_data(show_spinner="Cargando datos…")
def cargar(orden_csv: str | None = None) -> L.Datos:
    """Los datos ya limpios.

    `orden_csv` es la orden modificada desde la UI, serializada a texto: así
    Streamlit puede usarla de clave de caché.
    """
    if orden_csv is None:
        return L.cargar_datos()
    return L.cargar_datos(orden_df=pd.read_csv(io.StringIO(orden_csv)))


@st.cache_data(show_spinner=False)
def alertas_de(metodo: str, buffer: float, orden_csv: str | None = None) -> pd.DataFrame:
    return L.construir_alertas(cargar(orden_csv), metodo=metodo, buffer=buffer)


@st.cache_data(show_spinner=False)
def historico_de(sucursal: str, ingrediente_id: str) -> pd.DataFrame:
    # El histórico no depende de la orden, así que siempre sale de los CSV.
    consumo = cargar().consumo
    return consumo[(consumo["sucursal"] == sucursal) &
                   (consumo["ingrediente_id"] == ingrediente_id)]


def _secreto(nombre: str) -> str | None:
    """Lee un secreto de Streamlit y, si no hay, del entorno.

    `st.secrets` levanta excepción cuando no existe el archivo, que es el caso
    normal al correr en local sin chat: por eso el try.
    """
    try:
        valor = st.secrets.get(nombre)
    except Exception:
        valor = None
    return valor or os.environ.get(nombre)


# ---------------------------------------------------------------------------
# Piezas visuales
# ---------------------------------------------------------------------------


def tinte(color_hex: str, alfa: float = 0.10) -> str:
    h = color_hex.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alfa})"


@st.cache_data(show_spinner=False)
def logo_html(ruta: Path) -> str:
    """El logo incrustado en base64. Se incrusta en el HTML en vez de usar
    `st.image` para poder controlar el tamaño y el centrado."""
    tipos = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".svg": "image/svg+xml"}
    mime = tipos.get(ruta.suffix.lower(), "image/png")
    datos = base64.b64encode(ruta.read_bytes()).decode("ascii")
    return (
        '<div style="display:flex;justify-content:center;margin:.2rem 0 1.2rem 0;">'
        f'<img src="data:{mime};base64,{datos}" alt="Barrio Pizza" '
        'style="width:100%;max-width:150px;height:auto;border-radius:10px;"></div>'
    )


def sin_icono(mensaje: str) -> str:
    """El icono ya va en la insignia de la tarjeta; no hace falta repetirlo."""
    for icono in L.ICONOS_TIPO.values():
        if mensaje.startswith(icono):
            return mensaje[len(icono):].strip()
    return mensaje


def numeros_clave(fila: pd.Series) -> list[tuple[str, str]]:
    """Los números que la gerente necesita para decidir, siempre en formatos."""
    tipo = fila["tipo"]
    if tipo == L.DATO_RARO:
        pedidos = fila["formatos_pedidos"]
        return [("Pidió", "?" if pd.isna(pedidos) else f"{_fmt(pedidos)} formatos"),
                ("Necesita", "no se puede calcular")]
    if tipo == L.SIN_HISTORIAL:
        return [("Pidió", _fmt(fila["formatos_pedidos"])),
                ("Necesita", "sin histórico"),
                ("Stock", f"{_fmt(fila['stock_actual'])} {fila['unidad_base']}")]
    delta = fila["delta_formatos"]
    return [("Necesita", _fmt(fila["formatos_recomendados"])),
            ("Pidió", _fmt(fila["formatos_pedidos"])),
            ("Diferencia", f"{'+' if delta > 0 else ''}{_fmt(delta)}")]


def tarjeta_html(fila: pd.Series) -> str:
    """Tarjeta de alerta, en una sola línea de HTML: si llevara saltos con
    sangría, Markdown interpretaría esas líneas como bloque de código."""
    tipo = fila["tipo"]
    color = L.COLORES_TIPO[tipo]
    numeros = "".join(
        f'<span><i>{html.escape(etiqueta)}</i> <b>{html.escape(valor)}</b></span>'
        for etiqueta, valor in numeros_clave(fila)
    )
    return (
        f'<div class="alerta" style="--acc:{color};--bg:{tinte(color)}">'
        f'<div class="alerta-top"><span class="alerta-tit">'
        f'<span class="insignia">{L.ICONOS_TIPO[tipo]} '
        f'{html.escape(L.ETIQUETAS_TIPO[tipo].upper())}</span>'
        f'{html.escape(str(fila["sucursal"]))} · {html.escape(str(fila["nombre"]))}</span>'
        f'<span class="alerta-meta">{html.escape(str(fila["proveedor"]))} · '
        f'{html.escape(str(fila["formato_compra"]))}</span></div>'
        f'<div class="alerta-msg">{html.escape(sin_icono(str(fila["mensaje"])))}</div>'
        f'<div class="alerta-nums">{numeros}</div></div>'
    )


def fila_kpis(kpis: dict, kpis_otro: dict, etiqueta_otro: str) -> str:
    """Los números de arriba, con el contraste contra el otro método."""
    definicion = [
        ("total_alertas", "Alertas totales", "#F0EFEC"),
        ("quiebres", "Riesgo de quiebre", L.COLORES_TIPO[L.PIDE_MENOS]),
        ("olvidos", "Olvidos", L.COLORES_TIPO[L.OLVIDO]),
        ("excesos", "Excesos", L.COLORES_TIPO[L.PIDE_MAS]),
        ("no_verificables", "No verificables", L.COLORES_TIPO[L.DATO_RARO]),
    ]
    tarjetas = []
    for clave, etiqueta, color in definicion:
        valor = kpis[clave]
        diferencia = valor - kpis_otro[clave]
        if diferencia:
            flecha = "↑" if diferencia > 0 else "↓"
            delta = f'{flecha} {abs(diferencia)} vs. {html.escape(etiqueta_otro.lower())}'
        else:
            delta = "sin cambios con el otro método"
        tarjetas.append(
            f'<div class="kpi" style="--acc:{color}"><div class="kpi-num">{valor}</div>'
            f'<div class="kpi-lab">{html.escape(etiqueta)}</div>'
            f'<div class="kpi-del">{delta}</div></div>'
        )
    return f'<div class="fila">{"".join(tarjetas)}</div>'


def panel_sucursales(alertas: pd.DataFrame, sucursales: list[str]) -> str:
    """El estado de cada sucursal de un vistazo: es la vista que más rápido
    contesta la pregunta real de la gerente, que es a quién hay que llamar."""
    tarjetas = []
    for sucursal in sucursales:
        de_la_sucursal = alertas[alertas["sucursal"] == sucursal]
        conteos = de_la_sucursal["tipo"].value_counts()
        total = int(sum(conteos.get(t, 0) for t in L.TIPOS_ALERTA))

        if total == 0:
            color, estado = L.COLORES_TIPO[L.OK], "Todo en orden"
        else:
            # El color lo manda la alerta más grave que tenga la sucursal.
            peor = min((t for t in L.TIPOS_ALERTA if conteos.get(t, 0)),
                       key=lambda t: L.SEVERIDAD[t])
            color = L.COLORES_TIPO[peor]
            estado = f"{total} alerta{'s' if total != 1 else ''}"

        chips = "".join(
            f'<span class="suc-chip" style="color:{L.COLORES_TIPO[t]}">'
            f'{L.ICONOS_TIPO[t]} {int(conteos[t])}</span>'
            for t in L.TIPOS_ALERTA if conteos.get(t, 0)
        ) or '<span class="suc-chip">Nada que corregir</span>'

        tarjetas.append(
            f'<div class="suc" style="--acc:{color}">'
            f'<div class="suc-nom">{html.escape(str(sucursal))}</div>'
            f'<div class="suc-est">{html.escape(estado)}</div>'
            f'<div class="suc-chips">{chips}</div></div>'
        )
    return f'<div class="fila">{"".join(tarjetas)}</div>'


def leyenda(tipos_visibles: list[str]) -> str:
    chips = "".join(
        f'<span style="--acc:{L.COLORES_TIPO[t]}"><b>{L.ICONOS_TIPO[t]} '
        f'{html.escape(L.ETIQUETAS_TIPO[t])}</b> — '
        f'{html.escape(DESCRIPCIONES_TIPO[t])}</span>'
        for t in tipos_visibles
    )
    return f'<div class="leyenda">{chips}</div>'


def grafico_historico(fila: pd.Series) -> None:
    """Las 6 semanas de consumo y la proyección, con la semana atípica marcada."""
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
        df = pd.concat([df, pd.DataFrame([{"Semana": siguiente,
                                           "Proyección": float(proyeccion)}])],
                       ignore_index=True)

    columnas = [c for c in ("Consumo real", "Semana atípica (descartada)", "Proyección")
                if c in df.columns and df[c].notna().any()]
    colores = {"Consumo real": "#6FB0F0",
               "Semana atípica (descartada)": L.COLORES_TIPO[L.PIDE_MENOS],
               "Proyección": L.COLORES_TIPO[L.OK]}
    st.bar_chart(df.set_index("Semana")[columnas],
                 color=[colores[c] for c in columnas], height=190)


def detalle_calculo(fila: pd.Series, metodo: str) -> None:
    """El paso a paso del número, para que la alerta no sea una caja negra."""
    unidad = fila["unidad_base"]
    if fila["tipo"] == L.DATO_RARO:
        st.markdown(
            f"**`{fila['ingrediente_id']}` no está en el catálogo de insumos.** Sin "
            "catálogo no hay factor de conversión ni consumo histórico, así que no se "
            "puede validar la cantidad. Puede ser un insumo nuevo sin cargar o un error "
            "de tipeo en la orden."
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
            f"- **Necesidad** = proyección − stock = "
            f"**{_fmt(fila['necesidad_base'], 2)} {unidad}**"
        )
        lineas.append(
            f"- **Formatos** = ⌈{_fmt(fila['necesidad_base'], 2)} ÷ "
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


# ---------------------------------------------------------------------------
# Carga inicial
# ---------------------------------------------------------------------------

orden_csv = st.session_state.get(CLAVE_ORDEN)

try:
    datos = cargar(orden_csv)
except (FileNotFoundError, ValueError) as error:
    st.error(f"No se pudieron cargar los datos: {error}")
    if orden_csv is not None:
        # Si la orden cargada desde la UI es la que rompe, se vuelve sola a la
        # original: la app no puede quedar trabada por un archivo malo.
        del st.session_state[CLAVE_ORDEN]
        st.warning("Se volvió a la orden original del CSV.")
        st.button("Reintentar")
    else:
        st.info("Revisá que la carpeta `datos/` tenga los 4 CSV del reto.")
    st.stop()


# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------

with st.sidebar:
    if LOGO:
        st.markdown(logo_html(LOGO), unsafe_allow_html=True)
    else:
        st.header("🍕 Barrio Pizza")

    st.markdown('<div class="titulo-seccion">Método de proyección</div>',
                unsafe_allow_html=True)
    metodo = st.radio(
        "Método de proyección",
        options=[L.METODO_PROMEDIO, L.METODO_INTELIGENTE],
        format_func=lambda m: L.ETIQUETAS_METODO[m],
        index=1,
        label_visibility="collapsed",
    )
    st.caption(AYUDA_METODO[metodo])
    otro_metodo = L.METODO_INTELIGENTE if metodo == L.METODO_PROMEDIO else L.METODO_PROMEDIO

    with st.expander("⚙️ Ajustes avanzados"):
        buffer_pct = st.slider(
            "Colchón de seguridad", min_value=0, max_value=30,
            value=int(L.BUFFER * 100), step=5, format="%d%%",
            help="Pide un % extra sobre lo proyectado para absorber imprevistos.",
        )
    buffer = buffer_pct / 100.0

# Se calculan acá porque la lista de proveedores del filtro sale de las alertas
# (incluye "Desconocido" para lo que no está en el catálogo).
alertas_completas = alertas_de(metodo, buffer, orden_csv)
alertas_otro_metodo = alertas_de(otro_metodo, buffer, orden_csv)
sucursales_disponibles = sorted(datos.consumo["sucursal"].dropna().unique())
proveedores_disponibles = sorted(alertas_completas["proveedor"].dropna().unique())

with st.sidebar:
    st.markdown('<div class="titulo-seccion">Filtros</div>', unsafe_allow_html=True)
    sucursales = st.multiselect("Sucursal", sucursales_disponibles,
                                default=sucursales_disponibles)
    tipos = st.multiselect(
        "Tipo de alerta", options=list(L.TIPOS_ALERTA), default=list(L.TIPOS_ALERTA),
        format_func=lambda t: f"{L.ICONOS_TIPO[t]} {L.ETIQUETAS_TIPO[t]}",
    )
    proveedores_sel = st.multiselect("Proveedor", proveedores_disponibles,
                                     default=proveedores_disponibles)
    mostrar_ok = st.checkbox("Mostrar también lo que está OK", value=False)

tipos_activos = list(tipos) + ([L.OK] if mostrar_ok else [])
filtradas = L.filtrar(alertas_completas, sucursales, tipos_activos, proveedores_sel)
filtradas_otro = L.filtrar(alertas_otro_metodo, sucursales, tipos_activos, proveedores_sel)
kpis = L.resumen_kpis(filtradas)
kpis_otro = L.resumen_kpis(filtradas_otro)


# ---------------------------------------------------------------------------
# Chat, en la barra lateral para no ocupar la pantalla del dashboard
# ---------------------------------------------------------------------------

with st.sidebar:
    st.divider()
    with st.expander("💬 Preguntar a los datos"):
        clave_groq = _secreto("GROQ_API_KEY")
        if not clave_groq:
            st.caption(
                "Falta la clave de Groq (gratuita, sin tarjeta). Ponela en "
                "`.streamlit/secrets.toml` como `GROQ_API_KEY`, o en *Manage app → "
                "Settings → Secrets* si es la app publicada."
            )
        else:
            historial = st.session_state.setdefault("chat", [])
            st.caption("Responde solo con las alertas que estás viendo.")

            if not historial:
                for sugerencia in CH.PREGUNTAS_SUGERIDAS[:3]:
                    if st.button(sugerencia, use_container_width=True,
                                 key=f"sug_{sugerencia[:18]}"):
                        st.session_state["pregunta_pendiente"] = sugerencia
                        st.rerun()

            for mensaje in historial:
                with st.chat_message(mensaje["role"]):
                    st.markdown(mensaje["content"])

            pregunta = st.chat_input("Escribí tu pregunta…")
            pregunta = st.session_state.pop("pregunta_pendiente", None) or pregunta

            if pregunta:
                historial.append({"role": "user", "content": pregunta})
                with st.chat_message("user"):
                    st.markdown(pregunta)
                with st.chat_message("assistant"):
                    with st.spinner("Pensando…"):
                        try:
                            respuesta = CH.preguntar(
                                pregunta,
                                # Se le pasa lo filtrado: si la gerente filtró una
                                # sucursal, el chat habla de lo que está viendo.
                                filtradas if not filtradas.empty else alertas_completas,
                                api_key=clave_groq,
                                metodo=metodo,
                                modelo=_secreto("GROQ_MODELO") or CH.MODELO_POR_DEFECTO,
                                historial=historial[:-1],
                            )
                        except CH.ErrorChat as error:
                            respuesta = f"⚠️ {error}"
                    st.markdown(respuesta)
                historial.append({"role": "assistant", "content": respuesta})

            if historial and st.button("Borrar la conversación",
                                       use_container_width=True):
                st.session_state["chat"] = []
                st.rerun()

    st.divider()
    if datos.avisos:
        with st.expander(f"⚠️ Calidad de los datos ({len(datos.avisos)})"):
            for aviso in datos.avisos:
                st.caption(f"• {aviso}")
    else:
        st.caption("✅ Los 4 archivos cargaron sin problemas de formato.")


# ---------------------------------------------------------------------------
# Encabezado
# ---------------------------------------------------------------------------

st.title("Revisión de órdenes de compra")
st.caption(
    f"Semana en curso · {len(sucursales)} de {len(sucursales_disponibles)} sucursales · "
    f"Proyección: **{L.ETIQUETAS_METODO[metodo]}**"
    + (f" · Colchón: {buffer_pct}%" if buffer_pct else "")
)

if orden_csv is not None:
    st.warning(
        "Estás viendo una **orden modificada desde la app**, no el archivo original.",
        icon="✏️",
    )

st.markdown(fila_kpis(kpis, kpis_otro, L.ETIQUETAS_METODO[otro_metodo]),
            unsafe_allow_html=True)

st.markdown('<div class="titulo-seccion">Estado por sucursal</div>',
            unsafe_allow_html=True)
st.markdown(panel_sucursales(filtradas, sucursales or sucursales_disponibles),
            unsafe_allow_html=True)

st.markdown(leyenda(list(L.TIPOS_ALERTA) + ([L.OK] if mostrar_ok else [])),
            unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Contenido principal
# ---------------------------------------------------------------------------

tab_alertas, tab_raras, tab_proveedores, tab_editar = st.tabs([
    "🚨 Alertas",
    "🔎 Órdenes raras",
    "📦 Pedido por proveedor",
    "✏️ Editar la orden",
])

with tab_alertas:
    if filtradas.empty:
        st.success("No hay alertas con los filtros elegidos. Toda la orden está en orden 👌")
    else:
        for tipo in sorted(set(filtradas["tipo"]), key=lambda t: L.SEVERIDAD[t]):
            grupo = filtradas[filtradas["tipo"] == tipo]
            st.markdown(
                f'<div class="titulo-seccion" style="margin-top:1rem">'
                f'{L.ICONOS_TIPO[tipo]} {html.escape(L.ETIQUETAS_TIPO[tipo])} '
                f'({len(grupo)})</div>',
                unsafe_allow_html=True,
            )
            for _, alerta in grupo.iterrows():
                st.markdown(tarjeta_html(alerta), unsafe_allow_html=True)
                with st.expander("Ver cómo se calculó"):
                    detalle_calculo(alerta, metodo)

with tab_raras:
    st.caption("Quién pide distinto al resto de la cadena, para el mismo insumo.")
    raras = AN.ordenes_raras(
        L.filtrar(alertas_completas, sucursales, None, proveedores_sel)
    )
    if raras.empty:
        st.success("Ninguna sucursal se sale del patrón del resto.")
    else:
        for _, fila in raras.iterrows():
            if fila["ya_alertado"]:
                color = "#8A8A92"
                etiqueta = f"Ya tiene alerta: {L.ETIQUETAS_TIPO[fila['tipo_alerta']]}"
            else:
                color = "#B58CF0"
                etiqueta = "Solo se ve comparando sucursales"
            st.markdown(
                f'<div class="alerta" style="--acc:{color};--bg:{tinte(color)}">'
                f'<div class="alerta-top"><span class="insignia">'
                f'{html.escape(etiqueta)}</span></div>'
                f'<div class="alerta-msg">'
                f'{html.escape(sin_icono(str(fila["mensaje"])))}</div></div>',
                unsafe_allow_html=True,
            )
        with st.expander("Cómo se mide"):
            st.markdown(
                "Se compara la **cobertura** de cada pedido —cuántas semanas de "
                "consumo cubre— contra la **mediana de las otras sucursales** para "
                "ese mismo insumo. Al ser un cociente no tiene unidades, así que se "
                "puede comparar harina contra albahaca."
            )
            st.dataframe(
                raras.assign(
                    cobertura=raras["cobertura"].round(2),
                    cobertura_pares=raras["cobertura_pares"].round(2),
                    veces_vs_pares=raras["veces_vs_pares"].round(2),
                ).rename(columns={
                    "sucursal": "Sucursal", "nombre": "Insumo",
                    "formatos_pedidos": "Pidió", "cobertura": "Cobertura",
                    "cobertura_pares": "Cobertura de los pares",
                    "veces_vs_pares": "Veces vs. pares",
                    "formatos_segun_pares": "Según los pares",
                })[["Sucursal", "Insumo", "Pidió", "Cobertura",
                    "Cobertura de los pares", "Veces vs. pares", "Según los pares"]],
                hide_index=True, width="stretch",
            )

with tab_proveedores:
    st.caption("La orden ya corregida, agrupada como se le manda a cada proveedor.")
    # Se ignora el filtro de tipo de alerta: una orden para el proveedor tiene que
    # incluir también lo que ya estaba bien pedido, no solo las correcciones.
    pedido = P.pedido_corregido(
        L.filtrar(alertas_completas, sucursales, None, proveedores_sel)
    )
    if pedido.empty:
        st.info("No hay nada para pedir con los filtros elegidos.")
    else:
        resumen = P.resumen_proveedores(pedido)
        cambios = P.comparar_con_lo_pedido(pedido)
        st.markdown(
            f'<div class="fila">'
            f'<div class="kpi"><div class="kpi-num">{len(resumen)}</div>'
            f'<div class="kpi-lab">Proveedores</div></div>'
            f'<div class="kpi"><div class="kpi-num">'
            f'{_fmt(pedido["formatos_a_pedir"].sum())}</div>'
            f'<div class="kpi-lab">Formatos a comprar</div></div>'
            f'<div class="kpi" style="--acc:{L.COLORES_TIPO[L.PIDE_MAS]}">'
            f'<div class="kpi-num">{len(cambios)}</div>'
            f'<div class="kpi-lab">Líneas corregidas</div></div></div>',
            unsafe_allow_html=True,
        )
        proveedor = st.selectbox(
            "Proveedor", options=resumen["proveedor"].tolist(),
            format_func=lambda p: (
                f"{p} — {int(resumen.loc[resumen['proveedor'] == p, 'insumos'].iloc[0])} insumos"),
        )
        matriz = P.matriz_proveedor(pedido, proveedor)
        st.dataframe(matriz, hide_index=True, width="stretch")
        st.download_button(
            f"⬇️ Descargar la orden de {proveedor}",
            data=P.a_csv(matriz),
            file_name=f"pedido_{proveedor.lower().replace(' ', '_').replace('.', '')}.csv",
            mime="text/csv",
        )
        sin_proveedor = filtradas[filtradas["tipo"] == L.DATO_RARO]
        if not sin_proveedor.empty:
            st.warning(
                f"Quedaron fuera {len(sin_proveedor)} línea(s) sin proveedor conocido "
                f"({', '.join(sorted(set(sin_proveedor['ingrediente_id'])))}): "
                "no están en el catálogo."
            )

with tab_editar:
    st.caption("Probá otra orden y mirá cómo cambian las alertas.")

    subido = st.file_uploader(
        "Subir otro CSV (columnas: sucursal, ingrediente_id, cantidad_formatos)",
        type=["csv"],
    )
    if subido is not None and st.button("Usar este archivo", type="primary"):
        try:
            nueva = L.leer_orden_subida(subido)
            # Se valida cargándola de verdad: si el archivo no sirve, el error
            # aparece acá y no deja la app en un estado roto.
            L.cargar_datos(orden_df=nueva)
            st.session_state[CLAVE_ORDEN] = nueva.to_csv(index=False)
            st.rerun()
        except Exception as error:  # archivo corrupto, columnas faltantes, etc.
            st.error(f"No se pudo usar ese archivo: {error}")

    st.divider()

    catalogo_nombres = datos.catalogo[["ingrediente_id", "nombre", "formato_compra"]]
    orden_vista = datos.orden.merge(catalogo_nombres, on="ingrediente_id", how="left")
    orden_vista["nombre"] = orden_vista["nombre"].fillna(orden_vista["ingrediente_id"])
    orden_vista["formato_compra"] = orden_vista["formato_compra"].fillna("—")
    # La columna editable va al final, que es donde uno espera escribir.
    orden_vista = orden_vista[["sucursal", "nombre", "formato_compra",
                               "ingrediente_id", "cantidad_formatos"]]

    eleccion = st.selectbox("Sucursal a editar", ["Todas"] + sucursales_disponibles)
    vista = (orden_vista if eleccion == "Todas"
             else orden_vista[orden_vista["sucursal"] == eleccion])

    with st.form("editor_orden"):
        editada = st.data_editor(
            vista,
            column_config={
                "sucursal": st.column_config.TextColumn("Sucursal"),
                "ingrediente_id": st.column_config.TextColumn("ID del insumo"),
                "nombre": st.column_config.TextColumn("Insumo", disabled=True),
                "formato_compra": st.column_config.TextColumn("Formato", disabled=True),
                "cantidad_formatos": st.column_config.NumberColumn(
                    "Cantidad (formatos)", min_value=0, step=1),
            },
            num_rows="dynamic", hide_index=True, width="stretch", height=320,
            key="tabla_orden",
        )
        aplicar = st.form_submit_button("Aplicar cambios y recalcular", type="primary")

    if aplicar:
        if eleccion == "Todas":
            resultado = editada
        else:
            # Solo se editó una sucursal: hay que devolver también las demás, si no
            # la orden quedaría con una sola sucursal.
            resto = orden_vista[orden_vista["sucursal"] != eleccion]
            resultado = pd.concat([resto, editada], ignore_index=True)
        columnas = ["sucursal", "ingrediente_id", "cantidad_formatos"]
        st.session_state[CLAVE_ORDEN] = resultado[columnas].to_csv(index=False)
        st.rerun()

    columna_reset, columna_descarga = st.columns(2)
    if orden_csv is not None:
        if columna_reset.button("↩️ Volver a la orden original"):
            del st.session_state[CLAVE_ORDEN]
            st.rerun()
    else:
        columna_reset.caption("Estás viendo la orden original del CSV.")
    columna_descarga.download_button(
        "⬇️ Descargar esta orden",
        data=datos.orden.to_csv(index=False).encode("utf-8-sig"),
        file_name="orden_compra_semana.csv",
        mime="text/csv",
    )
