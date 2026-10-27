# Reglas de calidad de datos

Cada fila es una regla que aplica `etl.py`. La columna *Filas* es la cantidad de filas del archivo origen que tocó la regla en la última corrida. Las reglas que terminan en **RECHAZO** mandan la fila a `stg.rechazos` con su motivo; las demás la corrigen o la dejan en NULL (porque el dato no existe).

| Regla | Columna | Problema y qué se hizo | Filas |
|---|---|---|---:|
| C01 | `sku` | SKU repetido en el catalogo: se conserva la primera fila | 1 |
| R01 | `(fila completa)` | Fila exactamente repetida (misma id_pedido+linea y mismos 34 valores) | 175 |
| F01 | `fecha_pedido` | Formato dd/mm/aaaa hh:mm en vez de ISO aaaa-mm-dd hh:mm:ss | 6.544 |
| F02 | `fecha_pedido` | Año imposible (2205, 2225, 2226): se corrige al año de la fecha prometida | 12 |
| R04 | `fecha_entrega_real` | Entrega real anterior a la fecha del pedido -> RECHAZO | 17 |
| R03a | `cantidad` | Cantidad negativa -> RECHAZO | 54 |
| R03b | `cantidad` | Cantidad cero -> RECHAZO | 39 |
| P01 | `precio_unitario` | Precio con formato '$17.900' (simbolo y punto de miles) | 4.351 |
| P02 | `precio_unitario` | Precio = 0: se reemplaza por el precio de lista vigente ese dia | 57 |
| R05 | `precio_unitario` | Precio 0 y sin precio de lista (SKU fuera del catalogo) -> RECHAZO | 1 |
| S01 | `sku` | SKU que no existe en el catalogo (SKU-9001/9002/9003): se carga marcado en_catalogo=0 | 87 |
| D01 | `descuento` | Descuento vacio: se asume 0 (supuesto documentado) | 2.964 |
| D02 | `descuento` | Descuento con '%' ('5%', '0%') | 3.867 |
| D03 | `descuento` | Descuento como decimal ('0.05', '0.10', '0.15') | 1.978 |
| T01 | `cliente_ciudad` | Ciudad mal escrita (mayusculas, 'Villavo', 'FUSA', 'Pto. López', '(Meta)'...) | 3.873 |
| T02 | `cliente_ciudad / cliente_departamento` | Espacios al inicio o al final | 1.442 |
| T03 | `cliente_departamento` | Departamento en variantes ('META', 'Distrito Capital', 'BOGOTÁ'...) | 2.190 |
| R06 | `cliente_ciudad` | Ciudad que no se reconoce -> RECHAZO | 0 |
| T04 | `cliente_nombre` | Nombre con espacios dobles | 848 |
| T05 | `cliente_nombre` | Nombre en MAYUSCULAS/minusculas o sin tildes (se unifica por documento) | 3.196 |
| T06 | `producto` | Nombre de producto en MAYUSCULAS (se usa el nombre del catalogo) | 1.490 |
| T07 | `canal` | Canal con 15 variantes ('WA', 'TEL', 'Página web', 'app'...) | 4.533 |
| T08 | `metodo_pago` | Metodo de pago con variantes ('Credito 30 dias', 'NEQUI', 'Efectivo contra entrega'...) | 3.154 |
| T09 | `estado_pedido` | Estado en MAYUSCULAS ('ENTREGADO', 'CANCELADO'...) | 1.168 |
| R07 | `estado/canal/pago` | Valor fuera del diccionario -> RECHAZO | 0 |
| T10 | `refrigerado` | Booleano en 6 formas ('S','Si','1','N','No','0') | 5.898 |
| T11 | `reclamo` | Reclamo en variantes ('NO','SI','Sí')  | 3.570 |
| N01 | `reclamo` | Reclamo vacio: se deja NULL (desconocido) | 1.385 |
| T12 | `transportadora_nit` | NIT sin puntos y/o sin digito de verificacion (12 formatos -> 4 NIT) | 5.935 |
| T13 | `cliente_email` | Email invalido: espacio, sin '.com', punto final o sin '@' ('ana @yahoo.com', 'x@gmail', 'xgmail.com') | 121 |
| T14 | `cliente_telefonos` | Varios telefonos en una celda (separados por / ; , -) -> 1FN | 6.788 |
| T15 | `distancia_km` | Decimal con coma ('13,4') | 732 |
| N02 | `distancia_km` | Distancia vacia: se deja NULL (no hay forma confiable de inferirla) | 292 |
| N03 | `calificacion_servicio` | Calificacion vacia: se deja NULL (el cliente no califico) | 4.637 |
| N04 | `fecha_entrega_real` | Entrega real vacia (pedidos Cancelado/Anulado, legitimo): NULL | 511 |
| N05 | `novedad_transportadora` | Novedad vacia: NULL | 6.630 |

## Conciliación (conteos por etapa)

| Etapa | Valor |
|---|---:|
| Filas leídas del CSV | 14.777 |
| Filas en `stg.pedidos_raw` (tal cual) | 14.777 |
| Filas rechazadas (`stg.rechazos`) | 284 |
| Líneas cargadas en `oltp.pedido_linea` | 14.493 |
| Líneas en `dw.fact_ventas_linea` | 14.493 |
| Pedidos en `oltp.pedido` | 2.895 |
| Pedidos en `dw.fact_entregas_pedido` | 2.895 |

**Cuadre de conteos:** 14.777 leídas = 284 rechazadas + 14.493 cargadas.

**Total de control (venta neta de pedidos Entregado):**

| Dónde | Venta neta |
|---|---:|
| Calculada en pandas | $1.348.784.235 |
| `oltp` | $1.348.784.235 |
| `dw.fact_ventas_linea` | $1.348.784.235 |
| `dw.fact_entregas_pedido` | $1.348.784.235 |

Los cuatro valores son iguales. El ETL termina con error si alguno no cuadra.

## Los 284 rechazos

| Regla | Motivo | Filas |
|---|---|---:|
| R01 | Fila duplicada exacta | 175 |
| R03 | Cantidad negativa (54) o cero (39) | 91 |
| R04 | Entrega real anterior al pedido | 17 |
| R05 | Precio 0 y sin precio de lista | 1 |

Nota: 175 + 91 + 17 + 1 = 284. (Las 93 filas con cantidad ≤ 0 son 91 aquí porque 2 ya habían salido como duplicadas.)

Cinco pedidos quedaron fuera completos porque todas sus líneas fueron rechazadas (2.900 pedidos en el CSV, 2.895 cargados).
