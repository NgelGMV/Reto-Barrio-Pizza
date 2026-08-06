# Dashboard de órdenes de compra · Barrio Pizza

Revisión automática de las órdenes de compra semanales de 4 sucursales. En vez de
aprobar producto por producto "al ojo", la gerente de compras abre el dashboard y ve
de un vistazo qué sucursal **se va a quedar sin producto**, cuál **está pidiendo de
más**, qué **se olvidaron de pedir** y qué líneas **no se pueden verificar**.

> ### 👉 [barrio-pizza-ordenes.streamlit.app](https://barrio-pizza-ordenes.streamlit.app)
>
> Desplegada en Streamlit Community Cloud, sin necesidad de instalar nada.
> Si estuvo varios días sin visitas la app se duerme: el primer acceso puede tardar
> unos 30 segundos en despertar.

---

## Cómo correrlo en local

```bash
pip install -r requirements.txt
streamlit run app.py
```

Se abre en `http://localhost:8501`. No necesita claves, cuentas ni servicios pagos:
todo corre en local sobre los CSV de `datos/`.

Para correr las verificaciones:

```bash
python -m pytest test_logica.py -v
```

## Estructura

| Archivo | Qué hace |
|---|---|
| `logica.py` | Toda la lógica de negocio: carga y limpieza, conversión de unidades, proyección, necesidad, clasificación de alertas y redacción de los mensajes. No importa Streamlit. |
| `app.py` | Solo presentación: KPIs, filtros, tarjetas de alerta, toggle de proyección. |
| `proveedores.py` | Reagrupa el pedido corregido por proveedor (cada proveedor recibe su orden aparte). |
| `test_logica.py` | 30 verificaciones, incluidos los 6 casos de aceptación del enunciado. |
| `datos/` | Los 4 CSV del reto. |

La separación importa: la lógica se puede testear sin levantar la interfaz, y la
interfaz no toma ni una sola decisión de negocio.

---

## Cómo se calcula una alerta

Para cada par **(sucursal, ingrediente)** —recorriendo la unión de lo que la sucursal
consume y lo que pidió— el sistema hace cuatro cosas:

**1. Convierte unidades.** El consumo y el stock vienen en unidad base (kg, L, unidades)
pero las órdenes vienen en **formatos** (3 = 3 sacos). El factor sale siempre del
catálogo y **nunca está hardcodeado**, porque no siempre es entero: la salsa pelatti
viene en latas de 2.55 kg y la albahaca en paquetes de 0.25 kg.

**2. Proyecta el consumo de la semana 7.** Hay dos métodos y el dashboard los cambia en
vivo (ver abajo).

**3. Calcula la necesidad real:**

```
necesidad = max(consumo_proyectado − stock_actual, 0)
formatos_recomendados = ceil(necesidad ÷ unidad_base_por_formato)
```

El `ceil` es la clave del redondeo: como no existe medio saco, hay que comprar el
formato completo. Eso significa que **un sobrante menor a un formato es redondeo normal,
no un sobre-pedido**, y por eso la comparación se hace siempre en formatos y nunca en
kilos. Ejemplo real: Brisas del Golf necesita 244 kg de harina y pide 10 sacos (250 kg).
Sobran 6 kg, pero pedir 9 sacos la dejaría corta. Es OK, no un exceso.

**4. Clasifica**, en este orden de prioridad:

| Alerta | Cuándo |
|---|---|
| ⚪ **No verificable** | Está en la orden pero no en el catálogo → sin factor de conversión ni histórico, no hay forma de validarlo. |
| 🟠 **Olvido** | Lo consume, no lo pidió, y el stock no cubre la proyección. |
| 🔵 **Sin historial** | Está en catálogo y en la orden pero nunca se consumió → se puede convertir, no proyectar. |
| 🔴 **Riesgo de quiebre** | `formatos_pedidos < formatos_recomendados`. |
| 🟡 **Exceso** | `formatos_pedidos > formatos_recomendados` (peor si es perecedero). |
| 🟢 **OK** | Coinciden. |

