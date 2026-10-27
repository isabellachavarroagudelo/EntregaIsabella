/* ============================================================
   MercaLlano - Base de datos, esquemas y modelo OLTP (3FN)
   Motor: SQL Server 2019 o superior
   ============================================================ */
USE master;
GO
IF DB_ID(N'MercaLlano') IS NULL
    CREATE DATABASE MercaLlano;
GO
USE MercaLlano;
GO
SET QUOTED_IDENTIFIER ON;   -- necesario para columnas calculadas persistidas e indices filtrados
SET ANSI_NULLS ON;
GO
-- CREATE SCHEMA debe ir solo en su lote, por eso se envuelve en EXEC(...)
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'stg')  EXEC(N'CREATE SCHEMA stg');
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'oltp') EXEC(N'CREATE SCHEMA oltp');
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'dw')   EXEC(N'CREATE SCHEMA dw');
GO

/* ---------- 1. Geografia ---------- */
CREATE TABLE oltp.departamento (
    departamento_id TINYINT       IDENTITY(1,1) NOT NULL,
    nombre          NVARCHAR(40)  NOT NULL,
    CONSTRAINT PK_departamento        PRIMARY KEY (departamento_id),
    CONSTRAINT UQ_departamento_nombre UNIQUE (nombre)
);

CREATE TABLE oltp.ciudad (
    ciudad_id       SMALLINT      IDENTITY(1,1) NOT NULL,
    nombre          NVARCHAR(60)  NOT NULL,
    departamento_id TINYINT       NOT NULL,
    CONSTRAINT PK_ciudad        PRIMARY KEY (ciudad_id),
    CONSTRAINT UQ_ciudad_nombre UNIQUE (nombre),
    CONSTRAINT FK_ciudad_departamento
        FOREIGN KEY (departamento_id) REFERENCES oltp.departamento (departamento_id)
);

/* ---------- 2. Catalogos pequenos (listas de valores) ---------- */
CREATE TABLE oltp.segmento_cliente (
    segmento_id TINYINT      IDENTITY(1,1) NOT NULL,
    nombre      NVARCHAR(30) NOT NULL,
    CONSTRAINT PK_segmento_cliente PRIMARY KEY (segmento_id),
    CONSTRAINT UQ_segmento_nombre  UNIQUE (nombre)
);

CREATE TABLE oltp.canal (
    canal_id TINYINT      IDENTITY(1,1) NOT NULL,
    nombre   NVARCHAR(30) NOT NULL,
    CONSTRAINT PK_canal        PRIMARY KEY (canal_id),
    CONSTRAINT UQ_canal_nombre UNIQUE (nombre)
);

CREATE TABLE oltp.metodo_pago (
    metodo_pago_id TINYINT      IDENTITY(1,1) NOT NULL,
    nombre         NVARCHAR(40) NOT NULL,
    CONSTRAINT PK_metodo_pago        PRIMARY KEY (metodo_pago_id),
    CONSTRAINT UQ_metodo_pago_nombre UNIQUE (nombre)
);

CREATE TABLE oltp.estado_pedido (
    estado_id TINYINT      IDENTITY(1,1) NOT NULL,
    nombre    NVARCHAR(20) NOT NULL,          -- Entregado, Devuelto, Cancelado, Anulado
    CONSTRAINT PK_estado_pedido PRIMARY KEY (estado_id),
    CONSTRAINT UQ_estado_nombre UNIQUE (nombre)
);

CREATE TABLE oltp.novedad_transportadora (
    novedad_id  TINYINT      IDENTITY(1,1) NOT NULL,
    descripcion NVARCHAR(80) NOT NULL,
    CONSTRAINT PK_novedad      PRIMARY KEY (novedad_id),
    CONSTRAINT UQ_novedad_desc UNIQUE (descripcion)
);

/* ---------- 3. Actores ---------- */
CREATE TABLE oltp.cliente (
    cliente_id   INT           IDENTITY(1,1) NOT NULL,
    documento    VARCHAR(10)   NOT NULL,      -- 8 o 10 digitos, solo numeros
    nombre       NVARCHAR(120) NOT NULL,
    email        NVARCHAR(120) NULL,
    segmento_id  TINYINT       NOT NULL,
    direccion    NVARCHAR(200) NOT NULL,      -- direccion ACTUAL (la historica vive en pedido)
    ciudad_id    SMALLINT      NOT NULL,      -- ciudad ACTUAL
    CONSTRAINT PK_cliente           PRIMARY KEY (cliente_id),
    CONSTRAINT UQ_cliente_documento UNIQUE (documento),
    CONSTRAINT CK_cliente_documento CHECK (documento NOT LIKE '%[^0-9]%' AND LEN(documento) IN (8, 10)),
    CONSTRAINT FK_cliente_segmento  FOREIGN KEY (segmento_id) REFERENCES oltp.segmento_cliente (segmento_id),
    CONSTRAINT FK_cliente_ciudad    FOREIGN KEY (ciudad_id)   REFERENCES oltp.ciudad (ciudad_id)
);

