# -*- coding: utf-8 -*-
r"""
etl.py - MercaLlano S.A.S.
Flujo: archivos crudos -> stg (tal cual) -> oltp (limpio y normalizado) -> dw (dimensional)

Uso:
    py etl.py --dry-run                      # solo lee y limpia (no toca SQL Server)
    py etl.py                                # corrida completa en .\SQLEXPRESS
    py etl.py --server localhost --db MercaLlano --datos ..\datos_crudos
"""
import argparse
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------
# 0. CONFIGURACION
# --------------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
PERIODO_INI = pd.Timestamp("2025-01-01")
PERIODO_FIN = pd.Timestamp("2026-06-30 23:59:59")
FIN_DE_LOS_TIEMPOS = pd.Timestamp("2099-12-31")   # "sin fecha fin" (9999 no cabe en pandas 2.x)

# Diccionarios de normalizacion (la clave siempre va en minuscula y sin tildes)
CIUDADES = {  # clave -> (nombre oficial, departamento)
    "villavicencio": ("Villavicencio", "Meta"), "acacias": ("Acacías", "Meta"),
    "restrepo": ("Restrepo", "Meta"), "cumaral": ("Cumaral", "Meta"),
    "granada": ("Granada", "Meta"), "puerto lopez": ("Puerto López", "Meta"),
    "puerto gaitan": ("Puerto Gaitán", "Meta"),
    "bogota": ("Bogotá", "Bogotá D.C."),
    "soacha": ("Soacha", "Cundinamarca"), "chia": ("Chía", "Cundinamarca"),
    "fusagasuga": ("Fusagasugá", "Cundinamarca"),
    "yopal": ("Yopal", "Casanare"), "aguazul": ("Aguazul", "Casanare"),
    "villanueva": ("Villanueva", "Casanare"),
}
ALIAS_CIUDAD = {"villavo": "villavicencio", "fusa": "fusagasuga",
                "pto lopez": "puerto lopez", "pto gaitan": "puerto gaitan"}
CANALES = {"web": "Web", "pagina web": "Web", "app": "App", "app movil": "App",
           "whatsapp": "WhatsApp", "wa": "WhatsApp",
           "telefono": "Teléfono", "tel": "Teléfono"}
PAGOS = {"credito 30 dias": "Crédito 30 días", "pse": "PSE", "nequi": "Nequi",
         "contraentrega": "Contraentrega", "efectivo contra entrega": "Contraentrega",
         "tarjeta": "Tarjeta", "tarjeta credito/debito": "Tarjeta"}
ESTADOS = {"entregado": "Entregado", "cancelado": "Cancelado",
           "anulado": "Anulado", "devuelto": "Devuelto"}
SI = {"s", "si", "1"}
NO = {"n", "no", "0"}

COLS_PEDIDOS = ["id_pedido", "linea", "fecha_pedido", "canal", "cliente_documento",
                "cliente_nombre", "cliente_email", "cliente_telefonos",
                "cliente_direccion", "cliente_ciudad", "cliente_departamento",
                "segmento_cliente", "bodega_codigo", "bodega_nombre", "bodega_ciudad",
                "transportadora", "transportadora_nit", "distancia_km", "metodo_pago",
                "franja_entrega", "sku", "producto", "categoria", "refrigerado",
                "peso_unitario_kg", "precio_unitario", "cantidad", "descuento",
                "fecha_entrega_prometida", "fecha_entrega_real", "estado_pedido",
                "calificacion_servicio", "reclamo", "novedad_transportadora"]


