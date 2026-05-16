"""
Dashboard Analitico de Fondos de Inversion - Tesis Financiera
=============================================================

Aplicacion Streamlit para visualizar el analisis de Desempeno vs Costo
de fondos de inversion mexicanos extraidos del portal CNBV/STIV.

Funcionalidades:
    - Scatter Plot: TER (Gastos Totales) vs Rendimiento 12 meses.
    - Identificacion visual de fondos "caros y malos" vs "baratos y eficientes".
    - Tabla comparativa de comisiones por institucion.
    - Analisis de rendimientos por periodo.
    - Resumen general de la base de datos.

Ejecucion:
    streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import os
import sys

# Agregar el directorio raiz al path para imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.database.db_manager import DatabaseManager


# ---------------------------------------------------------------------------
# Configuracion de pagina
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Analisis Fondos CNBV - Tesis Financiera",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def parse_percentage_to_float(value: str) -> float:
    """Convierte un valor porcentual string a float.

    Acepta: '1.50%', '1.50', '-0.32%', None
    Retorna: float o NaN
    """
    if not value or value in ("N/A", "N/D", "None"):
        return np.nan
    try:
        cleaned = str(value).strip().replace("%", "").replace(",", ".")
        return float(cleaned)
    except (ValueError, TypeError):
        return np.nan


@st.cache_resource
def get_db_manager():
    """Inicializa el gestor de base de datos (cacheado)."""
    db_path = os.environ.get("DB_PATH", "data/tesis_prospectos.db")
    return DatabaseManager(db_path=db_path)


@st.cache_data(ttl=60)
def load_performance_data():
    """Carga datos de performance vs costo desde la DB."""
    db = get_db_manager()
    return db.get_performance_vs_cost_data()


@st.cache_data(ttl=60)
def load_comisiones_data():
    """Carga resumen de comisiones."""
    db = get_db_manager()
    return db.get_comisiones_summary()


@st.cache_data(ttl=60)
def load_rendimientos_data():
    """Carga todos los rendimientos."""
    db = get_db_manager()
    return db.get_all_rendimientos()


@st.cache_data(ttl=60)
def load_db_summary():
    """Carga resumen general de la DB."""
    db = get_db_manager()
    return db.get_database_summary()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("📊 Panel de Control")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navegacion",
    [
        "🏠 Resumen General",
        "📈 Desempeno vs Costo",
        "💰 Analisis de Comisiones",
        "📉 Rendimientos Historicos",
    ],
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Tesis**: Pipeline de Inteligencia Financiera\n\n"
    "**Fuente**: CNBV / STIV / HSBC"
)


# ---------------------------------------------------------------------------
# Pagina: Resumen General
# ---------------------------------------------------------------------------
if page == "🏠 Resumen General":
    st.title("🏠 Resumen General del Pipeline")
    st.markdown(
        "Vista de alto nivel de los datos recolectados y procesados "
        "por el pipeline de extraccion."
    )

    summary = load_db_summary()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Instituciones", summary.get("instituciones", 0))
    col2.metric("Fondos", summary.get("fondos", 0))
    col3.metric("Documentos", summary.get("documentos", 0))
    col4.metric("Rendimientos Registrados", summary.get("rendimientos_historicos", 0))

    st.markdown("---")

    col5, col6 = st.columns(2)
    col5.metric("Series", summary.get("series", 0))
    col6.metric("Metricas Extraidas", summary.get("metricas_prospecto", 0))

    st.markdown("---")
    st.subheader("Estado de la Base de Datos")

    if summary.get("documentos", 0) == 0:
        st.warning(
            "⚠️ La base de datos esta vacia. Ejecuta `python main.py` para "
            "iniciar el pipeline de extraccion, o carga datos de prueba."
        )
        st.markdown(
            """
            ### Inicio Rapido
            ```bash
            # 1. Instalar dependencias
            pip install -r requirements.txt

            # 2. Ejecutar pipeline de extraccion
            python main.py

            # 3. Lanzar dashboard
            streamlit run app.py
            ```
            """
        )
    else:
        st.success(
            f"✅ Base de datos activa con {summary.get('documentos', 0)} documentos "
            f"de {summary.get('instituciones', 0)} instituciones."
        )

        # Mostrar tabla de comisiones disponibles
        comisiones_data = load_comisiones_data()
        if comisiones_data:
            df = pd.DataFrame(comisiones_data)
            st.subheader("Muestra de Datos Disponibles")
            st.dataframe(
                df.head(10),
                use_container_width=True,
                hide_index=True,
            )


# ---------------------------------------------------------------------------
# Pagina: Desempeno vs Costo (SCATTER PLOT PRINCIPAL)
# ---------------------------------------------------------------------------
elif page == "📈 Desempeno vs Costo":
    st.title("📈 Analisis de Desempeno vs Costo")
    st.markdown(
        """
        **Objetivo**: Identificar fondos que ofrecen alto rendimiento con bajas comisiones
        ("eficientes") vs fondos caros con bajo rendimiento ("ineficientes").

        - **Eje X**: TER (Gastos Totales) — A menor valor, mas barato el fondo.
        - **Eje Y**: Rendimiento Neto 12 meses — A mayor valor, mejor desempeno.
        - **Cuadrante superior-izquierdo**: Fondos EFICIENTES (baratos y buenos).
        - **Cuadrante inferior-derecho**: Fondos INEFICIENTES (caros y malos).
        """
    )

    raw_data = load_performance_data()

    if not raw_data:
        st.warning(
            "⚠️ No hay datos disponibles para el scatter plot. "
            "Asegurate de que el pipeline haya procesado documentos con "
            "informacion de comisiones y rendimientos."
        )
        st.info(
            "💡 Para generar datos de prueba, ejecuta:\n"
            "```python\npython tests/test_extractor.py\n```"
        )
    else:
        df = pd.DataFrame(raw_data)

        # Convertir porcentajes a valores numericos
        df["ter_num"] = df["gastos_totales_ter"].apply(parse_percentage_to_float)
        df["rend_12m_num"] = df["rendimiento_12m"].apply(parse_percentage_to_float)
        df["bench_12m_num"] = df["benchmark_12m"].apply(parse_percentage_to_float)
        df["comision_admin_num"] = df["comision_administracion_anual"].apply(
            parse_percentage_to_float)

        # Filtrar filas con datos validos para el scatter
        df_valid = df.dropna(subset=["ter_num", "rend_12m_num"])

        # --- Filtros en sidebar ---
        st.sidebar.markdown("### Filtros")

        if "categoria" in df_valid.columns:
            categorias = ["Todas"] + sorted(
                df_valid["categoria"].dropna().unique().tolist())
            sel_categoria = st.sidebar.selectbox("Categoria", categorias)
            if sel_categoria != "Todas":
                df_valid = df_valid[df_valid["categoria"] == sel_categoria]

        if "institucion" in df_valid.columns:
            instituciones = ["Todas"] + sorted(
                df_valid["institucion"].dropna().unique().tolist())
            sel_inst = st.sidebar.selectbox("Institucion", instituciones)
            if sel_inst != "Todas":
                df_valid = df_valid[df_valid["institucion"] == sel_inst]

        if len(df_valid) == 0:
            st.warning("No hay datos que cumplan los filtros seleccionados.")
        else:
            # --- Scatter Plot Principal ---
            fig = px.scatter(
                df_valid,
                x="ter_num",
                y="rend_12m_num",
                color="categoria" if "categoria" in df_valid.columns else None,
                symbol="institucion" if "institucion" in df_valid.columns else None,
                hover_name="clave_pizarra",
                hover_data={
                    "serie_accionaria": True,
                    "ter_num": ":.2f",
                    "rend_12m_num": ":.2f",
                    "tipo_administracion": True,
                    "gastos_totales_ter": True,
                    "rendimiento_12m": True,
                },
                title="TER (Gastos Totales) vs Rendimiento Neto 12 Meses",
                labels={
                    "ter_num": "TER - Gastos Totales (%)",
                    "rend_12m_num": "Rendimiento Neto 12M (%)",
                    "categoria": "Categoria",
                    "institucion": "Institucion",
                },
                template="plotly_white",
            )

            # Agregar lineas de referencia (medianas)
            if len(df_valid) > 1:
                median_ter = df_valid["ter_num"].median()
                median_rend = df_valid["rend_12m_num"].median()

                fig.add_hline(
                    y=median_rend,
                    line_dash="dash",
                    line_color="gray",
                    opacity=0.5,
                    annotation_text=f"Mediana Rend: {median_rend:.2f}%",
                )
                fig.add_vline(
                    x=median_ter,
                    line_dash="dash",
                    line_color="gray",
                    opacity=0.5,
                    annotation_text=f"Mediana TER: {median_ter:.2f}%",
                )

            fig.update_layout(
                height=600,
                margin=dict(l=50, r=50, t=80, b=50),
            )
            fig.update_traces(marker=dict(size=12, opacity=0.8))

            st.plotly_chart(fig, use_container_width=True)

            # --- Metricas resumen ---
            st.markdown("---")
            st.subheader("Estadisticas del Universo")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Fondos Analizados", len(df_valid))
            col2.metric("TER Promedio", f"{df_valid['ter_num'].mean():.2f}%")
            col3.metric("Rend. 12M Promedio", f"{df_valid['rend_12m_num'].mean():.2f}%")
            col4.metric(
                "Ratio Rend/TER (Mediana)",
                f"{(df_valid['rend_12m_num'] / df_valid['ter_num'].replace(0, np.nan)).median():.2f}x"
                if df_valid['ter_num'].replace(0, np.nan).notna().any()
                else "N/D"
            )

            # --- Tabla de datos ---
            st.markdown("---")
            st.subheader("Detalle de Fondos")
            display_cols = [
                "clave_pizarra", "serie_accionaria", "institucion",
                "categoria", "tipo_administracion",
                "gastos_totales_ter", "rendimiento_12m", "benchmark_12m",
            ]
            available_cols = [c for c in display_cols if c in df_valid.columns]
            st.dataframe(
                df_valid[available_cols].sort_values("ter_num", ascending=True),
                use_container_width=True,
                hide_index=True,
            )


# ---------------------------------------------------------------------------
# Pagina: Analisis de Comisiones
# ---------------------------------------------------------------------------
elif page == "💰 Analisis de Comisiones":
    st.title("💰 Analisis Comparativo de Comisiones")
    st.markdown(
        "Comparacion de la estructura de costos entre fondos de inversion."
    )

    comisiones_data = load_comisiones_data()

    if not comisiones_data:
        st.warning("⚠️ No hay datos de comisiones disponibles.")
    else:
        df = pd.DataFrame(comisiones_data)

        # Convertir a numerico
        df["ter_num"] = df["gastos_totales_ter"].apply(parse_percentage_to_float)
        df["admin_num"] = df["comision_administracion_anual"].apply(
            parse_percentage_to_float)
        df["desemp_num"] = df["comision_desempeno"].apply(parse_percentage_to_float)

        # --- Distribucion de TER ---
        st.subheader("Distribucion de Gastos Totales (TER)")
        df_ter = df.dropna(subset=["ter_num"])

        if len(df_ter) > 0:
            fig_hist = px.histogram(
                df_ter,
                x="ter_num",
                color="categoria" if "categoria" in df_ter.columns else None,
                nbins=20,
                title="Distribucion del TER por Categoria",
                labels={"ter_num": "TER (%)"},
                template="plotly_white",
            )
            st.plotly_chart(fig_hist, use_container_width=True)

        # --- Comparacion por institucion ---
        st.subheader("TER Promedio por Institucion")
        df_inst = df.dropna(subset=["ter_num"]).groupby("institucion").agg(
            ter_promedio=("ter_num", "mean"),
            ter_mediana=("ter_num", "median"),
            num_fondos=("ter_num", "count"),
        ).reset_index()

        if len(df_inst) > 0:
            fig_bar = px.bar(
                df_inst,
                x="institucion",
                y="ter_promedio",
                color="institucion",
                text="num_fondos",
                title="TER Promedio por Institucion (# fondos en etiqueta)",
                labels={"ter_promedio": "TER Promedio (%)", "institucion": ""},
                template="plotly_white",
            )
            fig_bar.update_traces(textposition="outside")
            st.plotly_chart(fig_bar, use_container_width=True)

        # --- Box plot de comisiones ---
        st.subheader("Comparacion de Comisiones")
        col1, col2 = st.columns(2)

        with col1:
            df_admin = df.dropna(subset=["admin_num"])
            if len(df_admin) > 0:
                fig_box = px.box(
                    df_admin,
                    y="admin_num",
                    x="categoria" if "categoria" in df_admin.columns else None,
                    title="Comision por Administracion Anual",
                    labels={"admin_num": "Comision Admin (%)"},
                    template="plotly_white",
                )
                st.plotly_chart(fig_box, use_container_width=True)

        with col2:
            df_desemp = df.dropna(subset=["desemp_num"])
            if len(df_desemp) > 0:
                fig_box2 = px.box(
                    df_desemp,
                    y="desemp_num",
                    x="categoria" if "categoria" in df_desemp.columns else None,
                    title="Comision por Desempeno",
                    labels={"desemp_num": "Comision Desempeno (%)"},
                    template="plotly_white",
                )
                st.plotly_chart(fig_box2, use_container_width=True)

        # --- Tabla completa ---
        st.markdown("---")
        st.subheader("Tabla Completa de Comisiones")
        st.dataframe(
            df[[
                "clave_pizarra", "serie_accionaria", "institucion",
                "categoria", "tipo_administracion",
                "comision_administracion_anual", "comision_desempeno",
                "gastos_totales_ter", "var_maximo_autorizado",
            ]].sort_values("gastos_totales_ter", ascending=True, na_position="last"),
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------------------------
# Pagina: Rendimientos Historicos
# ---------------------------------------------------------------------------
elif page == "📉 Rendimientos Historicos":
    st.title("📉 Analisis de Rendimientos Historicos")
    st.markdown(
        "Comparativa de rendimientos del fondo vs su benchmark por periodo."
    )

    rend_data = load_rendimientos_data()

    if not rend_data:
        st.warning("⚠️ No hay datos de rendimientos disponibles.")
    else:
        df = pd.DataFrame(rend_data)
        df["rend_num"] = df["valor_rendimiento"].apply(parse_percentage_to_float)
        df["bench_num"] = df["valor_benchmark"].apply(parse_percentage_to_float)
        df["alpha"] = df["rend_num"] - df["bench_num"]

        # Orden de periodos
        period_order = ["1_mes", "3_meses", "12_meses", "3_anios"]
        period_labels = {
            "1_mes": "1 Mes",
            "3_meses": "3 Meses",
            "12_meses": "12 Meses",
            "3_anios": "3 Anos",
        }
        df["periodo_label"] = df["periodo"].map(period_labels)

        # --- Filtro por fondo ---
        fondos_disponibles = sorted(df["clave_pizarra"].unique().tolist())
        if fondos_disponibles:
            sel_fondo = st.selectbox(
                "Seleccionar Fondo",
                ["Todos"] + fondos_disponibles,
            )

            if sel_fondo != "Todos":
                df_filtered = df[df["clave_pizarra"] == sel_fondo]
            else:
                df_filtered = df
        else:
            df_filtered = df

        # --- Grafica de barras agrupadas (Fondo vs Benchmark) ---
        st.subheader("Rendimiento del Fondo vs Benchmark")

        if sel_fondo != "Todos" and len(df_filtered) > 0:
            fig_comp = go.Figure()
            df_plot = df_filtered.drop_duplicates(subset=["periodo"])

            fig_comp.add_trace(go.Bar(
                name="Fondo",
                x=df_plot["periodo_label"],
                y=df_plot["rend_num"],
                marker_color="#2E86AB",
            ))
            fig_comp.add_trace(go.Bar(
                name="Benchmark",
                x=df_plot["periodo_label"],
                y=df_plot["bench_num"],
                marker_color="#F18F01",
            ))

            fig_comp.update_layout(
                barmode="group",
                title=f"Rendimientos: {sel_fondo}",
                yaxis_title="Rendimiento (%)",
                template="plotly_white",
                height=400,
            )
            st.plotly_chart(fig_comp, use_container_width=True)

            # Alpha (exceso de retorno)
            alpha_12m = df_filtered[
                df_filtered["periodo"] == "12_meses"]["alpha"].values
            if len(alpha_12m) > 0 and not np.isnan(alpha_12m[0]):
                alpha_val = alpha_12m[0]
                delta_color = "normal" if alpha_val >= 0 else "inverse"
                st.metric(
                    "Alpha 12M (Fondo - Benchmark)",
                    f"{alpha_val:.2f}%",
                    delta=f"{alpha_val:.2f}%",
                    delta_color=delta_color,
                )
        else:
            # Vista agregada por periodo
            df_period_agg = df_filtered.groupby("periodo").agg(
                rend_promedio=("rend_num", "mean"),
                bench_promedio=("bench_num", "mean"),
                num_fondos=("rend_num", "count"),
            ).reindex(period_order).reset_index()
            df_period_agg["periodo_label"] = df_period_agg["periodo"].map(
                period_labels)

            if len(df_period_agg.dropna(subset=["rend_promedio"])) > 0:
                fig_agg = go.Figure()
                fig_agg.add_trace(go.Bar(
                    name="Promedio Fondos",
                    x=df_period_agg["periodo_label"],
                    y=df_period_agg["rend_promedio"],
                    marker_color="#2E86AB",
                ))
                fig_agg.add_trace(go.Bar(
                    name="Promedio Benchmarks",
                    x=df_period_agg["periodo_label"],
                    y=df_period_agg["bench_promedio"],
                    marker_color="#F18F01",
                ))
                fig_agg.update_layout(
                    barmode="group",
                    title="Rendimiento Promedio por Periodo (Universo Completo)",
                    yaxis_title="Rendimiento (%)",
                    template="plotly_white",
                    height=400,
                )
                st.plotly_chart(fig_agg, use_container_width=True)

        # --- Tabla de rendimientos ---
        st.markdown("---")
        st.subheader("Tabla de Rendimientos")
        display_df = df_filtered[[
            "clave_pizarra", "serie_accionaria", "institucion",
            "periodo_label", "valor_rendimiento", "valor_benchmark",
            "fecha_corte",
        ]].rename(columns={
            "periodo_label": "Periodo",
            "valor_rendimiento": "Rendimiento Fondo",
            "valor_benchmark": "Rendimiento Benchmark",
            "fecha_corte": "Fecha Corte",
        })
        st.dataframe(display_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.sidebar.markdown("---")
st.sidebar.caption(
    "Pipeline de Inteligencia Financiera v2.0\n\n"
    "Desarrollado como parte de Tesis de Ingenieria Matematica"
)