CREATE TABLE oltp.cliente_telefono (          -- resuelve la violacion de 1FN de cliente_telefonos
    cliente_id   INT         NOT NULL,
    telefono     VARCHAR(15) NOT NULL,
    es_principal BIT         NOT NULL CONSTRAINT DF_cliente_telefono_principal DEFAULT (0),
    CONSTRAINT PK_cliente_telefono PRIMARY KEY (cliente_id, telefono),
    CONSTRAINT CK_telefono_digitos CHECK (telefono NOT LIKE '%[^0-9]%'),
    CONSTRAINT FK_cliente_telefono_cliente
        FOREIGN KEY (cliente_id) REFERENCES oltp.cliente (cliente_id)
);

CREATE TABLE oltp.bodega (
    bodega_id TINYINT      IDENTITY(1,1) NOT NULL,
    codigo    VARCHAR(10)  NOT NULL,          -- BOD-01, BOD-02, BOD-03
    nombre    NVARCHAR(80) NOT NULL,
    ciudad_id SMALLINT     NOT NULL,
    CONSTRAINT PK_bodega        PRIMARY KEY (bodega_id),
    CONSTRAINT UQ_bodega_codigo UNIQUE (codigo),
    CONSTRAINT FK_bodega_ciudad FOREIGN KEY (ciudad_id) REFERENCES oltp.ciudad (ciudad_id)
);

CREATE TABLE oltp.transportadora (
    transportadora_id TINYINT      IDENTITY(1,1) NOT NULL,
    nombre            NVARCHAR(80) NOT NULL,
    nit               VARCHAR(14)  NOT NULL,  -- formato canonico 900.876.543-2
    CONSTRAINT PK_transportadora        PRIMARY KEY (transportadora_id),
    CONSTRAINT UQ_transportadora_nombre UNIQUE (nombre),
    CONSTRAINT UQ_transportadora_nit    UNIQUE (nit),
    CONSTRAINT CK_transportadora_nit
        CHECK (nit LIKE '[0-9][0-9][0-9].[0-9][0-9][0-9].[0-9][0-9][0-9]-[0-9]')
);

CREATE TABLE oltp.proveedor (
    proveedor_id SMALLINT      IDENTITY(1,1) NOT NULL,
    nit          VARCHAR(14)   NOT NULL,
    nombre       NVARCHAR(100) NOT NULL,
    ciudad_id    SMALLINT      NOT NULL,
    telefono     VARCHAR(15)   NULL,
    CONSTRAINT PK_proveedor        PRIMARY KEY (proveedor_id),
    CONSTRAINT UQ_proveedor_nit    UNIQUE (nit),
    CONSTRAINT FK_proveedor_ciudad FOREIGN KEY (ciudad_id) REFERENCES oltp.ciudad (ciudad_id)
);

/* ---------- 4. Productos ---------- */
CREATE TABLE oltp.categoria (
    categoria_id TINYINT      IDENTITY(1,1) NOT NULL,
    nombre       NVARCHAR(40) NOT NULL,
    CONSTRAINT PK_categoria        PRIMARY KEY (categoria_id),
    CONSTRAINT UQ_categoria_nombre UNIQUE (nombre)
);

CREATE TABLE oltp.subcategoria (
    subcategoria_id SMALLINT     IDENTITY(1,1) NOT NULL,
    categoria_id    TINYINT      NOT NULL,
    nombre          NVARCHAR(40) NOT NULL,
    CONSTRAINT PK_subcategoria PRIMARY KEY (subcategoria_id),
    CONSTRAINT UQ_subcategoria UNIQUE (categoria_id, nombre),
    CONSTRAINT FK_subcategoria_categoria
        FOREIGN KEY (categoria_id) REFERENCES oltp.categoria (categoria_id)
);

