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
| `anomalias.py` | Compara cada sucursal contra las demás para detectar órdenes fuera de patrón. |
| `chat.py` | Chat con los datos: arma el contexto y consulta el modelo (Groq). |
| `test_logica.py` | 30 verificaciones, incluidos los 6 casos de aceptación del enunciado. |
| `test_extras.py` | 19 verificaciones de los módulos opcionales, la edición de órdenes y el chat. |
| `datos/` | Los 4 CSV del reto. |
| `assets/` | El logo de la marca. Es opcional: si el archivo no está, la app arranca igual. |
| `.streamlit/config.toml` | Tema negro sobre el que el color queda reservado a las alertas. |

La separación importa: la lógica se puede testear sin levantar la interfaz, y la
interfaz no toma ni una sola decisión de negocio.

> El logo de Barrio Pizza se usa únicamente para ambientar esta entrega; es
> propiedad de la marca. Este dashboard es un prototipo de un ejercicio técnico,
> no una herramienta oficial de la empresa.

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

`test_extras.py` cubre los módulos opcionales: que el pedido corregido use lo
recomendado y no lo pedido, que la matriz por proveedor sume bien, que la detección de
órdenes raras encuentre a Via Argentina y no marque a quien pide como sus pares, y que
reemplazar la orden desde la interfaz recalcule las alertas (al pedir la mozzarella que
faltaba, el olvido desaparece).

```
49 passed
```

---

## Cómo está pensada la pantalla

La gerente entra para contestar una sola pregunta: **a quién tengo que llamar hoy**. La
pantalla está ordenada para que la conteste sin leer una tabla:

1. **Los números de arriba** dicen cuánto hay para revisar, y cada uno muestra cuánto
   cambiaría con el otro método de proyección. Es lo que hace visible, en vivo, que el
   promedio simple inventa un quiebre que no existe.
2. **El estado por sucursal** es la fila que más rápido resuelve la pregunta: cuatro
   tarjetas, cada una con el color de su alerta más grave. Marbella en verde significa
   "no la llames"; Costa del Este en rojo, "empezá por acá".
3. **Las alertas** vienen como frases accionables, agrupadas por gravedad, con los tres
   números que hacen falta para decidir (necesita / pidió / diferencia) y nunca en
   kilos: siempre en los formatos en los que realmente se compra.
4. **El detalle** está escondido en "Ver cómo se calculó", para el que quiera auditarlo.

El tema es negro con el color reservado a las alertas: el primario de la interfaz es
blanco, así que los filtros y las pestañas no compiten con el rojo. Si los botones
también fueran de color, el rojo dejaría de significar "riesgo de quiebre" y pasaría a
ser decoración.

El **chat vive en la barra lateral** y no en una pestaña propia, por dos razones: no
tiene que robarle espacio al tablero, y en Streamlit los diálogos y popovers se cierran
en cada rerun, con lo cual una conversación adentro sería inusable.

---

## Las cuatro vistas extra

**🔎 Órdenes raras.** Las alertas comparan cada sucursal contra sí misma. Esta vista
compara **una sucursal contra las demás**: mide la *cobertura* de cada pedido
(`pedido ÷ consumo proyectado`, o sea cuántas semanas cubre) y busca a quien se aleja de
la mediana de sus pares. Al ser un cociente no tiene unidades, así que se puede comparar
harina contra albahaca sin que los kilos distorsionen nada. Se compara contra la
**mediana de las otras** sucursales, no contra el promedio del grupo entero, para que una
sucursal muy desviada no se compare contra un número que ella misma infló.

Sobre los datos del reto encuentra 3 casos, y **los 3 ya tenían alerta individual**: acá
la vista no descubre nada nuevo, lo explica desde otro ángulo ("Via Argentina pide 4× lo
que piden las otras"). Cada hallazgo indica si ya tenía alerta propia o si solo se ve
comparando, para no vender como hallazgo algo que ya estaba a la vista.

> Probé también comparar el **perfil de consumo** entre sucursales, normalizando por
> tamaño de sucursal (la idea de "por cliente" del enunciado). Lo descarté: en este
> dataset los perfiles son casi idénticos y lo único que sobresalía era Marbella con
> pepperoni, que es el artefacto de la semana atípica de 150 kg. Habría sido una vista
> que solo genera un falso positivo.

**✏️ Editar la orden.** Se puede subir otro `orden_compra_semana.csv` o cambiar las
cantidades a mano en una tabla, y las alertas se recalculan al instante. Si el archivo
subido está roto, el error se muestra y la app vuelve sola a la orden original en vez de
quedar trabada. Mientras haya una orden modificada, un aviso arriba lo deja claro para
que nadie confunda una simulación con la orden real.

**📦 Pedido corregido por proveedor.** Ver más arriba.

**💬 Preguntar** (en la barra lateral). La gerente escribe en español ("¿qué sucursal
está pidiendo demasiado queso?") y recibe una respuesta en texto, sin leer tablas. Por
ejemplo, a *"resumime en 3 líneas qué tengo que corregir hoy"* responde:

> A Costa del Este le faltan 7 sacos de harina.
> Brisas del Golf se va a quedar corto de Mozzarella, le faltan 18 cajas.
> Via Argentina tiene un exceso de albahaca fresca, pidió 18 paquetes de más.

El modelo nunca ve los códigos internos ni los nombres de las columnas: la tabla se le
pasa con encabezados en castellano y las alertas ya traducidas. Si no los ve, no los
puede repetir, y la gerente no termina leyendo `PIDE_MENOS` ni `delta_formatos`.

La decisión de diseño importante es **cómo se conecta el modelo a los datos**. La
tentación es dejar que el modelo escriba código pandas y ejecutarlo, pero eso es darle
permiso de correr cualquier cosa en el servidor a cambio de muy poco. Acá se hace al
revés: se le pasa **la tabla de alertas ya calculada** dentro del prompt y se le pide que
responda solo con eso. Funciona porque el dataset es chico —89 líneas, unos 2.000
tokens— y entra entero en el contexto. Los números que ve el modelo son exactamente los
que calculó `logica.py`, así que el chat no puede contradecir al dashboard. Si mañana
fueran 50.000 líneas habría que resumir o filtrar antes de preguntar.

El chat respeta los filtros: si la gerente está mirando una sola sucursal, responde sobre
esa. Usa **Groq** (free tier, sin tarjeta) por HTTP directo, sin SDK, para no sumar una
dependencia que pueda romper el despliegue.

Para activarlo hace falta una clave gratuita de [console.groq.com](https://console.groq.com):

```toml
# .streamlit/secrets.toml   (ya está en .gitignore, la clave nunca se sube)
GROQ_API_KEY = "gsk_tu_clave"
GROQ_MODELO  = "llama-3.3-70b-versatile"   # opcional
```

En Streamlit Cloud la misma línea va en *Manage app → Settings → Secrets*. **Sin clave la
app funciona igual**: la pestaña explica cómo activarlo en vez de romperse, porque una
feature opcional no puede tumbar el dashboard.

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

- Historial: guardar las alertas de cada semana para ver qué sucursal mejora y cuál
  repite los mismos errores.
- Que el colchón de seguridad sea por insumo y no global: no es lo mismo quedarse sin
  mozzarella que sin orégano.
- Cruzar con el precio de cada formato para poner los excesos en dólares, que es el
  número que de verdad mueve una decisión de compra.