Cada alerta trae una frase accionable (no una tabla) y un desplegable **"Ver cómo se
calculó"** con el paso a paso de los números y un gráfico de las 6 semanas de consumo,
marcando en rojo las semanas descartadas y en verde la proyección. La idea es que
ninguna alerta sea una caja negra.

---

## Los dos métodos de proyección

El toggle de la barra lateral recalcula todo en vivo.

**Promedio simple** — el promedio de las 6 semanas. Es transparente, pero una sola
semana rara lo distorsiona y no ve las tendencias.

**Proyección inteligente** — dos pasos:

1. **Descarta semanas atípicas** con la distancia a la mediana: se descarta un punto si
   `|x − mediana| > 3 · MAD`. Se usa mediana y MAD en vez de media y desvío estándar
   porque el propio valor raro contamina la media: una semana de 150 kg sobre un consumo
   habitual de 30 kg inflaría tanto el desvío que dejaría de ser detectable. Si MAD = 0
   (serie constante) no se descarta nada.
2. **Ajusta una tendencia lineal** sobre la serie limpia (`numpy.polyfit`) y proyecta la
   semana 7. Si quedan menos de 3 puntos, cae al promedio de la serie limpia.

### Cuándo se considera que hay tendencia (decisión propia)

El enunciado dice "si la pendiente es casi cero, caer al promedio", sin definir "casi
cero". Con solo 5 o 6 puntos ruidosos, **cualquier** serie plana devuelve una pendiente
distinta de cero por puro azar, y extrapolarla una semana hacia adelante empeora la
proyección en vez de mejorarla. En una versión intermedia, usar un umbral relativo
simple hacía que el método "inteligente" generara **16 alertas con 11 quiebres**, casi
todos falsos.

La solución fue exigir dos condiciones para usar la tendencia:

- **Que sea relevante para el negocio:** el cambio semanal supera el 0.5% del nivel medio
  de la serie.
- **Que sea estadísticamente real:** `|pendiente / error estándar de la pendiente| ≥ 2`,
  o sea que el ruido de la serie no alcance para explicarla.

Con eso el método inteligente aplica tendencia solo donde de verdad la hay. Ambos
umbrales son constantes al inicio de `logica.py`.

### El contraste, con los datos reales

| Caso | Promedio simple | Proyección inteligente |
|---|---|---|
| **Costa del Este / harina** (consumo creciente 240→316) | proyecta 277 kg → faltan **4 sacos** | detecta +15 kg/semana, proyecta 330 kg → faltan **7 sacos** |
| **Marbella / pepperoni** (semana atípica de 150 kg) | el 150 infla el promedio a 49 kg → marca un quiebre de **4 cajas** | descarta S3, proyecta 29 kg → **la alerta desaparece**, pidió justo |

El segundo caso es el más interesante: el promedio simple genera una alerta falsa que
haría comprar 4 cajas de pepperoni de más.

---

## Supuestos

**`aji_chombo` (Costa del Este).** No está en el catálogo, así que no tiene factor de
conversión, proveedor ni histórico. No se inventa nada: se muestra como **no verificable**
con la cantidad pedida, se pide revisión manual y **no rompe el resto del análisis** (las
otras 88 líneas se procesan normalmente). Puede ser un insumo nuevo sin cargar o un error
de tipeo. Queda fuera del pedido corregido por proveedor, con un aviso explícito.

**Colchón de seguridad = 0% por defecto.** Es decir, se pide exactamente lo proyectado,
para calzar con el enunciado. Hay un slider en "Ajustes avanzados" para activarlo: con un
10–20% el sistema empieza a marcar como quiebre casi todo lo que hoy está justo, que es
justamente la conversación que conviene tener con la gerente (¿preferimos capital
inmovilizado o riesgo de quedarnos cortos?). No es un bug: es la política de inventario.