CREATE TABLE oltp.producto (
    producto_id     INT           IDENTITY(1,1) NOT NULL,
    sku             VARCHAR(12)   NOT NULL,
    nombre          NVARCHAR(100) NOT NULL,
    subcategoria_id SMALLINT      NOT NULL,
    unidad_medida   CHAR(3)       NOT NULL,   -- UND o KG
    peso_kg         DECIMAL(7,3)  NOT NULL,
    refrigerado     BIT           NOT NULL,
    costo_unitario  DECIMAL(12,2) NULL,       -- NULL = sin costo (producto fuera del catalogo)
    proveedor_id    SMALLINT      NULL,       -- NULL = sin proveedor (SKU-9001 a SKU-9003)
    activo          BIT           NOT NULL CONSTRAINT DF_producto_activo      DEFAULT (1),
    en_catalogo     BIT           NOT NULL CONSTRAINT DF_producto_en_catalogo DEFAULT (1),
    CONSTRAINT PK_producto     PRIMARY KEY (producto_id),
    CONSTRAINT UQ_producto_sku UNIQUE (sku),
    CONSTRAINT CK_producto_unidad CHECK (unidad_medida IN ('UND', 'KG')),
    CONSTRAINT CK_producto_peso   CHECK (peso_kg > 0),
    CONSTRAINT CK_producto_costo  CHECK (costo_unitario IS NULL OR costo_unitario >= 0),
    CONSTRAINT FK_producto_subcategoria FOREIGN KEY (subcategoria_id) REFERENCES oltp.subcategoria (subcategoria_id),
    CONSTRAINT FK_producto_proveedor    FOREIGN KEY (proveedor_id)    REFERENCES oltp.proveedor (proveedor_id)
);

CREATE TABLE oltp.historial_precio (             -- hoja historial_precios del Excel
    producto_id   INT           NOT NULL,
    vigente_desde DATE          NOT NULL,
    vigente_hasta DATE          NULL,             -- NULL = vigente hoy
    precio_lista  DECIMAL(12,2) NOT NULL,
    CONSTRAINT PK_historial_precio PRIMARY KEY (producto_id, vigente_desde),
    CONSTRAINT CK_historial_precio_positivo CHECK (precio_lista > 0),
    CONSTRAINT CK_historial_precio_fechas   CHECK (vigente_hasta IS NULL OR vigente_hasta >= vigente_desde),
    CONSTRAINT FK_historial_precio_producto FOREIGN KEY (producto_id) REFERENCES oltp.producto (producto_id)
);

/* ---------- 5. Pedidos ---------- */
CREATE TABLE oltp.pedido (
    pedido_id               INT           IDENTITY(1,1) NOT NULL,
    codigo_pedido           VARCHAR(12)   NOT NULL,     -- PED-00001 (llave natural del legado)
    cliente_id              INT           NOT NULL,
    canal_id                TINYINT       NOT NULL,
    metodo_pago_id          TINYINT       NOT NULL,
    bodega_id               TINYINT       NOT NULL,
    transportadora_id       TINYINT       NOT NULL,
    estado_id               TINYINT       NOT NULL,
    ciudad_entrega_id       SMALLINT      NOT NULL,     -- ciudad del cliente EN ESE pedido (historia)
    direccion_entrega       NVARCHAR(200) NOT NULL,
    fecha_pedido            DATETIME2(0)  NOT NULL,
    franja_entrega          NVARCHAR(10)  NOT NULL,
    distancia_km            DECIMAL(7,1)  NULL,
    fecha_entrega_prometida DATE          NOT NULL,
    fecha_entrega_real      DATETIME2(0)  NULL,         -- NULL si no se entrego
    calificacion_servicio   TINYINT       NULL,
    reclamo                 BIT           NULL,
    novedad_id              TINYINT       NULL,
    CONSTRAINT PK_pedido        PRIMARY KEY (pedido_id),
    CONSTRAINT UQ_pedido_codigo UNIQUE (codigo_pedido),
    -- Regla de negocio 1: no se promete una entrega en el pasado
    CONSTRAINT CK_pedido_promesa
        CHECK (fecha_entrega_prometida >= CAST(fecha_pedido AS DATE)),
    -- Regla de negocio 2: no se entrega antes de que el cliente pida
    CONSTRAINT CK_pedido_entrega_real
        CHECK (fecha_entrega_real IS NULL OR fecha_entrega_real >= fecha_pedido),
    CONSTRAINT CK_pedido_calificacion CHECK (calificacion_servicio BETWEEN 1 AND 5),
    CONSTRAINT CK_pedido_distancia    CHECK (distancia_km IS NULL OR distancia_km >= 0),
    CONSTRAINT CK_pedido_franja       CHECK (franja_entrega IN (N'Mañana', N'Tarde', N'Noche')),
    CONSTRAINT FK_pedido_cliente        FOREIGN KEY (cliente_id)        REFERENCES oltp.cliente (cliente_id),
    CONSTRAINT FK_pedido_canal          FOREIGN KEY (canal_id)          REFERENCES oltp.canal (canal_id),
    CONSTRAINT FK_pedido_metodo_pago    FOREIGN KEY (metodo_pago_id)    REFERENCES oltp.metodo_pago (metodo_pago_id),
    CONSTRAINT FK_pedido_bodega         FOREIGN KEY (bodega_id)         REFERENCES oltp.bodega (bodega_id),
    CONSTRAINT FK_pedido_transportadora FOREIGN KEY (transportadora_id) REFERENCES oltp.transportadora (transportadora_id),
    CONSTRAINT FK_pedido_estado         FOREIGN KEY (estado_id)         REFERENCES oltp.estado_pedido (estado_id),
    CONSTRAINT FK_pedido_ciudad_entrega FOREIGN KEY (ciudad_entrega_id) REFERENCES oltp.ciudad (ciudad_id),
    CONSTRAINT FK_pedido_novedad        FOREIGN KEY (novedad_id)        REFERENCES oltp.novedad_transportadora (novedad_id)
);

