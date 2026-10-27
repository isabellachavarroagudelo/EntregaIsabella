/* ============================================================
   MercaLlano - Modelo dimensional (Kimball) en el esquema dw
   Requiere haber ejecutado antes el script de la Parte 1
   (crea la base MercaLlano y los esquemas stg, oltp y dw).
   ============================================================ */
USE MercaLlano;
GO
SET QUOTED_IDENTIFIER ON;   -- necesario para columnas calculadas persistidas e indices filtrados
SET ANSI_NULLS ON;
GO
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'dw') EXEC(N'CREATE SCHEMA dw');
GO

/* =================== DIMENSIONES =================== */

-- dim_fecha: llave "inteligente" yyyymmdd (la unica excepcion aceptada a la regla de llave sin significado)
CREATE TABLE dw.dim_fecha (
    fecha_key        INT          NOT NULL,          -- 20250131
    fecha            DATE         NOT NULL,
    anio             SMALLINT     NOT NULL,
    trimestre        TINYINT      NOT NULL,
    mes              TINYINT      NOT NULL,
    nombre_mes       NVARCHAR(12) NOT NULL,
    anio_mes         CHAR(7)      NOT NULL,          -- 2025-01
    dia              TINYINT      NOT NULL,
    dia_semana       TINYINT      NOT NULL,          -- 1 = lunes ... 7 = domingo
    nombre_dia       NVARCHAR(12) NOT NULL,
    semana_iso       TINYINT      NOT NULL,
    es_fin_de_semana BIT          NOT NULL,
    es_festivo       BIT          NOT NULL CONSTRAINT DF_dim_fecha_festivo DEFAULT (0),
    CONSTRAINT PK_dim_fecha PRIMARY KEY (fecha_key),
    CONSTRAINT UQ_dim_fecha_fecha UNIQUE (fecha)
);

CREATE TABLE dw.dim_cliente (                         -- SCD tipo 2
    cliente_key   INT           IDENTITY(1,1) NOT NULL,   -- surrogate key
    cliente_id    INT           NOT NULL,                 -- llave de negocio (oltp.cliente)
    documento     VARCHAR(10)   NOT NULL,
    nombre        NVARCHAR(120) NOT NULL,                 -- SCD1 (se sobrescribe)
    segmento      NVARCHAR(30)  NOT NULL,                 -- SCD1 (no cambia en los datos)
    direccion     NVARCHAR(200) NOT NULL,                 -- SCD2
    ciudad        NVARCHAR(60)  NOT NULL,                 -- SCD2
    departamento  NVARCHAR(40)  NOT NULL,                 -- SCD2
    fecha_inicio  DATE          NOT NULL,
    fecha_fin     DATE          NOT NULL CONSTRAINT DF_dim_cliente_fin DEFAULT ('9999-12-31'),
    es_actual     BIT           NOT NULL CONSTRAINT DF_dim_cliente_actual DEFAULT (1),
    CONSTRAINT PK_dim_cliente PRIMARY KEY (cliente_key),
    CONSTRAINT UQ_dim_cliente_version UNIQUE (cliente_id, fecha_inicio),
    CONSTRAINT CK_dim_cliente_vigencia CHECK (fecha_fin >= fecha_inicio)
);
-- Solo una version actual por cliente
CREATE UNIQUE INDEX UX_dim_cliente_actual ON dw.dim_cliente (cliente_id) WHERE es_actual = 1;

CREATE TABLE dw.dim_producto (                        -- SCD tipo 2
    producto_key     INT           IDENTITY(1,1) NOT NULL,
    producto_id      INT           NOT NULL,                -- llave de negocio (oltp.producto)
    sku              VARCHAR(12)   NOT NULL,
    nombre_producto  NVARCHAR(100) NOT NULL,                -- SCD1
    categoria        NVARCHAR(40)  NOT NULL,                -- SCD1
    subcategoria     NVARCHAR(40)  NOT NULL,                -- SCD1
    unidad_medida    CHAR(3)       NOT NULL,
    peso_kg          DECIMAL(7,3)  NOT NULL,
    refrigerado      BIT           NOT NULL,
    proveedor_nit    VARCHAR(14)   NULL,                    -- SCD2
    proveedor_nombre NVARCHAR(100) NULL,                    -- SCD2 (NULL = producto sin catalogo)
    proveedor_ciudad NVARCHAR(60)  NULL,
    costo_unitario   DECIMAL(12,2) NULL,                    -- SCD2
    precio_lista     DECIMAL(12,2) NULL,                    -- SCD2 (viene de historial_precio)
    activo           BIT           NOT NULL,
    en_catalogo      BIT           NOT NULL,
    fecha_inicio     DATE          NOT NULL,
    fecha_fin        DATE          NOT NULL CONSTRAINT DF_dim_producto_fin DEFAULT ('9999-12-31'),
    es_actual        BIT           NOT NULL CONSTRAINT DF_dim_producto_actual DEFAULT (1),
    CONSTRAINT PK_dim_producto PRIMARY KEY (producto_key),
    CONSTRAINT UQ_dim_producto_version UNIQUE (producto_id, fecha_inicio),
    CONSTRAINT CK_dim_producto_vigencia CHECK (fecha_fin >= fecha_inicio)
);
CREATE UNIQUE INDEX UX_dim_producto_actual ON dw.dim_producto (producto_id) WHERE es_actual = 1;