# --------------------------------------------------------------------------
# 1. UTILIDADES DE TEXTO
# --------------------------------------------------------------------------
def clave(s: str) -> str:
    """'  Pto.  LÓPEZ ' -> 'pto. lopez'  (sin tildes, minuscula, espacios simples)."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())


def espacios(s: str) -> str:
    """Quita espacios de los bordes y colapsa los dobles."""
    return " ".join(str(s).split())


def clave_ciudad(s: str) -> str:
    k = clave(s)
    k = re.sub(r"\(.*?\)", "", k)           # 'Granada (Meta)' -> 'granada '
    k = k.split(",")[0]                      # 'Villavicencio, Meta' -> 'villavicencio'
    k = k.replace("d.c.", "").replace(".", "")
    k = " ".join(k.split())
    return ALIAS_CIUDAD.get(k, k)


def mas_frecuente_legible(serie: pd.Series) -> str:
    """Entre las variantes de un nombre elige la mejor: no TODO MAYUSCULA ni todo
    minuscula, con tildes si alguna variante las tiene (quitar tildes es perder
    informacion) y, a igualdad, la mas frecuente. Si no hay ninguna, Title Case."""
    v = serie.map(espacios)
    mixtas = v[~(v.str.isupper() | v.str.islower())]
    if not len(mixtas):
        return v.iloc[0].title()
    cuenta = mixtas.value_counts()
    con_tildes = {n: sum(ord(ch) > 127 for ch in n) for n in cuenta.index}
    return sorted(cuenta.index, key=lambda n: (-con_tildes[n], -cuenta[n]))[0]


def nit_canonico(serie: pd.Series) -> pd.Series:
    """'900876543', '900876543-2' o '900.876.543-2' -> '900.876.543-2'.
    El digito de verificacion faltante se toma de las filas que si lo traen."""
    dig = serie.str.replace(r"\D", "", regex=True)
    dv = {d[:9]: d[9] for d in dig if len(d) == 10}
    def fmt(d):
        b = d[:9]
        return f"{b[:3]}.{b[3:6]}.{b[6:9]}-{dv.get(b, '')}".rstrip("-")
    return dig.map(fmt)


def email_limpio(s: str) -> str:
    e = s.replace(" ", "").lower().rstrip(".")
    if "@" not in e:                         # 'ana.perezgmail.com' -> 'ana.perez@gmail.com'
        e = re.sub(r"(gmail|hotmail|yahoo|outlook)\.com$", r"@\1.com", e)
    if "@" in e and "." not in e.split("@")[1]:
        e += ".com"                          # 'ana@gmail' -> 'ana@gmail.com'
    return e


def descuento_a_fraccion(s: str):
    s = s.strip()
    if s == "":
        return 0.0                           # SUPUESTO: vacio = sin descuento
    v = float(s[:-1]) / 100 if s.endswith("%") else float(s)
    return v / 100 if v > 1 else v           # '5' -> 0.05 por si apareciera


def parsear_fecha_pedido(serie: pd.Series) -> pd.Series:
    iso = pd.to_datetime(serie.where(~serie.str.contains("/")),
                         format="%Y-%m-%d %H:%M:%S", errors="coerce")
    lat = pd.to_datetime(serie.where(serie.str.contains("/")),
                         format="%d/%m/%Y %H:%M", errors="coerce")
    return iso.fillna(lat)


# --------------------------------------------------------------------------
# 2. EXTRAER
# --------------------------------------------------------------------------
def leer_crudos(datos: Path):
    pedidos = pd.read_csv(datos / "pedidos_raw.csv", dtype=str,
                          keep_default_na=False, encoding="utf-8")
    productos = pd.read_excel(datos / "catalogo_productos.xlsx", sheet_name="productos",
                              header=2, dtype=str, keep_default_na=False)
    precios = pd.read_excel(datos / "catalogo_productos.xlsx", sheet_name="historial_precios",
                            header=2, dtype=str, keep_default_na=False)
    with open(datos / "novedades_viales.json", encoding="utf-8") as f:
        js = json.load(f)
    filas = []
    for d in js["registros"]:
        via = d["via_bogota_villavicencio"]
        for c in d["ciudades"]:
            filas.append({"fecha": d["fecha"], "estado_via": via["estado"],
                          "horas_restriccion": str(via["horas_restriccion"]),
                          "ciudad": c["ciudad"], "lluvia_mm": str(c["lluvia_mm"]),
                          "alerta": "" if c["alerta"] is None else c["alerta"]})
    novedades = pd.DataFrame(filas)
    for df in (pedidos, productos, precios, novedades):
        df.insert(0, "fila_origen", range(2 if df is pedidos else 4, len(df) + (2 if df is pedidos else 4)))
    return pedidos, productos, precios, novedades


# --------------------------------------------------------------------------
# 3. TRANSFORMAR (puro pandas: se puede probar sin SQL Server)
# --------------------------------------------------------------------------
class Reglas:
    """Libreta de cuentas: cuantas filas toco cada regla."""
    def __init__(self):
        self.filas = []

    def anota(self, regla, columna, descripcion, mascara):
        self.filas.append({"regla": regla, "columna": columna,
                           "descripcion": descripcion, "filas": int(mascara.sum())})


def limpiar_catalogo(productos, precios, R):
    rech = []
    p = productos.copy()
    dup = p.duplicated("sku", keep="first")
    R.anota("C01", "sku", "SKU repetido en el catalogo: se conserva la primera fila", dup)
    for _, r in p[dup].iterrows():
        rech.append({"archivo": "catalogo_productos.xlsx/productos", "fila_origen": r.fila_origen,
                     "llave": r.sku, "regla": "C01",
                     "motivo": "SKU duplicado en el catalogo (se conserva la primera fila; costo distinto: "
                               f"{r.costo_unitario})",
                     "fila_cruda": json.dumps(r.drop("fila_origen").to_dict(), ensure_ascii=False)})
    p = p[~dup].copy()
    p["nombre_producto"] = p.nombre_producto.map(espacios)
    p["costo_unitario"] = p.costo_unitario.astype(float)
    p["peso_kg"] = p.peso_kg.astype(float)
    p["refrigerado"] = p.refrigerado.map(clave).isin(SI)
    p["activo"] = p.activo.map(clave).isin(SI)
    p["proveedor_nit"] = nit_canonico(p.proveedor_nit)
    h = precios.copy()
    h["precio_lista"] = h.precio_lista.astype(int)
    h["vigente_desde"] = pd.to_datetime(h.vigente_desde)
    h["vigente_hasta"] = pd.to_datetime(h.vigente_hasta.replace("", pd.NA)).fillna(FIN_DE_LOS_TIEMPOS)
    return p, h, rech


def limpiar_pedidos(raw, cat, hist, R):
    df = raw.copy()
    n = len(df)
    rech = []

    def rechazar(mask, regla, motivo):
        for _, r in df[mask].iterrows():
            rech.append({"archivo": "pedidos_raw.csv", "fila_origen": int(r.fila_origen),
                         "llave": f"{r.id_pedido}/{r.linea}", "regla": regla, "motivo": motivo,
                         "fila_cruda": json.dumps({c: r[c] for c in COLS_PEDIDOS}, ensure_ascii=False)})

    activo = pd.Series(True, index=df.index)   # filas que siguen "vivas"

    # --- R01 duplicados exactos (se compara TODA la fila original) ---------
    dup = df.duplicated(subset=COLS_PEDIDOS, keep="first")
    R.anota("R01", "(fila completa)", "Fila exactamente repetida (misma id_pedido+linea y mismos 34 valores)", dup)
    rechazar(dup, "R01", "Duplicado exacto de una fila anterior")
    activo &= ~dup

    # --- Fechas -------------------------------------------------------------
    R.anota("F01", "fecha_pedido", "Formato dd/mm/aaaa hh:mm en vez de ISO aaaa-mm-dd hh:mm:ss",
            df.fecha_pedido.str.contains("/"))
    df["fecha_pedido_d"] = parsear_fecha_pedido(df.fecha_pedido)
    df["fecha_prom_d"] = pd.to_datetime(df.fecha_entrega_prometida, format="%Y-%m-%d", errors="coerce")
    df["fecha_real_d"] = pd.to_datetime(df.fecha_entrega_real.replace("", pd.NA),
                                        format="%Y-%m-%d %H:%M", errors="coerce")
    fuera = (df.fecha_pedido_d < PERIODO_INI) | (df.fecha_pedido_d > PERIODO_FIN)
    R.anota("F02", "fecha_pedido", "Año imposible (2205, 2225, 2226): se corrige al año de la fecha prometida", fuera)
    for i in df.index[fuera]:
        df.loc[i, "fecha_pedido_d"] = df.loc[i, "fecha_pedido_d"].replace(year=df.loc[i, "fecha_prom_d"].year)
    tarde_real = df.fecha_real_d < df.fecha_pedido_d.dt.normalize()
    R.anota("R04", "fecha_entrega_real", "Entrega real anterior a la fecha del pedido -> RECHAZO", tarde_real & activo)
    rechazar(tarde_real & activo, "R04", "fecha_entrega_real es anterior a fecha_pedido")
    activo &= ~tarde_real

    # --- Cantidad -------------------------------------------------------------
    df["cantidad_n"] = pd.to_numeric(df.cantidad, errors="coerce")
    neg = df.cantidad_n < 0
    cero = df.cantidad_n == 0
    R.anota("R03a", "cantidad", "Cantidad negativa -> RECHAZO", neg)
    R.anota("R03b", "cantidad", "Cantidad cero -> RECHAZO", cero)
    rechazar((neg | cero) & activo, "R03", "Cantidad menor o igual a cero")
    activo &= ~(neg | cero)

    # --- Precio ---------------------------------------------------------------
    R.anota("P01", "precio_unitario", "Precio con formato '$17.900' (simbolo y punto de miles)",
            df.precio_unitario.str.startswith("$"))
    df["precio_n"] = df.precio_unitario.str.replace(r"[$.\s]", "", regex=True).astype(int)
    cero_p = df.precio_n == 0
    R.anota("P02", "precio_unitario", "Precio = 0: se reemplaza por el precio de lista vigente ese dia", cero_p)
    h = df[["fila_origen", "sku", "fecha_pedido_d"]].merge(hist.drop(columns="fila_origen"), on="sku", how="left")
    h = h[(h.fecha_pedido_d.dt.normalize() >= h.vigente_desde) &
          (h.fecha_pedido_d.dt.normalize() <= h.vigente_hasta)].drop_duplicates("fila_origen")
    lista = df.fila_origen.map(h.set_index("fila_origen").precio_lista)
    df.loc[cero_p & lista.notna(), "precio_n"] = lista[cero_p & lista.notna()].astype(int)
    sin_precio = (df.precio_n == 0)
    R.anota("R05", "precio_unitario", "Precio 0 y sin precio de lista (SKU fuera del catalogo) -> RECHAZO", sin_precio & activo)
    rechazar(sin_precio & activo, "R05", "Precio 0 y no existe precio de lista para imputarlo")
    activo &= ~sin_precio

    # --- Catalogo vs pedido -----------------------------------------------------
    nocat = ~df.sku.isin(cat.sku)
    R.anota("S01", "sku", "SKU que no existe en el catalogo (SKU-9001/9002/9003): se carga marcado en_catalogo=0", nocat)

    # --- Descuento --------------------------------------------------------------
    R.anota("D01", "descuento", "Descuento vacio: se asume 0 (supuesto documentado)", df.descuento.str.strip() == "")
    R.anota("D02", "descuento", "Descuento con '%' ('5%', '0%')", df.descuento.str.endswith("%"))
    R.anota("D03", "descuento", "Descuento como decimal ('0.05', '0.10', '0.15')", df.descuento.str.startswith("0."))
    df["descuento_f"] = df.descuento.map(descuento_a_fraccion)

    # --- Texto y categorias --------------------------------------------------------
    df["cliente_ciudad_c"] = df.cliente_ciudad.map(clave_ciudad)
    desconocida = ~df.cliente_ciudad_c.isin(CIUDADES)
    df["ciudad"] = df.cliente_ciudad_c.map(lambda k: CIUDADES.get(k, (None, None))[0])
    df["departamento"] = df.cliente_ciudad_c.map(lambda k: CIUDADES.get(k, (None, None))[1])
    R.anota("T01", "cliente_ciudad", "Ciudad mal escrita (mayusculas, 'Villavo', 'FUSA', 'Pto. López', '(Meta)'...)",
            df.cliente_ciudad != df.ciudad)
    R.anota("T02", "cliente_ciudad / cliente_departamento", "Espacios al inicio o al final",
            (df.cliente_ciudad != df.cliente_ciudad.str.strip()) | (df.cliente_departamento != df.cliente_departamento.str.strip()))
    R.anota("T03", "cliente_departamento", "Departamento en variantes ('META', 'Distrito Capital', 'BOGOTÁ'...)",
            df.cliente_departamento != df.departamento)
    R.anota("R06", "cliente_ciudad", "Ciudad que no se reconoce -> RECHAZO", desconocida & activo)
    rechazar(desconocida & activo, "R06", "Ciudad no reconocida")
    activo &= ~desconocida

    R.anota("T04", "cliente_nombre", "Nombre con espacios dobles", df.cliente_nombre.str.contains(r"\s{2,}"))
    nombres = df.groupby("cliente_documento").cliente_nombre.agg(mas_frecuente_legible)
    df["cliente_nombre_c"] = df.cliente_documento.map(nombres)
    R.anota("T05", "cliente_nombre", "Nombre en MAYUSCULAS/minusculas o sin tildes (se unifica por documento)",
            df.cliente_nombre.map(espacios).ne(df.cliente_nombre_c))
    R.anota("T06", "producto", "Nombre de producto en MAYUSCULAS (se usa el nombre del catalogo)",
            df.producto.str.isupper())
    df["canal_c"] = df.canal.map(lambda s: CANALES.get(clave(s)))
    R.anota("T07", "canal", "Canal con 15 variantes ('WA', 'TEL', 'Página web', 'app'...)",
            df.canal != df.canal_c)
    df["pago_c"] = df.metodo_pago.map(lambda s: PAGOS.get(clave(s)))
    R.anota("T08", "metodo_pago", "Metodo de pago con variantes ('Credito 30 dias', 'NEQUI', 'Efectivo contra entrega'...)",
            df.metodo_pago != df.pago_c)
    df["estado_c"] = df.estado_pedido.map(lambda s: ESTADOS.get(clave(s)))
    R.anota("T09", "estado_pedido", "Estado en MAYUSCULAS ('ENTREGADO', 'CANCELADO'...)", df.estado_pedido != df.estado_c)
    bad = df.estado_c.isna() | df.canal_c.isna() | df.pago_c.isna()
    R.anota("R07", "estado/canal/pago", "Valor fuera del diccionario -> RECHAZO", bad & activo)
    rechazar(bad & activo, "R07", "Estado, canal o metodo de pago no reconocido")
    activo &= ~bad
    df["refrigerado_b"] = df.refrigerado.map(lambda s: clave(s) in SI)
    R.anota("T10", "refrigerado", "Booleano en 6 formas ('S','Si','1','N','No','0')", ~df.refrigerado.isin(["S", "N"]))
    df["reclamo_b"] = df.reclamo.map(lambda s: None if s.strip() == "" else clave(s) in SI)
    R.anota("T11", "reclamo", "Reclamo en variantes ('NO','SI','Sí') ", ~df.reclamo.isin(["S", "N", ""]))
    R.anota("N01", "reclamo", "Reclamo vacio: se deja NULL (desconocido)", df.reclamo.str.strip() == "")

    # --- NIT, email, telefonos, distancia, calificacion ------------------------------
    df["nit_c"] = nit_canonico(df.transportadora_nit)
    R.anota("T12", "transportadora_nit", "NIT sin puntos y/o sin digito de verificacion (12 formatos -> 4 NIT)",
            df.transportadora_nit != df.nit_c)
    df["email_c"] = df.cliente_email.map(email_limpio)
    R.anota("T13", "cliente_email", "Email invalido: espacio, sin '.com', punto final o sin '@' ('ana @yahoo.com', 'x@gmail', 'xgmail.com')",
            df.cliente_email != df.email_c)
    R.anota("T14", "cliente_telefonos", "Varios telefonos en una celda (separados por / ; , -) -> 1FN",
            df.cliente_telefonos.str.contains(r"[/;,\-]"))
    R.anota("T15", "distancia_km", "Decimal con coma ('13,4')", df.distancia_km.str.contains(","))
    df["distancia_n"] = pd.to_numeric(df.distancia_km.str.replace(",", "."), errors="coerce")
    R.anota("N02", "distancia_km", "Distancia vacia: se deja NULL (no hay forma confiable de inferirla)", df.distancia_km == "")
    R.anota("N03", "calificacion_servicio", "Calificacion vacia: se deja NULL (el cliente no califico)", df.calificacion_servicio == "")
    R.anota("N04", "fecha_entrega_real", "Entrega real vacia (pedidos Cancelado/Anulado, legitimo): NULL", df.fecha_entrega_real == "")
    R.anota("N05", "novedad_transportadora", "Novedad vacia: NULL", df.novedad_transportadora == "")

    # --- Venta neta ---------------------------------------------------------------------
    df["venta_neta"] = (df.precio_n * df.cantidad_n * (1 - df.descuento_f)).round(2)

    limpio = df[activo].copy()
    limpio["fecha_pedido"] = limpio.fecha_pedido_d
    return limpio, rech


def tablas_oltp(limpio, cat, hist):
    """Parte las filas planas en tablas normalizadas (DataFrames)."""
    t = {}
    # clientes: version "actual" = pedido mas reciente
    ordenado = limpio.sort_values(["cliente_documento", "fecha_pedido"])
    ult = ordenado.groupby("cliente_documento").tail(1)
    t["cliente"] = ult[["cliente_documento", "cliente_nombre_c", "email_c", "segmento_cliente",
                        "cliente_direccion", "ciudad"]].rename(columns={
        "cliente_documento": "documento", "cliente_nombre_c": "nombre", "email_c": "email",
        "segmento_cliente": "segmento", "cliente_direccion": "direccion"})
    tel = ult[["cliente_documento", "cliente_telefonos"]].copy()
    tel["telefono"] = tel.cliente_telefonos.str.split(r"\s*[/;,\-]\s*")
    tel = tel.explode("telefono")
    tel["es_principal"] = ~tel.duplicated("cliente_documento")
    t["cliente_telefono"] = tel[["cliente_documento", "telefono", "es_principal"]].rename(
        columns={"cliente_documento": "documento"})
    # historial de cliente (para el SCD2 del dw)
    h = ordenado[["cliente_documento", "cliente_nombre_c", "segmento_cliente", "cliente_direccion",
                  "ciudad", "departamento", "fecha_pedido"]].copy()
    h["fecha"] = h.fecha_pedido.dt.normalize()
    h["cambio"] = (h[["cliente_direccion", "ciudad"]].ne(
        h.groupby("cliente_documento")[["cliente_direccion", "ciudad"]].shift())).any(axis=1)
    h = h[h.cambio].drop_duplicates(["cliente_documento", "fecha"], keep="last")
    h["fecha_inicio"] = h.fecha
    h.loc[~h.duplicated("cliente_documento"), "fecha_inicio"] = pd.Timestamp("1900-01-01")
    h["fecha_fin"] = h.groupby("cliente_documento").fecha.shift(-1) - pd.Timedelta(days=1)
    h["es_actual"] = h.fecha_fin.isna()
    h["fecha_fin"] = h.fecha_fin.fillna(FIN_DE_LOS_TIEMPOS)
    t["cliente_hist"] = h[["cliente_documento", "cliente_nombre_c", "segmento_cliente", "cliente_direccion",
                           "ciudad", "departamento", "fecha_inicio", "fecha_fin", "es_actual"]].rename(
        columns={"cliente_documento": "documento", "cliente_nombre_c": "nombre", "segmento_cliente": "segmento",
                 "cliente_direccion": "direccion"})
    # pedido (cabecera: 1 fila por id_pedido)
    cab = limpio.sort_values(["id_pedido", "linea"]).drop_duplicates("id_pedido")
    t["pedido"] = cab[["id_pedido", "fecha_pedido", "canal_c", "cliente_documento", "ciudad", "cliente_direccion",
                       "bodega_codigo", "nit_c", "pago_c", "franja_entrega", "distancia_n", "fecha_prom_d",
                       "fecha_real_d", "estado_c", "calificacion_servicio", "reclamo_b",
                       "novedad_transportadora"]].rename(columns={
        "canal_c": "canal", "cliente_documento": "documento", "ciudad": "ciudad_entrega",
        "cliente_direccion": "direccion_entrega", "nit_c": "transportadora_nit", "pago_c": "metodo_pago",
        "distancia_n": "distancia_km", "fecha_prom_d": "fecha_entrega_prometida",
        "fecha_real_d": "fecha_entrega_real", "estado_c": "estado_pedido", "reclamo_b": "reclamo"})
    t["pedido"]["calificacion_servicio"] = pd.to_numeric(t["pedido"].calificacion_servicio, errors="coerce").astype("Int64")
    t["pedido"]["novedad_transportadora"] = t["pedido"].novedad_transportadora.replace("", None)
    t["linea"] = limpio[["id_pedido", "linea", "sku", "cantidad_n", "precio_n", "descuento_f", "venta_neta"]].rename(
        columns={"cantidad_n": "cantidad", "precio_n": "precio_unitario", "descuento_f": "descuento"})
    t["linea"]["linea"] = t["linea"].linea.astype(int)
    t["linea"]["cantidad"] = t["linea"].cantidad.astype(int)
    t["bodega"] = limpio.drop_duplicates("bodega_codigo")[["bodega_codigo", "bodega_nombre", "bodega_ciudad"]]
    t["bodega"] = t["bodega"].assign(bodega_ciudad=t["bodega"].bodega_ciudad.map(lambda s: CIUDADES[clave_ciudad(s)][0]))
    t["transportadora"] = limpio.groupby("nit_c").transportadora.agg(mas_frecuente_legible).reset_index().rename(
        columns={"nit_c": "nit", "transportadora": "nombre"})
    # productos: catalogo + los SKU que solo existen en pedidos
    fuera = limpio[~limpio.sku.isin(cat.sku)]
    extra = fuera.groupby("sku").agg(nombre=("producto", mas_frecuente_legible), categoria=("categoria", "first"),
                                    peso_kg=("peso_unitario_kg", lambda s: float(s.iloc[0])),
                                    refrigerado=("refrigerado_b", "first")).reset_index()
    base = cat.rename(columns={"nombre_producto": "nombre"})[
        ["sku", "nombre", "categoria", "subcategoria", "unidad_medida", "peso_kg", "refrigerado",
         "costo_unitario", "proveedor_nit", "activo"]]
    base["en_catalogo"] = True
    extra["en_catalogo"] = False
    extra["activo"] = True
    extra["subcategoria"] = "Sin clasificar"     # fuera del catalogo: no hay subcategoria ni unidad de medida
    extra["unidad_medida"] = "UND"
    t["producto"] = pd.concat([base, extra], ignore_index=True)
    t["proveedor"] = cat.drop_duplicates("proveedor_nit")[["proveedor_nit", "proveedor_nombre", "proveedor_ciudad",
                                                           "proveedor_telefono"]]
    return t


# --------------------------------------------------------------------------
# 4. CARGAR EN SQL SERVER
# --------------------------------------------------------------------------
def conectar(server, db, driver, rapido=True):
    from sqlalchemy import create_engine
    from urllib.parse import quote_plus
    cs = (f"DRIVER={{{driver}}};SERVER={server};DATABASE={db};"
          "Trusted_Connection=yes;TrustServerCertificate=yes;")
    return create_engine("mssql+pyodbc:///?odbc_connect=" + quote_plus(cs), fast_executemany=rapido)


def a_python(x):
    """Un valor de pandas/numpy -> valor que pyodbc entiende sin sorpresas."""
    if isinstance(x, pd.Timestamp):
        return x.to_pydatetime()
    if hasattr(x, "item"):                   # numpy.int64 / float64 / bool_ -> int / float / bool
        x = x.item()
    if isinstance(x, float):                 # texto plano: evita que pyodbc mande 0.05 como '5e-02'
        return f"{x:.6f}"                    # SQL Server lo convierte solo a DECIMAL/FLOAT
    return x


def filas(df):
    """DataFrame -> lista de tuplas con None en vez de NaN/NaT y tipos de Python."""
    d = df.astype(object).where(df.notna(), None)
    return [tuple(a_python(x) for x in r) for r in d.itertuples(index=False)]


TABLAS_NECESARIAS = [
    "oltp.departamento", "oltp.ciudad", "oltp.segmento_cliente", "oltp.canal", "oltp.metodo_pago",
    "oltp.estado_pedido", "oltp.novedad_transportadora", "oltp.cliente", "oltp.cliente_telefono",
    "oltp.bodega", "oltp.transportadora", "oltp.proveedor", "oltp.categoria", "oltp.subcategoria",
    "oltp.producto", "oltp.historial_precio", "oltp.pedido", "oltp.pedido_linea",
    "oltp.via_estado_dia", "oltp.clima_dia_ciudad",
    "dw.dim_fecha", "dw.dim_cliente", "dw.dim_producto", "dw.dim_bodega", "dw.dim_transportadora",
    "dw.dim_ciudad", "dw.dim_canal", "dw.dim_metodo_pago", "dw.dim_estado_pedido",
    "dw.dim_estado_via", "dw.dim_alerta_clima", "dw.fact_ventas_linea", "dw.fact_entregas_pedido"]


def verificar_ddl(conn):
    """Falla con un mensaje claro si falta ejecutar los scripts de las Partes 1 y 2."""
    faltan = [t for t in TABLAS_NECESARIAS if conn.exec_driver_sql(f"SELECT OBJECT_ID('{t}')").scalar() is None]
    if faltan:
        sys.exit("Faltan tablas (ejecuta primero los DDL de las Partes 1 y 2 con sqlcmd -I): " + ", ".join(faltan))
    for t in ("dw.dim_fecha", "dw.dim_estado_via", "dw.dim_alerta_clima"):    # las puebla el DDL de la Parte 2
        if conn.exec_driver_sql(f"SELECT COUNT(*) FROM {t}").scalar() == 0:
            sys.exit(f"{t} esta vacia: el script de la Parte 2 la puebla al final; ejecutalo completo.")


def stg_cargar(conn, nombre, df):
    """Crea (si falta) y recarga una tabla stg: TODO como texto, sin tocar nada."""
    cols = [c for c in df.columns if c != "fila_origen"]
    defs = ",\n  ".join(f"[{c}] NVARCHAR(500) NULL" for c in cols)
    conn.exec_driver_sql(f"IF OBJECT_ID('stg.{nombre}') IS NULL CREATE TABLE stg.{nombre} (\n"
                         f"  fila_origen INT NOT NULL,\n  {defs},\n"
                         f"  cargado_en DATETIME2 NOT NULL DEFAULT SYSDATETIME())")
    conn.exec_driver_sql(f"TRUNCATE TABLE stg.{nombre}")            # idempotencia
    lista = ", ".join(["fila_origen"] + [f"[{c}]" for c in cols])
    marcas = ", ".join("?" * (len(cols) + 1))
    conn.exec_driver_sql(f"INSERT INTO stg.{nombre} ({lista}) VALUES ({marcas})",
                         filas(df[["fila_origen"] + cols]))


def upsert(conn, df, tabla, claves):
    """MERGE generico: inserta lo nuevo, actualiza lo que cambio. Re-ejecutable."""
    cols = list(df.columns)
    tmp = "#t_" + tabla.replace(".", "_")
    lista = ", ".join(f"[{c}]" for c in cols)
    conn.exec_driver_sql(f"IF OBJECT_ID('tempdb..{tmp}') IS NOT NULL DROP TABLE {tmp}")
    conn.exec_driver_sql(f"SELECT TOP 0 {lista} INTO {tmp} FROM {tabla}")
    conn.exec_driver_sql(f"INSERT INTO {tmp} ({lista}) VALUES ({', '.join('?' * len(cols))})", filas(df))
    on = " AND ".join(f"d.[{k}] = s.[{k}]" for k in claves)
    otras = [c for c in cols if c not in claves]
    cambia = " OR ".join(f"(d.[{c}] <> s.[{c}] OR (d.[{c}] IS NULL AND s.[{c}] IS NOT NULL) "
                         f"OR (d.[{c}] IS NOT NULL AND s.[{c}] IS NULL))" for c in otras)
    upd = f"WHEN MATCHED AND ({cambia}) THEN UPDATE SET " + ", ".join(f"d.[{c}] = s.[{c}]" for c in otras) if otras else ""
    conn.exec_driver_sql(f"MERGE {tabla} AS d USING {tmp} AS s ON {on} {upd} "
                         f"WHEN NOT MATCHED THEN INSERT ({lista}) VALUES ({', '.join('s.[' + c + ']' for c in cols)});")


def ids(conn, tabla, col_clave, col_id):
    return dict(conn.exec_driver_sql(f"SELECT {col_clave}, {col_id} FROM {tabla}").fetchall())


def cargar_stg(conn, crudos, rechazos):
    conn.exec_driver_sql("IF SCHEMA_ID('stg') IS NULL EXEC('CREATE SCHEMA stg')")
    conn.exec_driver_sql("""
        IF OBJECT_ID('stg.rechazos') IS NULL CREATE TABLE stg.rechazos (
            rechazo_id  INT IDENTITY(1,1) PRIMARY KEY,
            archivo     NVARCHAR(100)  NOT NULL,
            fila_origen INT            NULL,
            llave       NVARCHAR(100)  NULL,
            regla       VARCHAR(10)    NOT NULL,
            motivo      NVARCHAR(300)  NOT NULL,
            fila_cruda  NVARCHAR(MAX)  NULL,
            rechazado_en DATETIME2     NOT NULL DEFAULT SYSDATETIME())""")
    for nombre, df in zip(["pedidos_raw", "productos", "historial_precios", "novedades_viales"], crudos):
        stg_cargar(conn, nombre, df)
    conn.exec_driver_sql("TRUNCATE TABLE stg.rechazos")             # se reconstruye en cada corrida
    if rechazos:
        r = pd.DataFrame(rechazos)[["archivo", "fila_origen", "llave", "regla", "motivo", "fila_cruda"]]
        conn.exec_driver_sql("INSERT INTO stg.rechazos (archivo, fila_origen, llave, regla, motivo, fila_cruda) "
                             "VALUES (?,?,?,?,?,?)", filas(r))


def catalogo(conn, tabla, col, valores, col_id):
    """Carga una lista de valores (catalogo pequeno) y devuelve {valor: id}."""
    upsert(conn, pd.DataFrame({col: sorted(valores)}), tabla, [col])
    return ids(conn, tabla, col, col_id)


def cargar_oltp(conn, t, novedades):
    # geografia
    dmap = catalogo(conn, "oltp.departamento", "nombre", {d for _, d in CIUDADES.values()}, "departamento_id")
    ciu = pd.DataFrame([(c, dmap[d]) for c, d in CIUDADES.values()], columns=["nombre", "departamento_id"])
    upsert(conn, ciu, "oltp.ciudad", ["nombre"])
    cmap = ids(conn, "oltp.ciudad", "nombre", "ciudad_id")
    # listas de valores
    segmap = catalogo(conn, "oltp.segmento_cliente", "nombre", set(t["cliente"].segmento), "segmento_id")
    canal_map = catalogo(conn, "oltp.canal", "nombre", set(t["pedido"].canal), "canal_id")
    pago_map = catalogo(conn, "oltp.metodo_pago", "nombre", set(t["pedido"].metodo_pago), "metodo_pago_id")
    estmap = catalogo(conn, "oltp.estado_pedido", "nombre", set(ESTADOS.values()), "estado_id")
    novmap = catalogo(conn, "oltp.novedad_transportadora", "descripcion",
                      set(t["pedido"].novedad_transportadora.dropna()), "novedad_id")
    # categoria y subcategoria
    catmap = catalogo(conn, "oltp.categoria", "nombre", set(t["producto"].categoria), "categoria_id")
    sub = t["producto"][["categoria", "subcategoria"]].drop_duplicates()
    sub = pd.DataFrame({"categoria_id": sub.categoria.map(catmap), "nombre": sub.subcategoria})
    upsert(conn, sub, "oltp.subcategoria", ["categoria_id", "nombre"])
    submap = {(c, n): i for c, n, i in conn.exec_driver_sql(
        "SELECT categoria_id, nombre, subcategoria_id FROM oltp.subcategoria").fetchall()}
    # proveedor / producto
    p = t["proveedor"].assign(ciudad_id=lambda d: d.proveedor_ciudad.map(lambda s: cmap[CIUDADES[clave_ciudad(s)][0]]))
    upsert(conn, p.rename(columns={"proveedor_nit": "nit", "proveedor_nombre": "nombre",
                                   "proveedor_telefono": "telefono"})[["nit", "nombre", "ciudad_id", "telefono"]],
           "oltp.proveedor", ["nit"])
    prov = ids(conn, "oltp.proveedor", "nit", "proveedor_id")
    pr = t["producto"].assign(
        subcategoria_id=lambda d: [submap[(catmap[c], s)] for c, s in zip(d.categoria, d.subcategoria)],
        proveedor_id=lambda d: d.proveedor_nit.map(prov).astype("Int64"))   # Int64: admite NULL sin volverse 3.0
    upsert(conn, pr[["sku", "nombre", "subcategoria_id", "unidad_medida", "peso_kg", "refrigerado",
                     "costo_unitario", "proveedor_id", "activo", "en_catalogo"]], "oltp.producto", ["sku"])
    pmap = ids(conn, "oltp.producto", "sku", "producto_id")
    hp = t["precio_lista"].assign(producto_id=lambda d: d.sku.map(pmap))
    hp["vigente_hasta"] = hp.vigente_hasta.where(hp.vigente_hasta != FIN_DE_LOS_TIEMPOS, pd.NaT)   # NULL = vigente hoy
    upsert(conn, hp[["producto_id", "vigente_desde", "vigente_hasta", "precio_lista"]], "oltp.historial_precio",
           ["producto_id", "vigente_desde"])
    # bodega / transportadora
    b = t["bodega"].assign(ciudad_id=lambda d: d.bodega_ciudad.map(cmap)).rename(
        columns={"bodega_codigo": "codigo", "bodega_nombre": "nombre"})
    upsert(conn, b[["codigo", "nombre", "ciudad_id"]], "oltp.bodega", ["codigo"])
    upsert(conn, t["transportadora"][["nit", "nombre"]], "oltp.transportadora", ["nit"])
    bmap = ids(conn, "oltp.bodega", "codigo", "bodega_id")
    tmap = ids(conn, "oltp.transportadora", "nit", "transportadora_id")
    # clientes
    c = t["cliente"].assign(ciudad_id=lambda d: d.ciudad.map(cmap), segmento_id=lambda d: d.segmento.map(segmap))
    upsert(conn, c[["documento", "nombre", "email", "segmento_id", "direccion", "ciudad_id"]], "oltp.cliente", ["documento"])
    clmap = ids(conn, "oltp.cliente", "documento", "cliente_id")
    tel = t["cliente_telefono"].assign(cliente_id=lambda d: d.documento.map(clmap))
    upsert(conn, tel[["cliente_id", "telefono", "es_principal"]], "oltp.cliente_telefono", ["cliente_id", "telefono"])
    # pedidos y lineas (venta_neta de la linea es columna calculada: la calcula SQL Server, no se inserta)
    ped = t["pedido"].assign(canal_id=lambda d: d.canal.map(canal_map), cliente_id=lambda d: d.documento.map(clmap),
                             ciudad_entrega_id=lambda d: d.ciudad_entrega.map(cmap),
                             bodega_id=lambda d: d.bodega_codigo.map(bmap),
                             transportadora_id=lambda d: d.transportadora_nit.map(tmap),
                             metodo_pago_id=lambda d: d.metodo_pago.map(pago_map),
                             estado_id=lambda d: d.estado_pedido.map(estmap),
                             novedad_id=lambda d: d.novedad_transportadora.map(novmap).astype("Int64")).rename(
        columns={"id_pedido": "codigo_pedido"})
    upsert(conn, ped[["codigo_pedido", "cliente_id", "canal_id", "metodo_pago_id", "bodega_id", "transportadora_id",
                      "estado_id", "ciudad_entrega_id", "direccion_entrega", "fecha_pedido", "franja_entrega",
                      "distancia_km", "fecha_entrega_prometida", "fecha_entrega_real", "calificacion_servicio",
                      "reclamo", "novedad_id"]], "oltp.pedido", ["codigo_pedido"])
    pedmap = ids(conn, "oltp.pedido", "codigo_pedido", "pedido_id")
    ln = t["linea"].assign(pedido_id=lambda d: d.id_pedido.map(pedmap), producto_id=lambda d: d.sku.map(pmap))
    upsert(conn, ln[["pedido_id", "linea", "producto_id", "cantidad", "precio_unitario", "descuento"]],
           "oltp.pedido_linea", ["pedido_id", "linea"])
    # via y clima
    nv = novedades.assign(fecha=lambda d: pd.to_datetime(d.fecha))
    via = nv.drop_duplicates("fecha")[["fecha", "estado_via", "horas_restriccion"]].copy()
    via["horas_restriccion"] = via.horas_restriccion.astype(float).astype(int)
    upsert(conn, via, "oltp.via_estado_dia", ["fecha"])
    cl = nv.assign(ciudad_id=lambda d: d.ciudad.map(lambda s: cmap[CIUDADES[clave_ciudad(s)][0]]),
                   lluvia_mm=lambda d: d.lluvia_mm.astype(float),
                   alerta=lambda d: d.alerta.replace("", None))
    upsert(conn, cl[["fecha", "ciudad_id", "lluvia_mm", "alerta"]], "oltp.clima_dia_ciudad", ["fecha", "ciudad_id"])


# Las dimensiones se alimentan desde oltp con MERGE por la llave de negocio (*_id): las llaves sustitutas
# (*_key) existentes se conservan. Los hechos se reconstruyen completos desde oltp.
SQL_DW = {
    "dim_bodega": """
        MERGE dw.dim_bodega AS d
        USING (SELECT b.bodega_id, b.codigo, b.nombre, c.nombre AS ciudad, dp.nombre AS departamento
               FROM oltp.bodega b JOIN oltp.ciudad c ON c.ciudad_id = b.ciudad_id
               JOIN oltp.departamento dp ON dp.departamento_id = c.departamento_id) AS s ON d.bodega_id = s.bodega_id
        WHEN MATCHED THEN UPDATE SET d.codigo = s.codigo, d.nombre = s.nombre, d.ciudad = s.ciudad,
             d.departamento = s.departamento
        WHEN NOT MATCHED THEN INSERT (bodega_id, codigo, nombre, ciudad, departamento)
             VALUES (s.bodega_id, s.codigo, s.nombre, s.ciudad, s.departamento);""",
    "dim_transportadora": """
        MERGE dw.dim_transportadora AS d
        USING (SELECT transportadora_id, nombre, nit,
                      CASE WHEN nombre LIKE N'%Flota Propia%' THEN 1 ELSE 0 END AS es_flota_propia
               FROM oltp.transportadora) AS s ON d.transportadora_id = s.transportadora_id
        WHEN MATCHED THEN UPDATE SET d.nombre = s.nombre, d.nit = s.nit, d.es_flota_propia = s.es_flota_propia
        WHEN NOT MATCHED THEN INSERT (transportadora_id, nombre, nit, es_flota_propia)
             VALUES (s.transportadora_id, s.nombre, s.nit, s.es_flota_propia);""",
    "dim_ciudad": """
        MERGE dw.dim_ciudad AS d
        USING (SELECT c.ciudad_id, c.nombre AS ciudad, dp.nombre AS departamento
               FROM oltp.ciudad c JOIN oltp.departamento dp ON dp.departamento_id = c.departamento_id) AS s
              ON d.ciudad_id = s.ciudad_id
        WHEN MATCHED THEN UPDATE SET d.ciudad = s.ciudad, d.departamento = s.departamento
        WHEN NOT MATCHED THEN INSERT (ciudad_id, ciudad, departamento) VALUES (s.ciudad_id, s.ciudad, s.departamento);""",
    "dim_canal": """
        MERGE dw.dim_canal AS d USING oltp.canal AS s ON d.canal_id = s.canal_id
        WHEN MATCHED THEN UPDATE SET d.canal = s.nombre
        WHEN NOT MATCHED THEN INSERT (canal_id, canal) VALUES (s.canal_id, s.nombre);""",
    "dim_metodo_pago": """
        MERGE dw.dim_metodo_pago AS d USING oltp.metodo_pago AS s ON d.metodo_pago_id = s.metodo_pago_id
        WHEN MATCHED THEN UPDATE SET d.metodo_pago = s.nombre
        WHEN NOT MATCHED THEN INSERT (metodo_pago_id, metodo_pago) VALUES (s.metodo_pago_id, s.nombre);""",
    "dim_estado_pedido": """
        MERGE dw.dim_estado_pedido AS d
        USING (SELECT estado_id, nombre AS estado, CASE WHEN nombre = N'Entregado' THEN 1 ELSE 0 END AS cuenta_como_venta
               FROM oltp.estado_pedido) AS s ON d.estado_id = s.estado_id
        WHEN MATCHED THEN UPDATE SET d.estado = s.estado, d.cuenta_como_venta = s.cuenta_como_venta
        WHEN NOT MATCHED THEN INSERT (estado_id, estado, cuenta_como_venta)
             VALUES (s.estado_id, s.estado, s.cuenta_como_venta);""",
    # SCD2 de producto: una version por periodo de historial_precio (precio de lista); la primera arranca
    # en 1900-01-01 y la ultima queda abierta (9999-12-31, es_actual = 1). Los SKU sin historial tienen 1 version.
    "dim_producto": """
        MERGE dw.dim_producto AS d
        USING (
            SELECT p.producto_id, p.sku, p.nombre AS nombre_producto, c.nombre AS categoria, sc.nombre AS subcategoria,
                   p.unidad_medida, p.peso_kg, p.refrigerado, pr.nit AS proveedor_nit, pr.nombre AS proveedor_nombre,
                   pc.nombre AS proveedor_ciudad, p.costo_unitario, h.precio_lista, p.activo, p.en_catalogo,
                   CASE WHEN h.producto_id IS NULL OR h.rn_asc = 1 THEN CAST('19000101' AS DATE) ELSE h.vigente_desde END AS fecha_inicio,
                   CASE WHEN h.producto_id IS NULL OR h.rn_desc = 1 THEN CAST('99991231' AS DATE) ELSE h.vigente_hasta END AS fecha_fin,
                   CASE WHEN h.producto_id IS NULL OR h.rn_desc = 1 THEN 1 ELSE 0 END AS es_actual
            FROM oltp.producto p
            JOIN oltp.subcategoria sc ON sc.subcategoria_id = p.subcategoria_id
            JOIN oltp.categoria c ON c.categoria_id = sc.categoria_id
            LEFT JOIN oltp.proveedor pr ON pr.proveedor_id = p.proveedor_id
            LEFT JOIN oltp.ciudad pc ON pc.ciudad_id = pr.ciudad_id
            LEFT JOIN (SELECT producto_id, vigente_desde, vigente_hasta, precio_lista,
                              ROW_NUMBER() OVER (PARTITION BY producto_id ORDER BY vigente_desde)      AS rn_asc,
                              ROW_NUMBER() OVER (PARTITION BY producto_id ORDER BY vigente_desde DESC) AS rn_desc
                       FROM oltp.historial_precio) h ON h.producto_id = p.producto_id
        ) AS s ON d.producto_id = s.producto_id AND d.fecha_inicio = s.fecha_inicio
        WHEN MATCHED THEN UPDATE SET d.sku = s.sku, d.nombre_producto = s.nombre_producto, d.categoria = s.categoria,
             d.subcategoria = s.subcategoria, d.unidad_medida = s.unidad_medida, d.peso_kg = s.peso_kg,
             d.refrigerado = s.refrigerado, d.proveedor_nit = s.proveedor_nit, d.proveedor_nombre = s.proveedor_nombre,
             d.proveedor_ciudad = s.proveedor_ciudad, d.costo_unitario = s.costo_unitario, d.precio_lista = s.precio_lista,
             d.activo = s.activo, d.en_catalogo = s.en_catalogo, d.fecha_fin = s.fecha_fin, d.es_actual = s.es_actual
        WHEN NOT MATCHED THEN INSERT (producto_id, sku, nombre_producto, categoria, subcategoria, unidad_medida, peso_kg,
             refrigerado, proveedor_nit, proveedor_nombre, proveedor_ciudad, costo_unitario, precio_lista, activo,
             en_catalogo, fecha_inicio, fecha_fin, es_actual)
             VALUES (s.producto_id, s.sku, s.nombre_producto, s.categoria, s.subcategoria, s.unidad_medida, s.peso_kg,
             s.refrigerado, s.proveedor_nit, s.proveedor_nombre, s.proveedor_ciudad, s.costo_unitario, s.precio_lista,
             s.activo, s.en_catalogo, s.fecha_inicio, s.fecha_fin, s.es_actual);""",
    "limpiar_hechos": "DELETE FROM dw.fact_entregas_pedido; DELETE FROM dw.fact_ventas_linea;",
    "fact_ventas_linea": """
        INSERT INTO dw.fact_ventas_linea (codigo_pedido, linea, fecha_key, cliente_key, producto_key, bodega_key,
               transportadora_key, canal_key, metodo_pago_key, estado_pedido_key, ciudad_entrega_key,
               cantidad, precio_unitario, descuento_pct, venta_bruta, descuento_valor, venta_neta,
               costo_total, es_costeable)
        SELECT p.codigo_pedido, l.linea, CONVERT(INT, CONVERT(CHAR(8), p.fecha_pedido, 112)),
               dc.cliente_key, dp.producto_key, db.bodega_key, dt.transportadora_key, dca.canal_key,
               dm.metodo_pago_key, de.estado_pedido_key, dci.ciudad_key,
               l.cantidad, l.precio_unitario, l.descuento,
               CAST(l.precio_unitario * l.cantidad AS DECIMAL(14,2)),
               CAST(l.precio_unitario * l.cantidad AS DECIMAL(14,2)) - l.venta_neta,
               l.venta_neta,
               CASE WHEN dp.costo_unitario IS NOT NULL THEN CAST(l.cantidad * dp.costo_unitario AS DECIMAL(14,2)) END,
               CASE WHEN dp.costo_unitario IS NOT NULL THEN 1 ELSE 0 END
        FROM oltp.pedido_linea l
        JOIN oltp.pedido p              ON p.pedido_id = l.pedido_id
        JOIN dw.dim_cliente dc          ON dc.cliente_id = p.cliente_id
             AND CAST(p.fecha_pedido AS DATE) BETWEEN dc.fecha_inicio AND dc.fecha_fin
        JOIN dw.dim_producto dp         ON dp.producto_id = l.producto_id
             AND CAST(p.fecha_pedido AS DATE) BETWEEN dp.fecha_inicio AND dp.fecha_fin
        JOIN dw.dim_bodega db           ON db.bodega_id = p.bodega_id
        JOIN dw.dim_transportadora dt   ON dt.transportadora_id = p.transportadora_id
        JOIN dw.dim_canal dca           ON dca.canal_id = p.canal_id
        JOIN dw.dim_metodo_pago dm      ON dm.metodo_pago_id = p.metodo_pago_id
        JOIN dw.dim_estado_pedido de    ON de.estado_id = p.estado_id
        JOIN dw.dim_ciudad dci          ON dci.ciudad_id = p.ciudad_entrega_id;""",
    "fact_entregas_pedido": """
        INSERT INTO dw.fact_entregas_pedido (codigo_pedido, fecha_pedido_key, fecha_prometida_key, fecha_entrega_key,
               cliente_key, bodega_key, transportadora_key, ciudad_entrega_key, canal_key, metodo_pago_key,
               estado_pedido_key, estado_via_key, alerta_clima_key, franja_entrega,
               num_lineas, unidades_totales, peso_total_kg, venta_neta_pedido, distancia_km, dias_retraso,
               horas_hasta_entrega, entregado_a_tiempo, cruza_via, lluvia_destino_mm, horas_restriccion_via,
               calificacion_servicio, reclamo)
        SELECT p.codigo_pedido,
               CONVERT(INT, CONVERT(CHAR(8), p.fecha_pedido, 112)),
               CONVERT(INT, CONVERT(CHAR(8), p.fecha_entrega_prometida, 112)),
               CONVERT(INT, CONVERT(CHAR(8), p.fecha_entrega_real, 112)),            -- NULL si no se entrego
               dc.cliente_key, db.bodega_key, dt.transportadora_key, dci.ciudad_key, dca.canal_key,
               dm.metodo_pago_key, de.estado_pedido_key, dev.estado_via_key, dal.alerta_clima_key, p.franja_entrega,
               x.num_lineas, x.unidades, CAST(x.peso AS DECIMAL(12,3)), x.venta_neta, p.distancia_km,
               DATEDIFF(DAY, p.fecha_entrega_prometida, CAST(p.fecha_entrega_real AS DATE)),
               CAST(DATEDIFF(MINUTE, p.fecha_pedido, p.fecha_entrega_real) / 60.0 AS DECIMAL(8,1)),
               CASE WHEN p.fecha_entrega_real IS NULL THEN NULL
                    WHEN CAST(p.fecha_entrega_real AS DATE) <= p.fecha_entrega_prometida THEN 1 ELSE 0 END,
               CASE WHEN (db.ciudad = N'Bogotá' AND dci.departamento = N'Meta')
                      OR (db.ciudad = N'Villavicencio' AND dci.departamento IN (N'Bogotá D.C.', N'Cundinamarca'))
                    THEN 1 ELSE 0 END,
               cl.lluvia_mm, v.horas_restriccion, p.calificacion_servicio, p.reclamo
        FROM oltp.pedido p
        JOIN dw.dim_cliente dc        ON dc.cliente_id = p.cliente_id
             AND CAST(p.fecha_pedido AS DATE) BETWEEN dc.fecha_inicio AND dc.fecha_fin
        JOIN dw.dim_bodega db         ON db.bodega_id = p.bodega_id
        JOIN dw.dim_transportadora dt ON dt.transportadora_id = p.transportadora_id
        JOIN dw.dim_ciudad dci        ON dci.ciudad_id = p.ciudad_entrega_id
        JOIN dw.dim_canal dca         ON dca.canal_id = p.canal_id
        JOIN dw.dim_metodo_pago dm    ON dm.metodo_pago_id = p.metodo_pago_id
        JOIN dw.dim_estado_pedido de  ON de.estado_id = p.estado_id
        JOIN oltp.via_estado_dia v    ON v.fecha = CAST(p.fecha_pedido AS DATE)
        JOIN dw.dim_estado_via dev    ON dev.estado_via = v.estado_via
        LEFT JOIN oltp.clima_dia_ciudad cl ON cl.fecha = CAST(p.fecha_pedido AS DATE) AND cl.ciudad_id = p.ciudad_entrega_id
        JOIN dw.dim_alerta_clima dal  ON dal.alerta = ISNULL(cl.alerta, N'Sin alerta')
        CROSS APPLY (SELECT COUNT(*) AS num_lineas, SUM(l.cantidad) AS unidades,
                            SUM(l.cantidad * pr.peso_kg) AS peso, SUM(l.venta_neta) AS venta_neta
                     FROM oltp.pedido_linea l JOIN oltp.producto pr ON pr.producto_id = l.producto_id
                     WHERE l.pedido_id = p.pedido_id) x
        WHERE x.num_lineas > 0;""",
}


def cargar_dw(conn, t):
    # dim_fecha, dim_estado_via y dim_alerta_clima ya vienen pobladas por el DDL de la Parte 2
    for k in ("dim_bodega", "dim_transportadora", "dim_ciudad", "dim_canal", "dim_metodo_pago",
              "dim_estado_pedido", "dim_producto"):
        conn.exec_driver_sql(SQL_DW[k])
    # SCD2 de cliente: la ciudad/direccion cambia en el tiempo para algunos clientes
    clmap = ids(conn, "oltp.cliente", "documento", "cliente_id")
    h = t["cliente_hist"].copy()
    h["cliente_id"] = h.documento.map(clmap)
    h["fecha_inicio"] = h.fecha_inicio.dt.date
    h["fecha_fin"] = [date(9999, 12, 31) if f == FIN_DE_LOS_TIEMPOS else f.date() for f in h.fecha_fin]
    upsert(conn, h[["cliente_id", "documento", "nombre", "segmento", "direccion", "ciudad", "departamento",
                    "fecha_inicio", "fecha_fin", "es_actual"]], "dw.dim_cliente", ["cliente_id", "fecha_inicio"])
    conn.exec_driver_sql(SQL_DW["limpiar_hechos"])                   # hechos: se reconstruyen
    conn.exec_driver_sql(SQL_DW["fact_ventas_linea"])
    conn.exec_driver_sql(SQL_DW["fact_entregas_pedido"])


# --------------------------------------------------------------------------
# 5. CONCILIACION
# --------------------------------------------------------------------------
def conciliar(conn, leidas, rechazos, limpio, t):
    esperado = float(limpio[limpio.estado_c == "Entregado"].venta_neta.sum())
    rep = {"leidas_csv": leidas["pedidos"], "rechazadas_csv": sum(1 for r in rechazos if r["archivo"] == "pedidos_raw.csv"),
           "candidatas_oltp": len(limpio), "pedidos_candidatos": limpio.id_pedido.nunique(),
           "venta_neta_entregada_pandas": round(esperado, 2)}
    if conn is not None:
        q = conn.exec_driver_sql
        rep["filas_stg_pedidos"] = q("SELECT COUNT(*) FROM stg.pedidos_raw").scalar()
        rep["filas_stg_rechazos"] = q("SELECT COUNT(*) FROM stg.rechazos WHERE archivo='pedidos_raw.csv'").scalar()
        rep["lineas_oltp"] = q("SELECT COUNT(*) FROM oltp.pedido_linea").scalar()
        rep["lineas_dw"] = q("SELECT COUNT(*) FROM dw.fact_ventas_linea").scalar()
        rep["pedidos_oltp"] = q("SELECT COUNT(*) FROM oltp.pedido").scalar()
        rep["pedidos_dw"] = q("SELECT COUNT(*) FROM dw.fact_entregas_pedido").scalar()
        rep["venta_neta_entregada_oltp"] = float(q(
            "SELECT SUM(l.venta_neta) FROM oltp.pedido_linea l JOIN oltp.pedido p ON p.pedido_id = l.pedido_id "
            "JOIN oltp.estado_pedido e ON e.estado_id = p.estado_id WHERE e.nombre = N'Entregado'").scalar())
        rep["venta_neta_entregada_dw"] = float(q(
            "SELECT SUM(f.venta_neta) FROM dw.fact_ventas_linea f "
            "JOIN dw.dim_estado_pedido e ON e.estado_pedido_key = f.estado_pedido_key WHERE e.estado = N'Entregado'").scalar())
        rep["venta_neta_entregas_dw"] = float(q(
            "SELECT SUM(f.venta_neta_pedido) FROM dw.fact_entregas_pedido f "
            "JOIN dw.dim_estado_pedido e ON e.estado_pedido_key = f.estado_pedido_key WHERE e.estado = N'Entregado'").scalar())
    ok = rep["leidas_csv"] == rep["rechazadas_csv"] + rep["candidatas_oltp"]
    if conn is not None:
        ok = (ok and rep["filas_stg_pedidos"] == rep["leidas_csv"]
              and rep["filas_stg_rechazos"] == rep["rechazadas_csv"]
              and rep["lineas_oltp"] == rep["candidatas_oltp"] == rep["lineas_dw"]
              and rep["pedidos_oltp"] == rep["pedidos_candidatos"] == rep["pedidos_dw"]
              and abs(rep["venta_neta_entregada_oltp"] - esperado) < 0.01
              and abs(rep["venta_neta_entregada_dw"] - esperado) < 0.01
              and abs(rep["venta_neta_entregas_dw"] - esperado) < 0.01)
    rep["cuadra"] = bool(ok)
    return rep


# --------------------------------------------------------------------------
# 6. PROGRAMA PRINCIPAL
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datos", default=str(BASE.parent / "datos_crudos"))
    ap.add_argument("--server", default=r".\SQLEXPRESS")
    ap.add_argument("--db", default="MercaLlano")
    ap.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    ap.add_argument("--lento", action="store_true", help="desactiva fast_executemany (si pyodbc se queja de tipos)")
    ap.add_argument("--dry-run", action="store_true", help="solo leer y limpiar; no toca SQL Server")
    a = ap.parse_args()

    print("1/6 Leyendo archivos crudos ...")
    pedidos, productos, precios, novedades = leer_crudos(Path(a.datos))
    leidas = {"pedidos": len(pedidos), "productos": len(productos), "precios": len(precios), "novedades": len(novedades)}

    print("2/6 Limpiando ...")
    R = Reglas()
    cat, hist, rech_cat = limpiar_catalogo(productos, precios, R)
    limpio, rech_ped = limpiar_pedidos(pedidos, cat, hist, R)
    rechazos = rech_cat + rech_ped
    t = tablas_oltp(limpio, cat, hist)
    t["precio_lista"] = hist
    pd.DataFrame(R.filas).to_csv(BASE / "reglas_calidad_conteos.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(rechazos).to_csv(BASE / "rechazos.csv", index=False, encoding="utf-8-sig")

    conn = None
    if not a.dry_run:
        eng = conectar(a.server, a.db, a.driver, rapido=not a.lento)
        with eng.begin() as conn:                                     # 1 sola transaccion: todo o nada
            conn.exec_driver_sql("IF SCHEMA_ID('stg') IS NULL EXEC('CREATE SCHEMA stg')")
            verificar_ddl(conn)
            print("3/6 Cargando stg (tal cual) ...")
            cargar_stg(conn, (pedidos, productos, precios, novedades), rechazos)
            print("4/6 Cargando oltp ...")
            cargar_oltp(conn, t, novedades)
            print("5/6 Cargando dw ...")
            cargar_dw(conn, t)
            print("6/6 Conciliando ...")
            rep = conciliar(conn, leidas, rechazos, limpio, t)
    else:
        print("3-5/6 (dry-run) se omiten stg, oltp y dw")
        rep = conciliar(None, leidas, rechazos, limpio, t)

    print(pd.DataFrame(R.filas).to_string(index=False))
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    (BASE / "conciliacion.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.exit(0 if rep["cuadra"] else 1)


if __name__ == "__main__":
    main()