CREATE TABLE oltp.pedido_linea (
    pedido_id       INT           NOT NULL,
    linea           SMALLINT      NOT NULL,
    producto_id     INT           NOT NULL,
    cantidad        DECIMAL(10,3) NOT NULL,
    precio_unitario DECIMAL(12,2) NOT NULL,    -- precio cobrado (foto del precio al momento del pedido)
    descuento       DECIMAL(4,3)  NOT NULL CONSTRAINT DF_linea_descuento DEFAULT (0),  -- fraccion de 0 a 1
    venta_neta AS CAST(precio_unitario * cantidad * (1 - descuento) AS DECIMAL(14,2)) PERSISTED,
    CONSTRAINT PK_pedido_linea PRIMARY KEY (pedido_id, linea),
    CONSTRAINT CK_linea_cantidad  CHECK (cantidad > 0),
    CONSTRAINT CK_linea_precio    CHECK (precio_unitario >= 0),
    CONSTRAINT CK_linea_descuento CHECK (descuento >= 0 AND descuento <= 1),
    CONSTRAINT FK_linea_pedido   FOREIGN KEY (pedido_id)   REFERENCES oltp.pedido (pedido_id),
    CONSTRAINT FK_linea_producto FOREIGN KEY (producto_id) REFERENCES oltp.producto (producto_id)
);

/* ---------- 6. Contexto diario (viene del JSON de novedades viales) ---------- */
CREATE TABLE oltp.via_estado_dia (
    fecha             DATE         NOT NULL,
    estado_via        NVARCHAR(20) NOT NULL,
    horas_restriccion TINYINT      NOT NULL,
    CONSTRAINT PK_via_estado_dia PRIMARY KEY (fecha),
    CONSTRAINT CK_via_estado CHECK (estado_via IN (N'Abierta', N'Paso restringido', N'Cierre total')),
    CONSTRAINT CK_via_horas  CHECK (horas_restriccion BETWEEN 0 AND 24)
);

CREATE TABLE oltp.clima_dia_ciudad (
    fecha     DATE         NOT NULL,
    ciudad_id SMALLINT     NOT NULL,
    lluvia_mm DECIMAL(6,1) NOT NULL,
    alerta    NVARCHAR(20) NULL,               -- NULL = sin alerta
    CONSTRAINT PK_clima_dia_ciudad PRIMARY KEY (fecha, ciudad_id),
    CONSTRAINT CK_clima_lluvia CHECK (lluvia_mm >= 0),
    CONSTRAINT CK_clima_alerta CHECK (alerta IS NULL OR alerta IN (N'Lluvia fuerte', N'Encharcamientos')),
    CONSTRAINT FK_clima_ciudad FOREIGN KEY (ciudad_id) REFERENCES oltp.ciudad (ciudad_id)
);
GO

/* ---------- 7. Indices de apoyo ---------- */
CREATE INDEX IX_pedido_cliente_fecha ON oltp.pedido (cliente_id, fecha_pedido);
CREATE INDEX IX_pedido_fecha         ON oltp.pedido (fecha_pedido) INCLUDE (estado_id, bodega_id, transportadora_id);
CREATE INDEX IX_linea_producto       ON oltp.pedido_linea (producto_id);
GO
