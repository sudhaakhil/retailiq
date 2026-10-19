"""Plotly chart helpers with one consistent, colour-blind-safe look.

Colours follow the Okabe-Ito palette. Series are also distinguished by line style / labels so meaning never
depends on colour alone. Money axes show the display currency code.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import state

PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#7F7F7F", "#8C564B"]
GOOD, BAD, NEUTRAL = "#009E73", "#D55E00", "#7F7F7F"


def _layout(fig: go.Figure, title: Optional[str] = None, height: int = 340, y_title: Optional[str] = None, money_axis: bool = False,
            legend: bool = True) -> go.Figure:
    cur = state.currency()
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=15)) if title else None, height=height, margin=dict(l=8, r=8, t=44 if title else 12, b=8),
        hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0) if legend else None, showlegend=legend,
        yaxis_title=(f"{y_title} ({cur.code})" if (money_axis and y_title) else (cur.code if money_axis else y_title)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False, linecolor="rgba(128,128,128,0.4)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.25)", zeroline=True, zerolinecolor="rgba(128,128,128,0.5)", tickformat=",.0f" if money_axis else None)
    return fig


def show(fig: go.Figure, alt: str, key: Optional[str] = None) -> None:
    st.plotly_chart(fig, width="stretch", alt=alt, key=key)


def lines(df: pd.DataFrame, x: str, series: Dict[str, str], title: Optional[str] = None, money_axis: bool = True, height: int = 340,
          y_title: Optional[str] = None, dash: Optional[Dict[str, str]] = None) -> go.Figure:
    """Line chart; ``series`` maps legend label -> column. Money columns must already be converted (``fx_df``)."""
    fig = go.Figure()
    for i, (label, col) in enumerate(series.items()):
        fig.add_scatter(x=df[x], y=df[col], mode="lines", name=label, line=dict(color=PALETTE[i % len(PALETTE)], width=2.2,
                                                                                dash=(dash or {}).get(label, "solid")))
    return _layout(fig, title, height, y_title, money_axis)


def bars(df: pd.DataFrame, x: str, series: Dict[str, str], title: Optional[str] = None, money_axis: bool = True, height: int = 340,
         stacked: bool = False, horizontal: bool = False, y_title: Optional[str] = None, text_fmt: Optional[str] = None) -> go.Figure:
    fig = go.Figure()
    for i, (label, col) in enumerate(series.items()):
        kw = dict(y=df[x], x=df[col], orientation="h") if horizontal else dict(x=df[x], y=df[col])
        fig.add_bar(name=label, marker_color=PALETTE[i % len(PALETTE)], text=df[col] if text_fmt else None,
                    texttemplate=text_fmt, **kw)
    fig.update_layout(barmode="stack" if stacked else "group")
    fig = _layout(fig, title, height, y_title, money_axis, legend=len(series) > 1)
    if horizontal:
        fig.update_yaxes(autorange="reversed", showgrid=False)
        fig.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,0.25)")
    return fig


def bar_line(df: pd.DataFrame, x: str, bar_cols: Dict[str, str], line_cols: Dict[str, str], title: Optional[str] = None, height: int = 360,
             line_is_pct: bool = False) -> go.Figure:
    """Bars on the left axis, lines on the right axis (e.g. sales bars + margin line)."""
    fig = go.Figure()
    for i, (label, col) in enumerate(bar_cols.items()):
        fig.add_bar(x=df[x], y=df[col], name=label, marker_color=PALETTE[i % len(PALETTE)])
    for j, (label, col) in enumerate(line_cols.items()):
        fig.add_scatter(x=df[x], y=df[col], name=label, mode="lines+markers", yaxis="y2",
                        line=dict(color=PALETTE[(len(bar_cols) + j) % len(PALETTE)], width=2.4, dash="dot"))
    fig = _layout(fig, title, height, money_axis=True)
    fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, tickformat=".0%" if line_is_pct else ",.0f"))
    return fig


def waterfall(labels: Sequence[str], values: Sequence[float], measures: Sequence[str], title: Optional[str] = None, height: int = 380) -> go.Figure:
    fig = go.Figure(go.Waterfall(x=list(labels), y=list(values), measure=list(measures), connector=dict(line=dict(color="rgba(128,128,128,0.5)")),
                                 increasing=dict(marker=dict(color=GOOD)), decreasing=dict(marker=dict(color=BAD)),
                                 totals=dict(marker=dict(color=PALETTE[0]))))
    return _layout(fig, title, height, money_axis=True, legend=False)


def heatmap(z: pd.DataFrame, title: Optional[str] = None, height: int = 420, fmt: str = ".0%") -> go.Figure:
    fig = go.Figure(go.Heatmap(z=z.values, x=[str(c) for c in z.columns], y=[str(i) for i in z.index], colorscale="Blues", zmin=0, zmax=1,
                               texttemplate="%{z:" + fmt + "}", hoverongaps=False, colorbar=dict(title="Retention", tickformat=".0%")))
    fig = _layout(fig, title, height, legend=False)
    fig.update_yaxes(autorange="reversed", zeroline=False)
    fig.update_layout(hovermode="closest")
    return fig


def scatter(df: pd.DataFrame, x: str, y: str, text: str, size: Optional[str] = None, color: Optional[str] = None, title: Optional[str] = None,
            height: int = 380, x_title: str = "", y_title: str = "", hline: Optional[float] = None) -> go.Figure:
    fig = go.Figure()
    sz = (df[size] / df[size].max() * 38 + 8) if size else 14
    fig.add_scatter(x=df[x], y=df[y], mode="markers+text", text=df[text], textposition="top center",
                    marker=dict(size=sz, color=[(GOOD if v >= 0 else BAD) for v in df[y]] if color is None else df[color], opacity=0.8,
                                line=dict(width=1, color="white")), hovertemplate="%{text}<br>" + x_title + ": %{x:,.2f}<br>" + y_title + ": %{y:,.0f}<extra></extra>")
    if hline is not None:
        fig.add_hline(y=hline, line_dash="dot", line_color="rgba(128,128,128,0.7)")
    fig = _layout(fig, title, height, legend=False)
    fig.update_layout(hovermode="closest", xaxis_title=x_title, yaxis_title=y_title)
    return fig


def donut(labels: Iterable, values: Iterable, title: Optional[str] = None, height: int = 320) -> go.Figure:
    fig = go.Figure(go.Pie(labels=list(labels), values=list(values), hole=0.55, sort=False, marker=dict(colors=PALETTE),
                           textinfo="label+percent", insidetextorientation="radial"))
    fig.update_layout(title=dict(text=title, x=0, font=dict(size=15)) if title else None, height=height, margin=dict(l=8, r=8, t=44 if title else 8, b=8),
                      showlegend=False, paper_bgcolor="rgba(0,0,0,0)")
    return fig


def cash_path(daily: pd.DataFrame, series: Dict[str, str], reserve: Optional[float] = None, title: Optional[str] = None, height: int = 380) -> go.Figure:
    """Cash balance lines with an optional minimum-reserve reference line. Values must be in display currency."""
    fig = lines(daily, "date", series, title, True, height)
    if reserve is not None:
        fig.add_hline(y=reserve, line_dash="dash", line_color=BAD, annotation_text="Minimum reserve", annotation_position="bottom right")
    return fig