CREATE TABLE dw.dim_bodega (
    bodega_key   TINYINT      IDENTITY(1,1) NOT NULL,
    bodega_id    TINYINT      NOT NULL,
    codigo       VARCHAR(10)  NOT NULL,
    nombre       NVARCHAR(80) NOT NULL,
    ciudad       NVARCHAR(60) NOT NULL,
    departamento NVARCHAR(40) NOT NULL,
    CONSTRAINT PK_dim_bodega PRIMARY KEY (bodega_key),
    CONSTRAINT UQ_dim_bodega_id UNIQUE (bodega_id)
);

CREATE TABLE dw.dim_transportadora (
    transportadora_key TINYINT      IDENTITY(1,1) NOT NULL,
    transportadora_id  TINYINT      NOT NULL,
    nombre             NVARCHAR(80) NOT NULL,
    nit                VARCHAR(14)  NOT NULL,
    es_flota_propia    BIT          NOT NULL,
    CONSTRAINT PK_dim_transportadora PRIMARY KEY (transportadora_key),
    CONSTRAINT UQ_dim_transportadora_id UNIQUE (transportadora_id)
);

CREATE TABLE dw.dim_ciudad (                          -- ciudad de entrega y ciudad de clima
    ciudad_key   SMALLINT     IDENTITY(1,1) NOT NULL,
    ciudad_id    SMALLINT     NOT NULL,
    ciudad       NVARCHAR(60) NOT NULL,
    departamento NVARCHAR(40) NOT NULL,
    CONSTRAINT PK_dim_ciudad PRIMARY KEY (ciudad_key),
    CONSTRAINT UQ_dim_ciudad_id UNIQUE (ciudad_id)
);

CREATE TABLE dw.dim_canal (
    canal_key TINYINT      IDENTITY(1,1) NOT NULL,
    canal_id  TINYINT      NOT NULL,
    canal     NVARCHAR(30) NOT NULL,
    CONSTRAINT PK_dim_canal PRIMARY KEY (canal_key),
    CONSTRAINT UQ_dim_canal_id UNIQUE (canal_id)
);

CREATE TABLE dw.dim_metodo_pago (
    metodo_pago_key TINYINT      IDENTITY(1,1) NOT NULL,
    metodo_pago_id  TINYINT      NOT NULL,
    metodo_pago     NVARCHAR(40) NOT NULL,
    CONSTRAINT PK_dim_metodo_pago PRIMARY KEY (metodo_pago_key),
    CONSTRAINT UQ_dim_metodo_pago_id UNIQUE (metodo_pago_id)
);

CREATE TABLE dw.dim_estado_pedido (
    estado_pedido_key  TINYINT      IDENTITY(1,1) NOT NULL,
    estado_id          TINYINT      NOT NULL,
    estado             NVARCHAR(20) NOT NULL,
    cuenta_como_venta  BIT          NOT NULL,          -- 1 solo para 'Entregado'
    CONSTRAINT PK_dim_estado_pedido PRIMARY KEY (estado_pedido_key),
    CONSTRAINT UQ_dim_estado_pedido_id UNIQUE (estado_id)
);

-- Dimension de via: contexto del dia del pedido (Parte 4, pregunta 7)
CREATE TABLE dw.dim_estado_via (
    estado_via_key    TINYINT      IDENTITY(1,1) NOT NULL,
    estado_via        NVARCHAR(20) NOT NULL,
    nivel_restriccion TINYINT      NOT NULL,            -- 0 abierta, 1 paso restringido, 2 cierre total
    CONSTRAINT PK_dim_estado_via PRIMARY KEY (estado_via_key),
    CONSTRAINT UQ_dim_estado_via UNIQUE (estado_via),
    CONSTRAINT CK_dim_estado_via_nivel CHECK (nivel_restriccion BETWEEN 0 AND 2)
);

