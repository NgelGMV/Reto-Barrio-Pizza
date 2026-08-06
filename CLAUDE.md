# CLAUDE.md — Dashboard de Órdenes de Compra · Barrio Pizza

> Este archivo es el brief del proyecto. Léelo completo antes de escribir código.
> Contiene el contexto, los contratos de datos, la lógica de negocio exacta,
> los casos de prueba que la solución DEBE cumplir, y los requisitos del dashboard.

---

## 1. Propósito

Barrio Pizza es una cadena de pizzerías con 10 sucursales en Panamá. Cada semana,
cada sucursal arma su **orden de compra de insumos**. Hoy la gerente de compras las
aprueba "al ojo", producto por producto. A veces piden **de más** (plata inmovilizada
y comida que se vence) y a veces **de menos** (se quedan sin producto en pleno servicio).

Hay que construir un **dashboard** que revise las órdenes de la semana (de 4 sucursales
de muestra) y **muestre alertas automáticas**: ¿piden lo que necesitan?, ¿piden de más?,
¿piden de menos?, ¿se olvidaron de algo? La gerente debe entender qué está mal **de un
vistazo**, sin leer código ni tablas crudas.

## 2. Cómo se evalúa (prioridades)

En orden de importancia:
1. Que **funcione** y detecte bien los problemas.
2. **Manejo correcto de unidades** (conversión formatos ↔ unidad base).
3. **Manejo de datos incompletos/raros** sin que la app se caiga.
4. **Razonamiento claro** reflejado en el código y el README.
5. **Claridad visual** del dashboard.

No se busca perfección ni un sistema terminado. Se busca algo que funcione, que piense
como quien resuelve un problema real de negocio, y que esté bien explicado.

## 3. Stack y restricciones

- **Lenguaje:** Python 3.11+
- **UI / dashboard:** Streamlit
- **Datos:** pandas, numpy
- **Gráficos (opcional):** plotly o los charts nativos de Streamlit
- **Despliegue:** Streamlit Community Cloud (gratis, conecta directo al repo de GitHub)
- **Restricción dura:** todo debe correr con herramientas **100% gratuitas**. Sin
  suscripciones ni claves de pago obligatorias.
- Correr en local con: `streamlit run app.py`

## 4. Estructura del proyecto

```
reto-barrio-pizza/
├── app.py              # UI de Streamlit (solo presentación, sin lógica de negocio)
├── logica.py           # carga, limpieza, proyección, necesidad, conversión, alertas
├── proveedores.py      # (opcional) pedido corregido agrupado por proveedor
├── test_logica.py      # verificaciones de aceptación (sección 7)
├── datos/              # los 4 CSV del reto (copiar desde el repo original)
│   ├── ingredientes.csv
│   ├── consumo_historico.csv
│   ├── inventario_actual.csv
│   └── orden_compra_semana.csv
├── requirements.txt    # streamlit, pandas, numpy, plotly
├── README.md           # cómo correrlo, supuestos, y nota de Odoo
└── CLAUDE.md           # este archivo
```

Separá **lógica** (`logica.py`) de **UI** (`app.py`). La lógica debe ser testeable sin
levantar Streamlit.

## 5. Contratos de datos (columnas exactas)

Todos los CSV están en `datos/`. **Ojo:** vienen con BOM → leer con
`encoding="utf-8-sig"`. Limpiar espacios en columnas de texto (`.str.strip()`).

### `ingredientes.csv` — catálogo (22 insumos)
| Columna | Tipo | Notas |
|---|---|---|
| `ingrediente_id` | str | clave primaria (ej. `harina`, `mozzarella`) |
| `nombre` | str | nombre legible |
| `proveedor` | str | 8 proveedores distintos |
| `unidad_base` | str | `kg`, `L` o `und` |
| `formato_compra` | str | ej. "Saco 25 kg", "Caja x 12 und" |
| `unidad_base_por_formato` | float | **factor de conversión** |
| `es_perecedero` | str | `Si` / `No` |

> **CRÍTICO:** el factor `unidad_base_por_formato` **no siempre es entero**. Ejemplos
> reales: `salsa_pelatti` = 2.55, `albahaca` = 0.25, `arugula` = 0.25. **No hardcodees
> factores**; leelos siempre del catálogo.

### `consumo_historico.csv` — consumo de las últimas 6 semanas
| Columna | Tipo | Notas |
|---|---|---|
| `sucursal` | str | 4 sucursales |
| `ingrediente_id` | str | FK a catálogo |
| `semana` | str | `S1`…`S6` |
| `consumo_unidad_base` | float | en unidad base; puede tener decimales |

Las 4 sucursales son: **Brisas del Golf, Costa del Este, Marbella, Via Argentina**.

### `inventario_actual.csv` — stock actual
| Columna | Tipo |
|---|---|
| `sucursal` | str |
| `ingrediente_id` | str |
| `stock_actual_unidad_base` | float (en unidad base) |

### `orden_compra_semana.csv` — lo que cada sucursal está pidiendo
| Columna | Tipo | Notas |
|---|---|---|
| `sucursal` | str |  |
| `ingrediente_id` | str |  |
| `cantidad_formatos` | float | **en formatos** (ej. `3` = 3 sacos), NO en unidad base |

