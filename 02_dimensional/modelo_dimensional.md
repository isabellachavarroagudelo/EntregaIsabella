# Parte 2 · Modelo dimensional (Kimball)

Esquema `dw` en SQL Server. El script es `ddl_dw.sql` y el diagrama está en `diagrama_estrella.png`.

## ¿Una tabla de hechos o dos?

Dos. El negocio mira cosas distintas con granos distintos:

- Las **ventas** se analizan por producto, o sea por línea de pedido.
- Las **entregas** se analizan por pedido (¿llegó a tiempo?).

Si metiera todo en una sola tabla de líneas, el dato de la entrega (a tiempo, calificación, reclamo) se repetiría en cada línea y al contar pedidos tardíos un pedido de 5 líneas contaría 5 veces.

## Hechos

| Tabla | Una fila representa… |
|---|---|
| `fact_ventas_linea` | una línea de un pedido |
| `fact_entregas_pedido` | un pedido completo |

Las dos tablas traen los pedidos de todos los estados. Las ventas solo cuentan los *Entregado*; eso se filtra con `dim_estado_pedido.cuenta_como_venta`.

## Medidas

| Tabla | Medida | Tipo | Por qué |
|---|---|---|---|
| fact_ventas_linea | cantidad, venta_bruta, descuento_valor, venta_neta, costo_total | Aditiva | Se pueden sumar por cualquier dimensión |
| fact_ventas_linea | precio_unitario, descuento_pct | No aditiva | Sumarlos no tiene sentido (se promedian o se recalculan) |
| fact_entregas_pedido | num_lineas, unidades_totales, peso_total_kg, venta_neta_pedido | Aditiva | Se suman por transportadora, mes, etc. |
| fact_entregas_pedido | dias_retraso, horas_hasta_entrega, distancia_km | Aditiva pero se usa promediada | Sumar horas de varios pedidos no dice nada; el promedio sí |
| fact_entregas_pedido | entregado_a_tiempo, cruza_via, reclamo | Aditiva (0/1) | Su suma es el conteo; su promedio es el porcentaje |
| fact_entregas_pedido | calificacion_servicio, lluvia_destino_mm, horas_restriccion_via | No aditiva | Son niveles: se promedian, no se suman |

No hay medidas semiaditivas: no guardamos saldos ni inventarios.

## Dimensiones (todas con llave sustituta `*_key`)

`dim_fecha` (llave yyyymmdd; en entregas juega 3 roles: pedido, prometida y entrega), `dim_cliente`, `dim_producto`, `dim_bodega`, `dim_transportadora`, `dim_ciudad`, `dim_canal`, `dim_metodo_pago`, `dim_estado_pedido`, `dim_estado_via`, `dim_alerta_clima`.

## SCD tipo 2

Se aplica en dos dimensiones, con las columnas de control `fecha_inicio`, `fecha_fin` y `es_actual`:

- **`dim_cliente`** (ciudad, dirección y departamento). 13 clientes se mudaron durante el periodo (1.133 clientes, 1.146 versiones). Esto permite responder la pregunta 3 con el departamento donde vivía el cliente **al momento del pedido**, y la pregunta 2 con la ciudad **actual**.
- **`dim_producto`** (precio de lista). Los productos cambian de precio de lista durante el periodo (48 de 52 SKU tienen 2 o 3 precios). Hay 126 versiones para 55 productos.

Al cargar los hechos, la versión correcta se elige por la fecha del pedido: `fecha_pedido BETWEEN fecha_inicio AND fecha_fin`. La versión actual termina en `9999-12-31`.

## Decisiones y supuestos

- Los 3 SKU que no están en el catálogo se cargan en `dim_producto` con `en_catalogo = 0` y sin costo. Así no se pierde la venta, y la pregunta 6 los reporta aparte.
- El estado de la vía y la alerta de clima del día del pedido viven en `dim_estado_via` y `dim_alerta_clima`; el pedido apunta a ellas.
- `cruza_via` se calcula en la carga con la regla del enunciado: bodega de Bogotá hacia un municipio del Meta, o bodega de Villavicencio hacia Bogotá o Cundinamarca.
- Las dimensiones se actualizan con `MERGE` y los hechos se reconstruyen completos en cada corrida, por eso el proceso se puede repetir sin duplicar.