-- Dimension de alerta climatica del dia en la ciudad de destino
CREATE TABLE dw.dim_alerta_clima (
    alerta_clima_key TINYINT      IDENTITY(1,1) NOT NULL,
    alerta           NVARCHAR(20) NOT NULL,             -- 'Sin alerta', 'Encharcamientos', 'Lluvia fuerte'
    nivel_alerta     TINYINT      NOT NULL,
    CONSTRAINT PK_dim_alerta_clima PRIMARY KEY (alerta_clima_key),
    CONSTRAINT UQ_dim_alerta_clima UNIQUE (alerta)
);
GO

/* =================== HECHOS =================== */

-- Grano: una fila por LINEA de pedido (pedido + numero de linea)
CREATE TABLE dw.fact_ventas_linea (
    codigo_pedido      VARCHAR(12)   NOT NULL,   -- dimension degenerada
    linea              SMALLINT      NOT NULL,   -- dimension degenerada
    fecha_key          INT           NOT NULL,   -- fecha del pedido
    cliente_key        INT           NOT NULL,   -- version vigente el dia del pedido
    producto_key       INT           NOT NULL,   -- version vigente el dia del pedido
    bodega_key         TINYINT       NOT NULL,
    transportadora_key TINYINT       NOT NULL,
    canal_key          TINYINT       NOT NULL,
    metodo_pago_key    TINYINT       NOT NULL,
    estado_pedido_key  TINYINT       NOT NULL,
    ciudad_entrega_key SMALLINT      NOT NULL,
    -- medidas
    cantidad           DECIMAL(10,3) NOT NULL,   -- aditiva (cuidado al mezclar KG y UND)
    precio_unitario    DECIMAL(12,2) NOT NULL,   -- NO aditiva
    descuento_pct      DECIMAL(4,3)  NOT NULL,   -- NO aditiva
    venta_bruta        DECIMAL(14,2) NOT NULL,   -- aditiva = precio * cantidad
    descuento_valor    DECIMAL(14,2) NOT NULL,   -- aditiva
    venta_neta         DECIMAL(14,2) NOT NULL,   -- aditiva = precio * cantidad * (1 - descuento)
    costo_total        DECIMAL(14,2) NULL,       -- aditiva; NULL si el producto no esta en el catalogo
    es_costeable       BIT           NOT NULL,
    margen_bruto AS (venta_neta - costo_total) PERSISTED,   -- aditiva; NULL si no hay costo
    CONSTRAINT PK_fact_ventas_linea PRIMARY KEY (codigo_pedido, linea),
    CONSTRAINT CK_fvl_cantidad CHECK (cantidad > 0),
    CONSTRAINT CK_fvl_descuento CHECK (descuento_pct >= 0 AND descuento_pct <= 1),
    CONSTRAINT FK_fvl_fecha         FOREIGN KEY (fecha_key)          REFERENCES dw.dim_fecha (fecha_key),
    CONSTRAINT FK_fvl_cliente       FOREIGN KEY (cliente_key)        REFERENCES dw.dim_cliente (cliente_key),
    CONSTRAINT FK_fvl_producto      FOREIGN KEY (producto_key)       REFERENCES dw.dim_producto (producto_key),
    CONSTRAINT FK_fvl_bodega        FOREIGN KEY (bodega_key)         REFERENCES dw.dim_bodega (bodega_key),
    CONSTRAINT FK_fvl_transportadora FOREIGN KEY (transportadora_key) REFERENCES dw.dim_transportadora (transportadora_key),
    CONSTRAINT FK_fvl_canal         FOREIGN KEY (canal_key)          REFERENCES dw.dim_canal (canal_key),
    CONSTRAINT FK_fvl_metodo_pago   FOREIGN KEY (metodo_pago_key)    REFERENCES dw.dim_metodo_pago (metodo_pago_key),
    CONSTRAINT FK_fvl_estado        FOREIGN KEY (estado_pedido_key)  REFERENCES dw.dim_estado_pedido (estado_pedido_key),
    CONSTRAINT FK_fvl_ciudad        FOREIGN KEY (ciudad_entrega_key) REFERENCES dw.dim_ciudad (ciudad_key)
);

