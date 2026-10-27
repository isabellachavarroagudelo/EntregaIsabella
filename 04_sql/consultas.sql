/* ============================================================
   consultas.sql  -  Parte 4 (SQL analitico)  -  MercaLlano S.A.S.
   Motor: SQL Server 2019+ (T-SQL).  Base: MercaLlano.  Esquema consultado: dw

   Tablas del modelo dimensional usadas (nombres del DDL de la Parte 2):
   - dw.fact_ventas_linea    (grano: linea de pedido). Puede traer todos los estados de
                             pedido; las ventas solo cuentan 'Entregado', por eso cada
                             consulta une dw.dim_estado_pedido y filtra cuenta_como_venta = 1.
       llaves: fecha_key, cliente_key, producto_key, estado_pedido_key, ...
       medidas: cantidad, venta_neta, costo_total (NULL = sin costo), es_costeable
   - dw.fact_entregas_pedido (grano: pedido): fecha_entrega_key (NULL si no se entrego),
       entregado_a_tiempo, cruza_via, estado_via_key, ciudad_entrega_key, ...
   - dw.dim_cliente (SCD2: cliente_key, cliente_id, documento, segmento, ciudad,
       departamento, fecha_inicio, fecha_fin, es_actual)
   - dw.dim_producto (categoria, nombre_producto, proveedor_nombre, en_catalogo)
   - dw.dim_fecha, dw.dim_ciudad, dw.dim_transportadora, dw.dim_estado_pedido,
     dw.dim_estado_via
   ============================================================ */
USE MercaLlano;
GO


-- Pregunta 1
-- Ventas netas por mes y por categoria de producto, en orden cronologico.
SELECT
    d.anio,
    d.mes,
    CASE WHEN p.en_catalogo = 1 THEN p.categoria ELSE N'Sin catalogo' END AS categoria,
    SUM(f.venta_neta)                                                     AS venta_neta
FROM dw.fact_ventas_linea  AS f
JOIN dw.dim_estado_pedido  AS ep ON ep.estado_pedido_key = f.estado_pedido_key
                                AND ep.cuenta_como_venta = 1               -- solo pedidos Entregado
JOIN dw.dim_fecha          AS d  ON d.fecha_key    = f.fecha_key
JOIN dw.dim_producto       AS p  ON p.producto_key = f.producto_key        -- los SKU fuera del catalogo existen como producto con en_catalogo = 0
GROUP BY d.anio, d.mes, CASE WHEN p.en_catalogo = 1 THEN p.categoria ELSE N'Sin catalogo' END
ORDER BY d.anio, d.mes, categoria;
GO


-- Pregunta 2
-- Top 10 clientes por ventas netas: documento, nombre, segmento, ciudad ACTUAL,
-- numero de pedidos y ticket promedio por pedido.
WITH ventas_cliente AS (
    SELECT
        c.documento,                                       -- agrupamos por documento, NO por cliente_key:
        SUM(f.venta_neta)               AS venta_neta,     -- un cliente con SCD2 tiene varias cliente_key
        COUNT(DISTINCT f.codigo_pedido) AS pedidos
    FROM dw.fact_ventas_linea AS f
    JOIN dw.dim_estado_pedido AS ep ON ep.estado_pedido_key = f.estado_pedido_key
                                   AND ep.cuenta_como_venta = 1
    JOIN dw.dim_cliente       AS c  ON c.cliente_key = f.cliente_key
    GROUP BY c.documento
)
SELECT TOP (10)
    v.documento,
    a.nombre,
    a.segmento,
    a.ciudad                                  AS ciudad_actual,
    v.pedidos,
    v.venta_neta,
    v.venta_neta * 1.0 / v.pedidos            AS ticket_promedio
FROM ventas_cliente AS v
JOIN dw.dim_cliente AS a
     ON a.documento = v.documento AND a.es_actual = 1     -- version vigente del cliente
ORDER BY v.venta_neta DESC;
GO