## 6. Lógica de negocio (el corazón del reto)

Procesar cada par **(sucursal, ingrediente_id)** recorriendo la **unión** de lo que la
sucursal consume (tiene histórico) y lo que pidió. Para cada par:

### 6.1 Conversión de unidades
```
pedido_base = cantidad_formatos * unidad_base_por_formato   # formatos → unidad base
```

### 6.2 Proyección del consumo de la próxima semana
- **Método base (requerido):** promedio simple de las 6 semanas.
- **Método inteligente (recomendado, suma puntos):**
  1. Detectar y quitar **semanas atípicas** (outliers). Usar distancia a la mediana:
     descartar puntos con `|x − mediana| > 3 · MAD` (MAD = desviación absoluta mediana).
     Si MAD = 0, no descartar nada.
  2. Sobre la serie limpia, ajustar una **tendencia lineal** (`numpy.polyfit` grado 1)
     y predecir la semana 7. Si quedan <3 puntos o la pendiente es casi cero, caer al
     promedio de la serie limpia.
- Exponer ambos métodos con un **toggle en la UI** para poder comparar (ver sección 8).

### 6.3 Necesidad real
```
necesidad_base = max(consumo_proyectado - stock_actual, 0)
```
Parametrizá un colchón de seguridad opcional (`BUFFER = 0.0` por defecto para calzar con
el enunciado; documentá en el README si lo activás, ej. 10%).

### 6.4 Formatos recomendados (aplica sola la regla del redondeo)
```
formatos_recomendados = ceil(necesidad_base / unidad_base_por_formato)   # math.ceil
```
> Como los insumos solo se compran en **formatos completos**, el `ceil` ya absorbe
> cualquier excedente **menor a un formato**. Por eso NUNCA hay que marcar un sobrante
> sub-formato como sobre-pedido: eso es redondeo normal.

### 6.5 Clasificación de alertas
Evaluar en este orden:

1. **`DATO_RARO`** — el ingrediente está en la orden pero **NO existe en el catálogo**
   → no se puede convertir ni proyectar. Mostrarlo, marcarlo, **y no romper la app**.
2. **`OLVIDO`** — el ingrediente se consume (tiene histórico) pero **NO está en la
   orden**, y la necesidad proyectada es > 0 (es decir `formatos_recomendados ≥ 1`).
   Si el stock ya cubre la proyección (necesidad = 0), NO es olvido → tratar como OK.
3. **`SIN_HISTORIAL`** (defensivo) — está en catálogo y en la orden pero no tiene
   histórico de consumo → se puede convertir pero no proyectar con confianza. Informar.
4. En catálogo **y** en la orden **y** con histórico → calcular:
   ```
   delta = formatos_pedidos - formatos_recomendados
   ```
   - `delta > 0`  → **`PIDE_MAS`** por `delta` formatos (peor si `es_perecedero = Si`).
   - `delta < 0`  → **`PIDE_MENOS`** por `|delta|` formatos → riesgo de quiebre.
   - `delta == 0` → **`OK`**.

### 6.6 Plantillas de mensajes (estilo pedido en el enunciado)
Usar frases accionables, no tablas. Sugerencias (refinables):
- **PIDE_MENOS:** `🔴 {sucursal}: pidió {pedidos} {formato} de {nombre}; se proyecta necesidad de {recomendados} {formato}. Faltan {delta} → riesgo de quiebre.`
- **PIDE_MAS:** `🟡 {sucursal}: pidió {pedidos} {formato} de {nombre}; con {recomendados} {formato} alcanza. Sobran {delta}{" · PERECEDERO, riesgo de merma" si es_perecedero}.`
- **OLVIDO:** `🟠 {sucursal}: no pidió {nombre}, pero lo consume y el stock ({stock} {unidad}) no cubre la proyección ({proyeccion} {unidad}). Debería pedir ~{recomendados} {formato}.`
- **DATO_RARO:** `⚪ {sucursal}: pidió {pedidos} formatos de "{ingrediente_id}", que no está en el catálogo. No verificable → revisar manualmente.`

## 7. Casos de prueba de aceptación (este dataset)

Los datos tienen trampas intencionales. La solución **debe** producir estos resultados.
Implementalos en `test_logica.py` y corré los tests antes de dar por terminado.

1. **Conversión con decimales:** convertir `salsa_pelatti` (factor 2.55) y `albahaca`
   (factor 0.25) sin redondear el factor. Ej.: 4 latas de salsa = 10.2 kg.
2. **Olvido:** **Brisas del Golf** NO pidió `mozzarella`, pero la consume fuerte
   (proyección ≈ 200 kg, stock ≈ 22 kg). → debe generar **OLVIDO** (recomendado ≈ 18
   cajas de 10 kg).
3. **Dato raro:** **Costa del Este** pidió `aji_chombo`, que **no está en el catálogo**.
   → debe marcarse **DATO_RARO** y la app **no debe caerse**.