-- Grano: una fila por PEDIDO (la entrega completa, sin importar cuantas lineas tenga)
CREATE TABLE dw.fact_entregas_pedido (
    codigo_pedido        VARCHAR(12)   NOT NULL,   -- dimension degenerada, llave del grano
    fecha_pedido_key     INT           NOT NULL,   -- rol 1 de dim_fecha
    fecha_prometida_key  INT           NOT NULL,   -- rol 2 de dim_fecha
    fecha_entrega_key    INT           NULL,       -- rol 3 de dim_fecha; NULL si no se entrego
    cliente_key          INT           NOT NULL,
    bodega_key           TINYINT       NOT NULL,
    transportadora_key   TINYINT       NOT NULL,
    ciudad_entrega_key   SMALLINT      NOT NULL,
    canal_key            TINYINT       NOT NULL,
    metodo_pago_key      TINYINT       NOT NULL,
    estado_pedido_key    TINYINT       NOT NULL,
    estado_via_key       TINYINT       NOT NULL,   -- estado de la via el dia del pedido
    alerta_clima_key     TINYINT       NOT NULL,   -- alerta en la ciudad de entrega el dia del pedido
    franja_entrega       NVARCHAR(10)  NOT NULL,   -- dimension degenerada (3 valores)
    -- medidas
    pedidos              TINYINT       NOT NULL CONSTRAINT DF_fep_pedidos DEFAULT (1),  -- aditiva (contador)
    num_lineas           SMALLINT      NOT NULL,   -- aditiva
    unidades_totales     DECIMAL(12,3) NOT NULL,   -- aditiva
    peso_total_kg        DECIMAL(12,3) NOT NULL,   -- aditiva
    venta_neta_pedido    DECIMAL(14,2) NOT NULL,   -- aditiva
    distancia_km         DECIMAL(7,1)  NULL,       -- aditiva (suma de km de pedidos), el promedio se calcula
    dias_retraso         SMALLINT      NULL,       -- NO aditiva (negativo = llego antes)
    horas_hasta_entrega  DECIMAL(8,1)  NULL,       -- NO aditiva (se promedia)
    entregado_a_tiempo   BIT           NULL,       -- NULL = no se entrego; aditiva como conteo
    cruza_via            BIT           NOT NULL,   -- bandera; aditiva como conteo
    lluvia_destino_mm    DECIMAL(6,1)  NULL,       -- NO aditiva (NULL = sin dato ese dia)
    horas_restriccion_via TINYINT      NULL,       -- NO aditiva
    calificacion_servicio TINYINT      NULL,       -- NO aditiva (se promedia)
    reclamo              BIT           NULL,       -- aditiva como conteo
    CONSTRAINT PK_fact_entregas_pedido PRIMARY KEY (codigo_pedido),
    CONSTRAINT CK_fep_calificacion CHECK (calificacion_servicio IS NULL OR calificacion_servicio BETWEEN 1 AND 5),
    CONSTRAINT CK_fep_a_tiempo CHECK (entregado_a_tiempo IS NULL OR fecha_entrega_key IS NOT NULL),
    CONSTRAINT FK_fep_fecha_pedido    FOREIGN KEY (fecha_pedido_key)    REFERENCES dw.dim_fecha (fecha_key),
    CONSTRAINT FK_fep_fecha_prometida FOREIGN KEY (fecha_prometida_key) REFERENCES dw.dim_fecha (fecha_key),
    CONSTRAINT FK_fep_fecha_entrega   FOREIGN KEY (fecha_entrega_key)   REFERENCES dw.dim_fecha (fecha_key),
    CONSTRAINT FK_fep_cliente         FOREIGN KEY (cliente_key)         REFERENCES dw.dim_cliente (cliente_key),
    CONSTRAINT FK_fep_bodega          FOREIGN KEY (bodega_key)          REFERENCES dw.dim_bodega (bodega_key),
    CONSTRAINT FK_fep_transportadora  FOREIGN KEY (transportadora_key)  REFERENCES dw.dim_transportadora (transportadora_key),
    CONSTRAINT FK_fep_ciudad          FOREIGN KEY (ciudad_entrega_key)  REFERENCES dw.dim_ciudad (ciudad_key),
    CONSTRAINT FK_fep_canal           FOREIGN KEY (canal_key)           REFERENCES dw.dim_canal (canal_key),
    CONSTRAINT FK_fep_metodo_pago     FOREIGN KEY (metodo_pago_key)     REFERENCES dw.dim_metodo_pago (metodo_pago_key),
    CONSTRAINT FK_fep_estado          FOREIGN KEY (estado_pedido_key)   REFERENCES dw.dim_estado_pedido (estado_pedido_key),
    CONSTRAINT FK_fep_estado_via      FOREIGN KEY (estado_via_key)      REFERENCES dw.dim_estado_via (estado_via_key),
    CONSTRAINT FK_fep_alerta_clima    FOREIGN KEY (alerta_clima_key)    REFERENCES dw.dim_alerta_clima (alerta_clima_key)
);