-- Pregunta 3
-- % de pedidos entregados tarde por transportadora y departamento de destino
-- (la ciudad de entrega guardada en el pedido = donde estaba el cliente AL MOMENTO del pedido).
-- Solo combinaciones con >= 30 pedidos.
SELECT
    t.nombre                                              AS transportadora,
    ci.departamento                                       AS departamento_destino,
    COUNT(*)                                              AS pedidos_entregados,
    SUM(CASE WHEN e.entregado_a_tiempo = 0 THEN 1 ELSE 0 END) AS pedidos_tarde,
    CAST(100.0 * SUM(CASE WHEN e.entregado_a_tiempo = 0 THEN 1 ELSE 0 END)
         / COUNT(*) AS DECIMAL(5,1))                      AS pct_tarde
FROM dw.fact_entregas_pedido AS e
JOIN dw.dim_estado_pedido    AS ep ON ep.estado_pedido_key = e.estado_pedido_key
                                  AND ep.cuenta_como_venta = 1            -- pedidos Entregado
JOIN dw.dim_ciudad           AS ci ON ci.ciudad_key         = e.ciudad_entrega_key
JOIN dw.dim_transportadora   AS t  ON t.transportadora_key  = e.transportadora_key
WHERE e.entregado_a_tiempo IS NOT NULL                                    -- tiene entrega real
GROUP BY t.nombre, ci.departamento
HAVING COUNT(*) >= 30
ORDER BY pct_tarde DESC;
GO


-- Pregunta 4
-- Los 3 productos con mas unidades vendidas dentro de cada categoria (funcion de ventana).
WITH unidades AS (
    SELECT
        p.categoria,
        p.producto_id,                                   -- llave de negocio: une las versiones SCD2 del producto
        MAX(p.nombre_producto) AS nombre_producto,
        SUM(f.cantidad)        AS unidades
    FROM dw.fact_ventas_linea AS f
    JOIN dw.dim_estado_pedido AS ep ON ep.estado_pedido_key = f.estado_pedido_key
                                   AND ep.cuenta_como_venta = 1
    JOIN dw.dim_producto      AS p  ON p.producto_key = f.producto_key
    WHERE p.en_catalogo = 1                              -- sin catalogo no hay categoria
    GROUP BY p.categoria, p.producto_id
), ranking AS (
    SELECT
        categoria, nombre_producto, unidades,
        ROW_NUMBER() OVER (PARTITION BY categoria
                           ORDER BY unidades DESC, nombre_producto) AS puesto   -- desempata por nombre
    FROM unidades
)
SELECT categoria, puesto, nombre_producto, unidades
FROM ranking
WHERE puesto <= 3
ORDER BY categoria, puesto;
GO


-- Pregunta 5
-- Variacion porcentual de las ventas netas de cada mes frente al mes anterior (LAG).
WITH mensual AS (
    SELECT d.anio, d.mes, SUM(f.venta_neta) AS venta_neta
    FROM dw.fact_ventas_linea AS f
    JOIN dw.dim_estado_pedido AS ep ON ep.estado_pedido_key = f.estado_pedido_key
                                   AND ep.cuenta_como_venta = 1
    JOIN dw.dim_fecha         AS d  ON d.fecha_key = f.fecha_key
    GROUP BY d.anio, d.mes
)
SELECT
    anio,
    mes,
    venta_neta,
    LAG(venta_neta) OVER (ORDER BY anio, mes) AS venta_mes_anterior,
    CAST(100.0 * (venta_neta - LAG(venta_neta) OVER (ORDER BY anio, mes))
         / NULLIF(LAG(venta_neta) OVER (ORDER BY anio, mes), 0) AS DECIMAL(8,1)) AS variacion_pct
FROM mensual
ORDER BY anio, mes;      -- el primer mes sale NULL: no tiene mes anterior
GO


-- Pregunta 6a
-- Margen bruto (venta neta - costo) y % de margen por proveedor (solo lineas costeables).
SELECT
    p.proveedor_nombre                              AS proveedor,
    SUM(f.venta_neta)                               AS venta_neta,
    SUM(f.costo_total)                              AS costo,
    SUM(f.venta_neta) - SUM(f.costo_total)          AS margen_bruto,
    CAST(100.0 * (SUM(f.venta_neta) - SUM(f.costo_total))
         / NULLIF(SUM(f.venta_neta), 0) AS DECIMAL(5,1)) AS pct_margen