4. **Pide de menos con tendencia:** **Costa del Este / harina** tiene consumo creciente
   (S1→S6: 240, 255, 268, 284, 300, 316). Con promedio simple la necesidad ya supera lo
   pedido → **PIDE_MENOS**. Con el método inteligente (que capta la tendencia) la brecha
   se ve aún mayor.
5. **Semana atípica:** **Marbella / pepperoni** = [28, 30, **150**, 27, 29, 31]. Con
   promedio simple la proyección se infla (~49) y marca **PIDE_MENOS**; con el método
   inteligente (quita la semana de 150, mediana ≈ 29) la necesidad baja y la alerta puede
   desaparecer. **Este contraste debe ser visible** al cambiar el toggle de proyección.
6. **Regla de redondeo:** un excedente **menor a un formato completo** NO debe marcarse
   como sobre-pedido (`delta` se calcula sobre formatos, con `ceil`).

## 8. Requisitos del dashboard (`app.py`)

Pensalo como algo que la **gerente de compras** usaría, todo en **español**.

**Encabezado / KPIs (fila superior):**
- Total de alertas · Nº de quiebres (rojo) · Nº de excesos (amarillo) · Nº de olvidos
  (naranja) · Nº de datos no verificables (gris). Opcional: "sucursal con más alertas".

**Filtros (sidebar):**
- Sucursal (multiselect, por defecto todas), Tipo de alerta (multiselect), Proveedor
  (multiselect).

**Zona principal:**
- Las alertas como **tarjetas o tabla con color** según tipo, **ordenadas por severidad**
  (quiebres primero, luego olvidos, excesos, datos raros). Cada fila muestra: sucursal,
  ingrediente, proveedor, tipo, la **frase accionable** y los números clave
  (necesita / pidió / diferencia en formatos).
- Una pequeña **leyenda** de colores ("cómo leer esto").

**Control de proyección:**
- Toggle **"Promedio simple" vs "Proyección inteligente"** que **recalcula** las alertas
  en vivo. Es el momento estrella para el video (casos 4 y 5).

## 9. Features opcionales (marcadas como opcionales — sumar según tiempo)

En orden de mejor relación esfuerzo/impacto:
- **Pedido corregido por proveedor** (`proveedores.py`): agrupar los formatos
  recomendados por proveedor, para reenviarle a cada uno su parte. El catálogo ya trae
  `proveedor`. Fácil y muy vendible.
- **Cargar/editar órdenes desde la UI:** un `st.file_uploader` para subir otro
  `orden_compra_semana.csv` y ver las alertas actualizarse. Acerca a la visión final.
- **Chat con los datos:** un cuadro donde la gerente escribe en español (ej. "¿qué
  sucursal está pidiendo demasiado queso?") y responde en texto. Conectar un LLM al
  DataFrame de alertas. Opciones **gratuitas**: API de Google Gemini (free tier) o Groq.
  Solo si sobra tiempo; NO es obligatorio y NO debe requerir clave de pago.
- **Detección de órdenes raras** comparando una sucursal contra las demás.

## 10. Nota de Odoo (incluir en el README)

No hace falta usar Odoo, pero el README debe explicar cómo se llevaría a producción con
un sistema como Odoo: leer vía su **API (XML-RPC / JSON-RPC)** el catálogo desde
`product.product` + unidades de medida (`uom.uom` / categorías de UoM) para la conversión
de formatos, el inventario desde `stock.quant`, y las órdenes desde `purchase.order` /
sus líneas; correr esta misma lógica sobre esos datos y devolver las alertas (o escribir
sugerencias en la orden). El consumo histórico saldría de POS / reportes de consumo.

## 11. Convenciones de código

- Textos de UI y mensajes de alerta **en español**.
- Constantes de configuración (BUFFER, umbral de outliers) al inicio del módulo, no
  mágicas y dispersas.
- Lectura robusta: `encoding="utf-8-sig"`, `.str.strip()` en texto, `pd.to_numeric(...,
  errors="coerce")` en numéricos y manejar los NaN explícitamente.
- **Nunca** asumir que todos los ingredientes existen en todos los archivos: hacer merges
  defensivos (`how="outer"` donde aplique) y no reventar ante claves faltantes o sobrantes.
- Código legible antes que "clever". Comentarios donde el razonamiento no sea obvio
  (por qué se proyecta así, por qué se usa `ceil`, cómo se tratan los datos raros).

## 12. Definition of done

- [ ] Los 4 CSV cargan y se limpian sin errores (BOM, espacios, tipos).
- [ ] Conversión de formatos correcta, incluidos factores decimales.
- [ ] Las 5 alertas de la sección 7 se producen correctamente y `aji_chombo` no rompe nada.
- [ ] `test_logica.py` pasa.
- [ ] Dashboard con KPIs, filtros, alertas con color ordenadas por severidad, y toggle de
      proyección funcionando.
- [ ] `requirements.txt` completo y `streamlit run app.py` funciona en limpio.
- [ ] `README.md` con: cómo correrlo, supuestos (incl. tratamiento de `aji_chombo` y del
      colchón de seguridad), y la nota de Odoo.
- [ ] Desplegado en Streamlit Community Cloud y **probado en ventana de incógnito**.