-- Indices para los joins mas frecuentes de la Parte 4
CREATE INDEX IX_fvl_fecha    ON dw.fact_ventas_linea (fecha_key) INCLUDE (venta_neta, cantidad);
CREATE INDEX IX_fvl_producto ON dw.fact_ventas_linea (producto_key);
CREATE INDEX IX_fvl_cliente  ON dw.fact_ventas_linea (cliente_key);
CREATE INDEX IX_fep_fecha    ON dw.fact_entregas_pedido (fecha_pedido_key);
CREATE INDEX IX_fep_transp   ON dw.fact_entregas_pedido (transportadora_key, ciudad_entrega_key);
GO

/* =================== FILAS FIJAS DE DIMENSIONES PEQUENAS =================== */
INSERT INTO dw.dim_estado_via (estado_via, nivel_restriccion)
SELECT v.estado_via, v.nivel
FROM (VALUES (N'Abierta', 0), (N'Paso restringido', 1), (N'Cierre total', 2)) AS v(estado_via, nivel)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_estado_via e WHERE e.estado_via = v.estado_via);

INSERT INTO dw.dim_alerta_clima (alerta, nivel_alerta)
SELECT v.alerta, v.nivel
FROM (VALUES (N'Sin alerta', 0), (N'Encharcamientos', 1), (N'Lluvia fuerte', 2)) AS v(alerta, nivel)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_alerta_clima a WHERE a.alerta = v.alerta);
GO

/* =================== POBLAR dim_fecha (re-ejecutable) =================== */
;WITH dias AS (
    SELECT CAST('20250101' AS DATE) AS f
    UNION ALL
    SELECT DATEADD(DAY, 1, f) FROM dias WHERE f < CAST('20261231' AS DATE)
)
INSERT INTO dw.dim_fecha
    (fecha_key, fecha, anio, trimestre, mes, nombre_mes, anio_mes, dia,
     dia_semana, nombre_dia, semana_iso, es_fin_de_semana, es_festivo)
SELECT
    CONVERT(INT, CONVERT(CHAR(8), f, 112)),
    f,
    YEAR(f),
    DATEPART(QUARTER, f),
    MONTH(f),
    CHOOSE(MONTH(f), N'Enero', N'Febrero', N'Marzo', N'Abril', N'Mayo', N'Junio',
                     N'Julio', N'Agosto', N'Septiembre', N'Octubre', N'Noviembre', N'Diciembre'),
    CONVERT(CHAR(7), f, 120),
    DAY(f),
    (DATEDIFF(DAY, '19000101', f) % 7) + 1,                 -- 1900-01-01 fue lunes; no depende de DATEFIRST
    CHOOSE((DATEDIFF(DAY, '19000101', f) % 7) + 1,
           N'Lunes', N'Martes', N'Miércoles', N'Jueves', N'Viernes', N'Sábado', N'Domingo'),
    DATEPART(ISO_WEEK, f),
    CASE WHEN (DATEDIFF(DAY, '19000101', f) % 7) + 1 >= 6 THEN 1 ELSE 0 END,
    -- Solo festivos de fecha fija; los que se trasladan al lunes se cargan aparte desde el calendario oficial
    CASE WHEN (MONTH(f) = 1  AND DAY(f) = 1)  OR (MONTH(f) = 5  AND DAY(f) = 1)
           OR (MONTH(f) = 7  AND DAY(f) = 20) OR (MONTH(f) = 8  AND DAY(f) = 7)
           OR (MONTH(f) = 12 AND DAY(f) = 8)  OR (MONTH(f) = 12 AND DAY(f) = 25)
         THEN 1 ELSE 0 END
FROM dias
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_fecha d WHERE d.fecha = dias.f)
OPTION (MAXRECURSION 0);
GO