FROM dw.fact_ventas_linea AS f
JOIN dw.dim_estado_pedido AS ep ON ep.estado_pedido_key = f.estado_pedido_key
                               AND ep.cuenta_como_venta = 1
JOIN dw.dim_producto      AS p  ON p.producto_key = f.producto_key
WHERE f.es_costeable = 1
GROUP BY p.proveedor_nombre
ORDER BY margen_bruto DESC;
GO

-- Pregunta 6b
-- Lineas y venta que NO se pueden costear (producto inexistente en el catalogo).
SELECT
    COUNT(*)           AS lineas_sin_costo,
    SUM(f.venta_neta)  AS venta_sin_costo
FROM dw.fact_ventas_linea AS f
JOIN dw.dim_estado_pedido AS ep ON ep.estado_pedido_key = f.estado_pedido_key
                               AND ep.cuenta_como_venta = 1
WHERE f.es_costeable = 0;
GO


-- Pregunta 7
-- Pedidos que cruzan la via Bogota-Villavicencio: % de entregas tardias segun el
-- estado de la via el dia del pedido.
SELECT
    v.estado_via,
    COUNT(*)                                                    AS pedidos_entregados,
    SUM(CASE WHEN e.entregado_a_tiempo = 0 THEN 1 ELSE 0 END)   AS pedidos_tarde,
    CAST(100.0 * SUM(CASE WHEN e.entregado_a_tiempo = 0 THEN 1 ELSE 0 END)
         / COUNT(*) AS DECIMAL(5,1))                            AS pct_tarde
FROM dw.fact_entregas_pedido AS e
JOIN dw.dim_estado_pedido    AS ep ON ep.estado_pedido_key = e.estado_pedido_key
                                  AND ep.cuenta_como_venta = 1               -- pedidos Entregado
JOIN dw.dim_estado_via       AS v  ON v.estado_via_key = e.estado_via_key
WHERE e.cruza_via = 1
  AND e.entregado_a_tiempo IS NOT NULL
GROUP BY v.estado_via, v.nivel_restriccion
ORDER BY v.nivel_restriccion;       -- Abierta, Paso restringido, Cierre total
GO


-- Pregunta 8
-- Clientes que compraron en el 1er trimestre de 2025 y NO volvieron a comprar en el
-- 1er semestre de 2026, agrupados por segmento.
WITH compras AS (                       -- una fila por cliente (documento) y dia de compra
    SELECT DISTINCT c.documento, d.fecha
    FROM dw.fact_ventas_linea AS f
    JOIN dw.dim_estado_pedido AS ep ON ep.estado_pedido_key = f.estado_pedido_key
                                   AND ep.cuenta_como_venta = 1
    JOIN dw.dim_cliente       AS c  ON c.cliente_key = f.cliente_key
    JOIN dw.dim_fecha         AS d  ON d.fecha_key   = f.fecha_key
), q1_2025 AS (
    SELECT DISTINCT documento
    FROM compras
    WHERE fecha >= '2025-01-01' AND fecha < '2025-04-01'
), perdidos AS (
    SELECT q.documento
    FROM q1_2025 AS q
    WHERE NOT EXISTS (
        SELECT 1 FROM compras AS k
        WHERE k.documento = q.documento
          AND k.fecha >= '2026-01-01' AND k.fecha < '2026-07-01'
    )
)
SELECT
    a.segmento,
    COUNT(*)                                             AS clientes_perdidos,
    (SELECT COUNT(*) FROM q1_2025 AS q2
       JOIN dw.dim_cliente AS a2 ON a2.documento = q2.documento AND a2.es_actual = 1
      WHERE a2.segmento = a.segmento)                    AS clientes_q1_2025,
    CAST(100.0 * COUNT(*) /
         (SELECT COUNT(*) FROM q1_2025 AS q2
            JOIN dw.dim_cliente AS a2 ON a2.documento = q2.documento AND a2.es_actual = 1
           WHERE a2.segmento = a.segmento) AS DECIMAL(5,1)) AS pct_perdidos
FROM perdidos AS p
JOIN dw.dim_cliente AS a ON a.documento = p.documento AND a.es_actual = 1
GROUP BY a.segmento
ORDER BY clientes_perdidos DESC;
GO