**Stock faltante o ilegible = 0.** Supuesto conservador: asumir que no hay stock puede
llevar a comprar de más, pero nunca a un quiebre.

**Cantidad ilegible o negativa en la orden ≠ 0.** Se marca como no verificable en vez de
asumir cero, porque asumir cero inventaría un "olvido" que no existe.

**Filas duplicadas** (mismo par sucursal-ingrediente repetido) se suman, y se avisa en la
barra lateral. **Semanas sin número reconocible** se ordenan al final en vez de descartarse.

**No pedir algo no siempre es un olvido.** Si el stock ya cubre la proyección, la
necesidad es 0 y no hay nada que pedir: se trata como OK.

Todos los problemas de formato que encuentra la carga se reportan en la barra lateral,
bajo "Avisos de calidad de datos". Con los datos actuales del reto no aparece ninguno.

---

## Qué verifican los tests

`test_logica.py` cubre los 6 casos de aceptación del enunciado (conversión con factores
decimales, el olvido de mozzarella, el `aji_chombo`, el quiebre con tendencia de Costa del
Este, la semana atípica de Marbella y la regla de redondeo) y agrega pruebas defensivas
con un dataset sintético: CSV con BOM, espacios en los nombres, consumos ilegibles, stock
negativo, ingredientes sin histórico, ingredientes fuera de catálogo y órdenes vacías.
También verifica que las alertas salgan ordenadas por severidad y que los filtros del
dashboard no fallen con cero resultados.

```
30 passed
```

---

## Cómo se llevaría esto a producción con Odoo

Hoy la herramienta lee 4 CSV. En producción esos 4 CSV se reemplazan por 4 consultas a
Odoo vía su **API XML-RPC / JSON-RPC** (`/xmlrpc/2/object`, método `execute_kw`), sin
tocar la lógica de negocio:

| Dato | De dónde sale en Odoo |
|---|---|
| Catálogo y formatos | `product.product` / `product.template`, con las unidades de medida en `uom.uom` y sus categorías (`uom.category`). El campo `factor` / `factor_inv` de `uom.uom` cumple exactamente el papel de `unidad_base_por_formato`, y Odoo ya resuelve la conversión entre la UoM de compra y la de stock. |
| Inventario por sucursal | `stock.quant`, filtrando por la ubicación (`location_id`) de cada sucursal. |
| Órdenes de la semana | `purchase.order` y sus líneas `purchase.order.line` (`product_qty` en `product_uom`). |
| Consumo histórico | Del POS (`pos.order.line`) o de los movimientos de stock de salida (`stock.move`), agregados por semana y sucursal. Si hay recetas cargadas (`mrp.bom`), el consumo teórico de insumos se puede derivar de las pizzas vendidas. |

El flujo sería: un módulo lee esos modelos, arma los mismos DataFrames que hoy salen de
`cargar_datos()`, corre `construir_alertas()` sin cambios y devuelve las alertas. La
salida puede ir a un dashboard embebido, o mejor: **escribir la sugerencia directamente
en la orden de compra** —una nota en la línea, o directamente ajustar `product_qty` con
un estado "sugerido por el sistema" que la gerente aprueba o rechaza—. Así la corrección
vive donde se toma la decisión y no en una pantalla aparte.

El punto importante es que `logica.py` no sabe de dónde vienen los datos: hoy son CSV,
mañana es Odoo, y los tests siguen valiendo igual.

---

## Ideas para seguir

- Cargar la orden desde la propia interfaz (`st.file_uploader`) para revisar cualquier
  semana sin tocar archivos.
- Detección de órdenes raras comparando una sucursal contra las demás, normalizando por
  volumen de ventas.
- Un "chat con los datos" sobre el DataFrame de alertas, con un modelo de free tier.
- Historial: guardar las alertas de cada semana para ver qué sucursal mejora y cuál
  repite los mismos errores.
