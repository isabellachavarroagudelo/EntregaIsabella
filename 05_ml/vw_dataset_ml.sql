/* Vista de entrenamiento: UNA fila por pedido, solo con informacion conocida al confirmar el pedido.
   Usa los nombres del DDL de las Partes 1 y 2 (base MercaLlano):
   - dw.fact_entregas_pedido : grano pedido (llaves *_key, distancia_km, cruza_via, lluvia_destino_mm,
                               entregado_a_tiempo, franja_entrega)
   - dw.dim_cliente, dim_ciudad, dim_bodega, dim_transportadora, dim_canal, dim_metodo_pago,
     dim_estado_pedido, dim_estado_via, dim_alerta_clima, dim_fecha
   - oltp.pedido (fecha_pedido y fecha_entrega_real con hora), oltp.pedido_linea, oltp.producto (peso, refrigerado) */
USE MercaLlano;
GO
CREATE OR ALTER VIEW dw.vw_dataset_ml AS
WITH lineas AS (                                   -- 1) resumen de las lineas de cada pedido
    SELECT l.pedido_id,
           COUNT(*)                                         AS n_lineas,
           SUM(l.cantidad)                                  AS unidades,
           SUM(l.cantidad * pr.peso_kg)                     AS peso_kg,
           SUM(l.cantidad * l.precio_unitario)              AS venta_bruta,
           SUM(CASE WHEN pr.refrigerado = 1 THEN l.cantidad ELSE 0 END) * 1.0
               / NULLIF(SUM(l.cantidad), 0)                 AS pct_refrigerado
    FROM oltp.pedido_linea AS l
    JOIN oltp.producto     AS pr ON pr.producto_id = l.producto_id
    WHERE l.cantidad > 0
    GROUP BY l.pedido_id
), base AS (                                       -- 2) TODOS los pedidos (el historial los necesita)
    SELECT e.codigo_pedido, o.pedido_id, c.documento,
           o.fecha_pedido, o.fecha_entrega_real AS fecha_real,
           ep.estado,
           CAST(o.fecha_pedido AS date)                      AS dia_pedido,
           fp.fecha                                          AS fecha_prometida,
           e.cruza_via, ev.estado_via,
           CASE WHEN e.entregado_a_tiempo IS NOT NULL
                THEN 1 - CAST(e.entregado_a_tiempo AS int) END   AS entregado_tarde,   -- variable objetivo
           c.segmento, ci.ciudad AS ciudad_destino, ci.departamento,   -- ciudad de entrega EN ese pedido
           e.distancia_km, mp.metodo_pago, e.franja_entrega,
           ca.canal, b.ciudad AS bodega, t.nombre AS transportadora,
           e.lluvia_destino_mm, al.alerta
    FROM dw.fact_entregas_pedido AS e
    JOIN oltp.pedido             AS o  ON o.codigo_pedido = e.codigo_pedido
    JOIN dw.dim_fecha            AS fp ON fp.fecha_key = e.fecha_prometida_key
    JOIN dw.dim_canal            AS ca ON ca.canal_key = e.canal_key
    JOIN dw.dim_metodo_pago      AS mp ON mp.metodo_pago_key = e.metodo_pago_key
    JOIN dw.dim_estado_pedido    AS ep ON ep.estado_pedido_key = e.estado_pedido_key
    JOIN dw.dim_estado_via       AS ev ON ev.estado_via_key = e.estado_via_key
    JOIN dw.dim_alerta_clima     AS al ON al.alerta_clima_key = e.alerta_clima_key
    JOIN dw.dim_cliente          AS c  ON c.cliente_key = e.cliente_key
    JOIN dw.dim_ciudad           AS ci ON ci.ciudad_key = e.ciudad_entrega_key
    JOIN dw.dim_bodega           AS b  ON b.bodega_key = e.bodega_key
    JOIN dw.dim_transportadora   AS t  ON t.transportadora_key = e.transportadora_key
), hist AS (                                       -- 3) historial del cliente (solo el PASADO)
    SELECT b.*,
           COUNT(*) OVER (PARTITION BY b.documento ORDER BY b.fecha_pedido, b.pedido_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)        AS pedidos_previos,
           DATEDIFF(day, LAG(b.dia_pedido) OVER (PARTITION BY b.documento ORDER BY b.fecha_pedido, b.pedido_id),
                    b.dia_pedido)                                                  AS dias_desde_ultimo
    FROM base AS b
)
SELECT
    h.codigo_pedido AS id_pedido, h.fecha_pedido, h.canal, h.segmento, h.ciudad_destino, h.departamento,
    h.bodega, h.transportadora, h.distancia_km, h.metodo_pago, h.franja_entrega,
    l.n_lineas, l.unidades, l.peso_kg, l.venta_bruta, l.pct_refrigerado,
    DATEDIFF(day, h.dia_pedido, h.fecha_prometida)         AS dias_prometidos,
    CAST(h.cruza_via AS int)                               AS cruza_via,
    h.estado_via,                                          -- estado de la via el dia del pedido
    h.lluvia_destino_mm                                    AS lluvia_mm_destino,
    CASE WHEN h.alerta = N'Sin alerta' THEN NULL ELSE h.alerta END AS alerta_destino,
    h.pedidos_previos,
    prev.pct_tarde_previo,                                 -- solo entregas que YA habian llegado ese dia
    h.dias_desde_ultimo,
    h.entregado_tarde                                      -- <- variable objetivo (y)
FROM hist AS h
JOIN lineas AS l ON l.pedido_id = h.pedido_id
OUTER APPLY (
    SELECT AVG(CAST(p.entregado_tarde AS float)) AS pct_tarde_previo
    FROM base AS p
    WHERE p.documento = h.documento
      AND p.fecha_real IS NOT NULL
      AND CAST(p.fecha_real AS date) < h.dia_pedido        -- evita mirar el futuro
) AS prev
WHERE h.estado IN (N'Entregado', N'Devuelto')              -- se excluyen Cancelado/Anulado
  AND h.fecha_real IS NOT NULL                             -- sin fecha real no hay etiqueta
  AND CAST(h.fecha_real AS date) >= h.dia_pedido;          -- descarta fechas incoherentes (real < pedido)
GO
