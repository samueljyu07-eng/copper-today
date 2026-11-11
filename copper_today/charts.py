"""Plotly charts for the index and each component series."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

COPPER = "#9a5b2a"
MA = "#5f6f52"
FLAG = "#8f2d1f"
RULE = "#1f4b66"
INK = "#2a211c"
GRID = "#eadfce"
PAPER = "#f7f3ec"
PLOT = "#fffdf9"


def index_chart(frame: pd.DataFrame, p75: float | None, markers: list[dict]) -> go.Figure:
    figure = _figure("Index", 460)
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["index_value"],
            name="Index",
            mode="lines",
            line=dict(color=COPPER, width=2.6),
            customdata=frame["flag"],
            hovertemplate="%{x|%Y-%m-%d}<br>Index %{y:.1f}<br>%{customdata}<extra></extra>",
            connectgaps=False,
        )
    )
    _add_average(figure, frame, "index_ma_7", ",.1f")
    flagged = frame.loc[frame["flagged"]]
    if not flagged.empty:
        figure.add_trace(
            go.Scatter(
                x=flagged["date"],
                y=flagged["index_value"],
                name="Flagged",
                mode="markers",
                marker=dict(size=9, color=FLAG, symbol="diamond"),
                hoverinfo="skip",
            )
        )
    if p75 is not None:
        figure.add_hline(
            y=p75,
            line_dash="dot",
            line_color=FLAG,
            annotation_text=f"75th percentile ({p75:.1f})",
            annotation_position="top left",
            annotation_font_size=12,
            annotation_font_color=FLAG,
        )
    _add_markers(figure, markers, annotate=True)
    return figure


def series_chart(
    frame: pd.DataFrame,
    value_col: str,
    ma_col: str,
    name: str,
    ytitle: str,
    yformat: str,
    markers: list[dict],
) -> go.Figure:
    figure = _figure(ytitle, 340)
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame[value_col],
            name=name,
            mode="lines",
            line=dict(color=COPPER, width=2.4),
            hovertemplate=_hover(yformat),
            connectgaps=False,
        )
    )
    _add_average(figure, frame, ma_col, yformat)
    _add_markers(figure, markers, annotate=False)
    return figure


def _figure(ytitle: str, height: int) -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        paper_bgcolor=PAPER,
        plot_bgcolor=PLOT,
        font=dict(family="Arial, Helvetica, sans-serif", color=INK, size=13),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=64, r=24, t=48, b=40),
        height=height,
        yaxis_title=ytitle,
    )
    figure.update_xaxes(showgrid=False, zeroline=False)
    figure.update_yaxes(gridcolor=GRID, zeroline=False)
    return figure


def _add_average(figure: go.Figure, frame: pd.DataFrame, column: str, yformat: str) -> None:
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame[column],
            name="7-day average",
            mode="lines",
            line=dict(color=MA, width=1.8, dash="dot"),
            hovertemplate=_hover(yformat),
            connectgaps=False,
        )
    )


def _add_markers(figure: go.Figure, markers: list[dict], *, annotate: bool) -> None:
    for marker in markers:
        figure.add_vline(
            x=pd.Timestamp(marker["date"]),
            line_width=1,
            line_dash="dash",
            line_color=RULE,
            annotation_text=marker["label"] if annotate else None,
            annotation_position="top left",
            annotation_textangle=-90,
            annotation_font_size=11,
            annotation_font_color=RULE,
        )


def _hover(yformat: str) -> str:
    return f"%{{x|%Y-%m-%d}}<br>%{{y:{yformat}}}<extra></extra>"
