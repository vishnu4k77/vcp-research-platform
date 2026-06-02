"""Smart Filter bar — SQL-style query input for the scanner table.

UI design:
  - Preset buttons: one-click SQL for common setups
  - SQL text input: type WHERE clause + optional ORDER BY
  - Syntax-highlighted display: coloured SQL preview below the input
  - Field reference expander: available fields + operator cheatsheet
  - Watermark when no query is active

Supported syntax:
  WHERE field op value [AND/OR field op value]* [ORDER BY field [ASC|DESC]]
  Parenthesised OR groups: (VCP = 1 OR Breakout = 1)
  Fields with spaces must be double-quoted: "S2 Age(d)" "BO Ready"

Public API:  render_filter_bar(df) -> filtered + sorted DataFrame
"""

from __future__ import annotations

import math
import re
import pandas as pd
import streamlit as st

from app.config.strategy_config import DashboardConfig
from app.services.scanner_service import ScannerService

from app.dashboard.styles import (
    BLUE, GREEN, ORANGE, RED, PURPLE,
    TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY,
    BG_CARD, BG_ELEVATED, BG_INPUT,
    BORDER, BORDER_MED,
)

# ── SQL syntax-highlight colours ──────────────────────────────────────────────
_C_KW    = "#7B8DDE"   # blue-purple — WHERE AND OR ORDER BY ASC DESC
_C_FIELD = "#4EC9B0"   # teal        — field names
_C_OP    = "#CE9178"   # orange      — operators  = >= <=
_C_VAL   = "#B5CEA8"   # sage-green  — numeric values
_C_PAREN = "#FFD700"   # gold        — ( )

# ── Field registry ────────────────────────────────────────────────────────────
_FIELDS: dict[str, dict] = {
    "IC":          {"col": "IC",          "type": "bool"},
    "Trend":       {"col": "Trend",       "type": "bool"},
    "Stage2":      {"col": "Stage2",      "type": "bool"},
    "VCP":         {"col": "VCP",         "type": "bool"},
    "Breakout":    {"col": "Breakout",    "type": "bool"},
    "BO Ready":    {"col": "BO Ready",    "type": "bool"},
    "Liquid":      {"col": "Liquid",      "type": "bool"},
    "Quality":     {"col": "Quality",     "type": "bool"},
    "RS":          {"col": "RS",          "type": "bool"},
    "Score":       {"col": "Score",       "type": "numeric"},
    "S2 Age(d)":   {"col": "S2 Age(d)",   "type": "numeric"},
    "Dist Pivot%": {"col": "Dist Pivot%", "type": "numeric"},
    "Dist 52w%":   {"col": "Dist 52w%",   "type": "numeric"},
    "Fund Score":  {"col": "Fund Score",  "type": "numeric"},
    "ROE%":        {"col": "ROE%",        "type": "numeric"},
    "ROCE%":       {"col": "ROCE%",       "type": "numeric"},
    "Promoter%":   {"col": "Promoter%",   "type": "numeric"},
}

# Normalised lookup: lowercase display-name or col-name → canonical display name
_FIELD_LOOKUP: dict[str, str] = {}
for _d, _m in _FIELDS.items():
    _FIELD_LOOKUP[_d.lower()] = _d
    _FIELD_LOOKUP[_m["col"].lower()] = _d
_FIELD_LOOKUP["stage 2"] = "Stage2"   # backward-compat alias

_SORT_FIELDS = {
    "Score", "S2 Age(d)", "ROE%", "ROCE%",
    "Dist Pivot%", "Dist 52w%", "Fund Score", "Promoter%",
}

# Word aliases accepted as boolean values in conditions
_VALUE_ALIASES: dict[str, float] = {
    "tick":  1.0, "true":  1.0, "yes": 1.0, "on":  1.0,
    "cross": 0.0, "false": 0.0, "no":  0.0, "off": 0.0,
}
_VALUE_ALIAS_LOWER = set(_VALUE_ALIASES.keys())

# ── Presets stored as SQL strings ─────────────────────────────────────────────
_QUICK_PRESETS: dict[str, str] = {
    "🌱 Weinstein": (
        'WHERE IC = 1 AND Trend = 1 AND Stage2 = 1 AND "S2 Age(d)" <= 5 '
        'AND Liquid = 1 AND Quality = 1 ORDER BY "S2 Age(d)" ASC'
    ),
    "🌀 VCP Watch": (
        'WHERE Trend = 1 AND Stage2 = 1 AND VCP = 1 AND Liquid = 1 '
        'ORDER BY Score DESC'
    ),
    "⚡ Breakout": (
        'WHERE Breakout = 1 AND Liquid = 1 ORDER BY Score DESC'
    ),
    "🚀 RS Leaders": (
        'WHERE Trend = 1 AND Stage2 = 1 AND RS = 1 AND Liquid = 1 '
        'ORDER BY Score DESC'
    ),
    "🏆 Minervini A+": (
        'WHERE IC = 1 AND Trend = 1 AND Stage2 = 1 AND RS = 1 AND Liquid = 1 '
        'AND Quality = 1 AND (VCP = 1 OR Breakout = 1 OR "BO Ready" = 1) '
        'ORDER BY Score DESC'
    ),
}

# Sample queries are no longer hardcoded here.
# They are seeded into the saved_queries table (is_sample = 1) by
# setup_db._seed_sample_queries(), which reads ScannerQueryConfig.SAMPLE_QUERIES
# from strategy_config.py.  To add or edit samples, update that config class
# and re-run setup_db.py.  No UI code changes required.

# Chips shown in the field-reference expander
_FIELD_CHIPS = [
    "IC", "Trend", "Stage2", "VCP", "Breakout", '"BO Ready"',
    "Liquid", "Quality", "RS", "Score",
    '"S2 Age(d)"', '"Dist Pivot%"', '"Dist 52w%"', '"Fund Score"',
    "ROE%", "ROCE%", "Promoter%",
]

# Pre-rendered watermark HTML (shown when query is empty)
_WATERMARK_HTML = (
    f'e.g.&nbsp;&nbsp;'
    f'<span style="color:{_C_KW};font-weight:700">WHERE</span> '
    f'<span style="color:{_C_FIELD};font-weight:600">Stage2</span>'
    f'<span style="color:{_C_OP};font-weight:600"> = </span>'
    f'<span style="color:{_C_VAL};font-weight:600">1</span> '
    f'<span style="color:{_C_KW};font-weight:700">AND</span> '
    f'<span style="color:{_C_FIELD};font-weight:600">&quot;S2 Age(d)&quot;</span>'
    f'<span style="color:{_C_OP};font-weight:600"> &lt;= </span>'
    f'<span style="color:{_C_VAL};font-weight:600">5</span> '
    f'<span style="color:{_C_KW};font-weight:700">AND</span> '
    f'<span style="color:{_C_FIELD};font-weight:600">ROE%</span>'
    f'<span style="color:{_C_OP};font-weight:600"> &gt;= </span>'
    f'<span style="color:{_C_VAL};font-weight:600">15</span>'
    f'&nbsp;&nbsp;'
    f'<span style="color:{_C_KW};font-weight:700">ORDER BY</span> '
    f'<span style="color:{_C_FIELD};font-weight:600">Score</span> '
    f'<span style="color:{_C_KW};font-weight:700">DESC</span>'
)

_K_QUERY     = "_sf_query"
_K_SAVE_NAME = "_sf_save_name"
_K_SAVE_DESC = "_sf_save_desc"
_K_SAVE_MSG  = "_sf_save_msg"   # ("ok"|"err", message) — shown one render then cleared


def _init_state() -> None:
    """Initialise all session-state keys used by the filter bar on first render."""
    if _K_QUERY     not in st.session_state: st.session_state[_K_QUERY]     = ""
    if _K_SAVE_NAME not in st.session_state: st.session_state[_K_SAVE_NAME] = ""
    if _K_SAVE_DESC not in st.session_state: st.session_state[_K_SAVE_DESC] = ""
    if _K_SAVE_MSG  not in st.session_state: st.session_state[_K_SAVE_MSG]  = None


def _query_box_height(text: str) -> int:
    """Return the text-area height in pixels that fits the query without scrolling.

    Height grows with query length and is clamped to DashboardConfig bounds so
    the box never takes over the screen.  All thresholds are config-driven.

    Args:
        text: Current query string.

    Returns:
        Height in pixels clamped to [QUERY_BOX_MIN_HEIGHT_PX, QUERY_BOX_MAX_HEIGHT_PX].
    """
    cfg = DashboardConfig
    if not text:
        return cfg.QUERY_BOX_MIN_HEIGHT_PX
    n_lines = max(2, math.ceil(len(text) / cfg.QUERY_BOX_CHARS_PER_LINE) + 1)
    raw_height = n_lines * cfg.QUERY_BOX_LINE_HEIGHT_PX + 16  # +16 for inner padding
    return min(max(raw_height, cfg.QUERY_BOX_MIN_HEIGHT_PX), cfg.QUERY_BOX_MAX_HEIGHT_PX)


# ── Parser ────────────────────────────────────────────────────────────────────

def _split_top_level(text: str) -> list[tuple[str, str]]:
    """Split WHERE clause on top-level AND/OR, ignoring content inside parentheses."""
    parts: list[tuple[str, str]] = []
    depth, i, n = 0, 0, len(text)
    current: list[str] = []
    logic = "WHERE"

    while i < n:
        c = text[i]
        if c == "(":
            depth += 1
            current.append(c)
            i += 1
        elif c == ")":
            depth -= 1
            current.append(c)
            i += 1
        elif depth == 0:
            m = re.match(r"\b(AND|OR)\b", text[i:], re.IGNORECASE)
            if m:
                clause = "".join(current).strip()
                if clause:
                    parts.append((logic, clause))
                logic = m.group(1).upper()
                current = []
                i += len(m.group(0))
            else:
                current.append(c)
                i += 1
        else:
            current.append(c)
            i += 1

    clause = "".join(current).strip()
    if clause:
        parts.append((logic, clause))
    return parts


# Matches: "quoted field" op value   OR   unquoted_field op value
# Value can be a number OR a word alias (tick, true, false, yes, no …)
# The unquoted branch uses [\w%()]+(?:\s+[\w%()]+)* so that fields with
# spaces or parens (S2 Age(d), BO Ready, Dist Pivot%) are handled even
# when the user forgets to quote them.
_COND_RE = re.compile(
    r'^"([^"]+)"\s*(>=|<=|>|<|=)\s*(-?\d+\.?\d*|\w+)\s*$'
    r'|'
    r'^([\w%()]+(?:\s+[\w%()]+)*)\s*(>=|<=|>|<|=)\s*(-?\d+\.?\d*|\w+)\s*$',
    re.IGNORECASE,
)


def _build_between_pattern() -> re.Pattern:
    """Build a BETWEEN regex anchored to the exact known field names.

    Uses the _FIELDS registry as the source of truth so any new field added
    to _FIELDS is automatically supported here — no regex edits required.

    Fields with spaces or parens are included in two forms:
      - quoted:   "S2 Age(d)"
      - unquoted: S2 Age(d)

    Using known names (longest first) prevents a generic greedy wildcard from
    accidentally matching partial expressions like "1 AND S2 Age(d)".

    Returns:
        Compiled regex that matches:  FIELD BETWEEN number AND number
    """
    alts: list[str] = []
    for name in sorted(_FIELDS.keys(), key=len, reverse=True):
        escaped = re.escape(name)
        if re.search(r"[\s()]", name):
            alts.append(f'"(?:{escaped})"')   # "S2 Age(d)"
            alts.append(f"(?:{escaped})")     # S2 Age(d)
        else:
            alts.append(escaped)              # Stage2  VCP  ROE%  etc.
    fields_re = "|".join(alts)
    return re.compile(
        rf"({fields_re})\s+BETWEEN\s+(-?\d+\.?\d*)\s+AND\s+(-?\d+\.?\d*)",
        re.IGNORECASE,
    )


_BETWEEN_RE: re.Pattern = _build_between_pattern()


def _preprocess_between(text: str) -> str:
    """Expand FIELD BETWEEN x AND y  →  FIELD >= x AND FIELD <= y.

    Must run before _split_top_level so the AND inside BETWEEN is not
    mistakenly treated as a top-level condition separator.

    Fields with spaces or parens are auto-quoted in the output so
    _parse_simple can resolve them via the quoted-field branch of _COND_RE.

    Args:
        text: Raw WHERE clause text with ORDER BY already stripped.

    Returns:
        Text with every BETWEEN … AND … construct replaced by two comparisons.
    """
    def _expand(m: re.Match) -> str:
        """Replace one BETWEEN match with two comparison conditions."""
        field = m.group(1).strip()
        low, high = m.group(2), m.group(3)
        already_quoted = field.startswith('"') and field.endswith('"')
        if not already_quoted and re.search(r"[\s()]", field):
            field = f'"{field}"'
        return f"{field} >= {low} AND {field} <= {high}"

    return _BETWEEN_RE.sub(_expand, text)


def _parse_simple(text: str) -> dict | None:
    """Parse a single 'field op value' string into a condition dict."""
    m = _COND_RE.match(text.strip())
    if not m:
        return None

    if m.group(1) is not None:   # quoted field
        raw_field, op, val_s = m.group(1), m.group(2), m.group(3)
    else:                         # unquoted field
        raw_field, op, val_s = m.group(4), m.group(5), m.group(6)

    canonical = _FIELD_LOOKUP.get(raw_field.strip().lower())
    if not canonical:
        return None

    # Resolve word aliases (tick → 1.0, true → 1.0, false → 0.0, etc.)
    val_lower = val_s.strip().lower()
    if val_lower in _VALUE_ALIASES:
        val = _VALUE_ALIASES[val_lower]
    else:
        try:
            val = float(val_s)
        except ValueError:
            return None

    return {
        "field": canonical,
        "col":   _FIELDS[canonical]["col"],
        "op":    op,
        "value": val,
    }


def _parse_query(text: str) -> tuple[list[dict], str, bool, str | None]:
    """Parse a SQL-like query string.

    Returns:
        (conditions, sort_field, sort_asc, error_msg)
        Each condition is a dict with keys:
          type  = "simple" | "or_group"
          logic = "WHERE" | "AND" | "OR"
          For simple: col, op, value
          For or_group: conditions (list of simple dicts without logic/type)
    """
    text = text.strip()
    if not text:
        return [], "Score", False, None

    # 1. Extract ORDER BY from end
    sort_field, sort_asc = "Score", False
    om = re.search(
        r'\bORDER\s+BY\s+"?([^"]+?)"?\s*(ASC|DESC)?\s*$',
        text, re.IGNORECASE,
    )
    if om:
        raw_sf    = om.group(1).strip()
        direction = (om.group(2) or "DESC").upper()
        resolved  = _FIELD_LOOKUP.get(raw_sf.lower())
        if resolved and resolved in _SORT_FIELDS:
            sort_field = resolved
        sort_asc = direction == "ASC"
        text = text[: om.start()].strip()

    # 2. Strip optional WHERE keyword
    text = re.sub(r"^\s*WHERE\s+", "", text, flags=re.IGNORECASE).strip()
    if not text:
        return [], sort_field, sort_asc, None

    # 3. Expand BETWEEN before splitting (prevents AND inside BETWEEN from being
    #    treated as a top-level condition separator)
    text = _preprocess_between(text)

    # 4. Split on top-level AND/OR and parse each clause
    conditions: list[dict] = []
    errors: list[str] = []

    for logic, clause in _split_top_level(text):
        clause = clause.strip()

        if clause.startswith("(") and clause.endswith(")"):
            # Parenthesised OR group: (A = 1 OR B = 1 OR C = 1)
            inner = clause[1:-1].strip()
            group: list[dict] = []
            for part in re.split(r"\bOR\b", inner, flags=re.IGNORECASE):
                c = _parse_simple(part.strip())
                if c:
                    group.append(c)
                else:
                    errors.append(part.strip())
            if group:
                conditions.append({"type": "or_group", "logic": logic, "conditions": group})
        else:
            c = _parse_simple(clause)
            if c:
                c.update({"type": "simple", "logic": logic})
                conditions.append(c)
            else:
                errors.append(clause)

    err = (
        f"Could not parse: {', '.join(repr(e) for e in errors)}"
        if errors else None
    )
    return conditions, sort_field, sort_asc, err


# ── SQL syntax highlighter ────────────────────────────────────────────────────

_KNOWN_FIELD_LOWER = (
    {k.lower() for k in _FIELDS}
    | {v["col"].lower() for v in _FIELDS.values()}
)
_KW_UPPER = {"WHERE", "AND", "OR", "ASC", "DESC", "BY", "BETWEEN"}


def _highlight_sql(text: str) -> str:
    """Tokenise SQL text and return syntax-highlighted HTML."""

    def _span(color: str, content: str, bold: bool = False) -> str:
        fw   = "700" if bold else "600"
        safe = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<span style="color:{color};font-weight:{fw}">{safe}</span>'

    def _raw(content: str) -> str:
        return content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    out: list[str] = []
    i, n = 0, len(text)

    while i < n:
        c = text[i]

        # Whitespace
        if c.isspace():
            out.append(_raw(c))
            i += 1
            continue

        # Quoted field  "S2 Age(d)"
        if c == '"':
            j = text.find('"', i + 1)
            j = n - 1 if j == -1 else j
            out.append(_span(_C_FIELD, text[i: j + 1]))
            i = j + 1
            continue

        # Parentheses
        if c in "()":
            out.append(_span(_C_PAREN, c, bold=True))
            i += 1
            continue

        # Negative number  -25
        if c == "-" and i + 1 < n and text[i + 1].isdigit():
            m = re.match(r"-\d+\.?\d*", text[i:])
            if m:
                out.append(_span(_C_VAL, m.group()))
                i += len(m.group())
                continue

        # Multi-char operator  >= <=
        m = re.match(r">=|<=", text[i:])
        if m:
            out.append(_span(_C_OP, m.group()))
            i += len(m.group())
            continue

        # Single-char operator  = > <
        if c in "><=":
            out.append(_span(_C_OP, c))
            i += 1
            continue

        # Positive number
        m = re.match(r"\d+\.?\d*", text[i:])
        if m:
            out.append(_span(_C_VAL, m.group()))
            i += len(m.group())
            continue

        # Word token
        m = re.match(r"[\w%]+", text[i:])
        if m:
            word = m.group()
            wu   = word.upper()

            # Two-word keyword: ORDER BY
            if wu == "ORDER":
                bm = re.match(r"\s+BY\b", text[i + len(word):], re.IGNORECASE)
                if bm:
                    full = word + bm.group()
                    out.append(_span(_C_KW, full, bold=True))
                    i += len(full)
                    continue

            if wu in _KW_UPPER:
                out.append(_span(_C_KW, word, bold=True))
            elif word.lower() in _KNOWN_FIELD_LOWER:
                out.append(_span(_C_FIELD, word))
            elif word.lower() in _VALUE_ALIAS_LOWER:
                out.append(_span(_C_VAL, word))
            else:
                out.append(_raw(word))
            i += len(word)
            continue

        # Fallback
        out.append(_raw(c))
        i += 1

    return "".join(out)


# ── Filter application ────────────────────────────────────────────────────────

_OP_FN = {
    "=":  lambda s, v: s == v,
    ">=": lambda s, v: s >= v,
    "<=": lambda s, v: s <= v,
    ">":  lambda s, v: s > v,
    "<":  lambda s, v: s < v,
}


def _col_mask(df: pd.DataFrame, col: str, op: str, value: float) -> pd.Series:
    fn = _OP_FN.get(op)
    if fn is None or col not in df.columns:
        return pd.Series(True, index=df.index)
    return fn(pd.to_numeric(df[col], errors="coerce"), value).fillna(False)


def _apply_filters(df: pd.DataFrame, conditions: list[dict]) -> pd.DataFrame:
    if df.empty or not conditions:
        return df

    mask = pd.Series(True, index=df.index)

    for cond in conditions:
        logic = cond.get("logic", "AND")

        if cond["type"] == "simple":
            row_mask = _col_mask(df, cond["col"], cond["op"], cond["value"])
        else:  # or_group
            row_mask = pd.Series(False, index=df.index)
            for c in cond["conditions"]:
                row_mask |= _col_mask(df, c["col"], c["op"], c["value"])

        if logic in ("WHERE", "AND"):
            mask &= row_mask
        else:
            mask |= row_mask

    return df[mask]


# ── Main render ───────────────────────────────────────────────────────────────

def render_filter_bar(df: pd.DataFrame) -> pd.DataFrame:
    """Render the SQL query filter bar and return the filtered + sorted DataFrame.

    Call after the display DataFrame has been built so column names match
    the field registry (_FIELDS col values = display column names).

    Args:
        df: Scanner DataFrame with display column names.

    Returns:
        Filtered and sorted DataFrame.
    """
    _init_state()
    query     = st.session_state[_K_QUERY]
    has_query = bool(query.strip())

    with st.container(border=True):

        # ── Preset buttons + Clear ────────────────────────────────────────────
        preset_names = list(_QUICK_PRESETS.keys())
        btn_cols = st.columns([1.35, 1.35, 1.0, 1.35, 1.65, 0.9])
        for col, pname in zip(btn_cols[:5], preset_names):
            with col:
                is_active = has_query and query.strip() == _QUICK_PRESETS[pname].strip()
                if st.button(
                    pname,
                    key=f"qp_{pname}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state[_K_QUERY] = _QUICK_PRESETS[pname]
                    st.rerun()
        with btn_cols[5]:
            if st.button(
                "✕ Clear" if has_query else "Clear",
                key="sf_clear",
                use_container_width=True,
                type="primary" if has_query else "secondary",
                disabled=not has_query,
            ):
                st.session_state[_K_QUERY] = ""
                st.rerun()

        # ── SQL text area (auto-height) ───────────────────────────────────────
        new_query = st.text_area(
            "SQL query",
            value=query,
            key="sf_query_input",
            placeholder='WHERE Stage2 = 1 AND "S2 Age(d)" <= 5 ORDER BY Score DESC',
            label_visibility="collapsed",
            height=_query_box_height(query),
        )
        if new_query != query:
            st.session_state[_K_QUERY] = new_query
            query     = new_query
            has_query = bool(query.strip())

        # ── Parse ─────────────────────────────────────────────────────────────
        conditions, sort_field, sort_asc, parse_err = _parse_query(query)

        # ── SQL display panel ─────────────────────────────────────────────────
        if has_query and conditions:
            sort_dir = "↑ ASC" if sort_asc else "↓ DESC"
            n_cond   = len(conditions)
            badges = (
                f'<span style="background:{GREEN}20;color:{GREEN};'
                f'border:1px solid {GREEN}40;border-radius:4px;'
                f'padding:2px 9px;font-family:Inter,sans-serif;'
                f'font-size:10px;font-weight:700;margin-right:7px">'
                f'{n_cond} condition{"s" if n_cond != 1 else ""}</span>'
                f'<span style="background:{BLUE}20;color:{BLUE};'
                f'border:1px solid {BLUE}40;border-radius:4px;'
                f'padding:2px 9px;font-family:Inter,sans-serif;'
                f'font-size:10px;font-weight:700">'
                f'&#8645; {sort_field} {sort_dir}</span>'
            )
            highlighted = _highlight_sql(query)
            st.markdown(
                f'<div style="background:{BG_INPUT};border:1px solid {BORDER_MED};'
                f'border-radius:8px;padding:10px 16px;margin-top:5px">'
                f'<div style="margin-bottom:7px">{badges}</div>'
                f'<div style="font-family:\'Courier New\',monospace;font-size:13px;'
                f'line-height:1.9;word-break:break-all;letter-spacing:0.01em">'
                f'{highlighted}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if parse_err:
                st.caption(f"⚠ Some conditions skipped — {parse_err}")

        elif has_query and parse_err:
            st.markdown(
                f'<div style="background:{BG_INPUT};border:1px solid {RED}50;'
                f'border-radius:8px;padding:10px 16px;margin-top:5px;'
                f'font-family:\'Courier New\',monospace;font-size:12.5px;color:{RED}">'
                f'⚠ &nbsp;Parse error — {parse_err}'
                f'</div>',
                unsafe_allow_html=True,
            )

        else:
            # Watermark / empty-state
            st.markdown(
                f'<div style="background:{BG_INPUT};border:1px dashed {BORDER_MED};'
                f'border-radius:8px;padding:10px 16px;margin-top:5px;'
                f'font-family:\'Courier New\',monospace;font-size:13px;'
                f'line-height:1.9;opacity:0.65">'
                f'{_WATERMARK_HTML}'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Bottom row: sample queries | save | field reference ──────────────
        ex_col, sv_col, ref_col = st.columns([2.5, 1.5, 2.0])

        # ── Sample queries expander (DB-driven via ScannerQueryConfig) ───────
        with ex_col:
            try:
                sample_df = ScannerService.list_sample_queries()
            except AttributeError:
                sample_df = __import__("pandas").DataFrame()
            n_samples = len(sample_df)
            label_ex = f"💡 Sample queries ({n_samples})" if n_samples else "💡 Sample queries"
            with st.expander(label_ex, expanded=False):
                if sample_df.empty:
                    st.caption("Run setup_db.py to seed sample queries into the database.")
                else:
                    st.markdown(
                        f'<div style="font-size:10px;color:{TEXT_MUTED};'
                        f'margin-bottom:4px">Click any example to load it into the query box</div>',
                        unsafe_allow_html=True,
                    )
                    for _, row in sample_df.iterrows():
                        tip = row.get("description") or row["sql_text"]
                        if st.button(
                            row["query_name"],
                            key=f"sq_{row['id']}",
                            use_container_width=True,
                            help=str(tip),
                        ):
                            st.session_state[_K_QUERY] = row["sql_text"]
                            st.rerun()

        # ── Save query popover ────────────────────────────────────────────────
        with sv_col:
            save_disabled = not has_query
            with st.popover(
                "💾 Save query" if not save_disabled else "💾 Save",
                disabled=save_disabled,
                use_container_width=True,
            ):
                st.markdown(
                    f'<div style="font-size:12px;font-weight:600;'
                    f'color:{TEXT_PRIMARY};margin-bottom:4px">Save current query</div>',
                    unsafe_allow_html=True,
                )
                sv_name = st.text_input(
                    "Name",
                    value=st.session_state[_K_SAVE_NAME],
                    key="sf_sav_name_input",
                    placeholder="e.g. My RS leaders",
                    max_chars=100,
                )
                sv_desc = st.text_input(
                    "Description (optional)",
                    value=st.session_state[_K_SAVE_DESC],
                    key="sf_sav_desc_input",
                    placeholder="short note about this filter",
                )
                st.code(query[:120] + ("…" if len(query) > 120 else ""), language=None)
                if st.button("Save to DB", key="sf_save_btn", type="primary",
                             use_container_width=True):
                    st.session_state[_K_SAVE_NAME] = sv_name
                    st.session_state[_K_SAVE_DESC] = sv_desc
                    try:
                        ok, msg = ScannerService.save_query(sv_name, query, sv_desc)
                    except AttributeError:
                        ok, msg = False, "Restart Streamlit to activate the save feature."
                    except Exception as _exc:
                        ok, msg = False, f"Save failed: {_exc}"
                    st.session_state[_K_SAVE_MSG] = ("ok" if ok else "err", msg)
                    if ok:
                        st.session_state[_K_SAVE_NAME] = ""
                        st.session_state[_K_SAVE_DESC] = ""
                    st.rerun()

            # Show one-shot save feedback outside the popover
            if st.session_state[_K_SAVE_MSG]:
                kind, msg = st.session_state[_K_SAVE_MSG]
                if kind == "ok":
                    st.success(msg, icon="✅")
                else:
                    st.error(msg, icon="⚠")
                st.session_state[_K_SAVE_MSG] = None

        # ── Field reference expander ──────────────────────────────────────────
        with ref_col:
            with st.expander("📋 Fields & syntax", expanded=False):
                chip_parts = []
                for chip in _FIELD_CHIPS:
                    chip_parts.append(
                        f'<span style="background:{BG_ELEVATED};color:{_C_FIELD};'
                        f'border:1px solid {BORDER_MED};border-radius:4px;'
                        f'padding:2px 7px;font-family:Courier New,monospace;'
                        f'font-size:11px;white-space:nowrap;margin:2px">{chip}</span>'
                    )
                chips_html = "&nbsp; ".join(chip_parts)
                st.markdown(
                    f'<div style="line-height:2.2;margin-bottom:5px">{chips_html}</div>'
                    f'<div style="font-size:10px;color:{TEXT_MUTED};line-height:1.7">'
                    f'Operators: = &nbsp; &gt;= &nbsp; &lt;= &nbsp; &gt; &nbsp; &lt;'
                    f'&nbsp;|&nbsp; Quote fields with spaces: &quot;S2 Age(d)&quot;'
                    f'&nbsp;|&nbsp; Sort: ORDER BY Score DESC'
                    f'&nbsp;|&nbsp; OR group: (VCP = 1 OR Breakout = 1)'
                    f'&nbsp;|&nbsp; Range: Stage2 BETWEEN 1 AND 20'
                    f'&nbsp;|&nbsp; Bool: Liquid = tick / true / 1'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # ── Saved queries expander ────────────────────────────────────────────
        try:
            saved_df = ScannerService.list_saved_queries()
        except AttributeError:
            saved_df = __import__("pandas").DataFrame()
        if not saved_df.empty:
            with st.expander(
                f"📂 My saved queries ({len(saved_df)})", expanded=False
            ):
                for _, row in saved_df.iterrows():
                    r1, r2 = st.columns([5, 1])
                    with r1:
                        tip = row.get("description") or row["sql_text"][:100]
                        if st.button(
                            row["query_name"],
                            key=f"svq_{row['id']}",
                            help=tip,
                            use_container_width=True,
                        ):
                            st.session_state[_K_QUERY] = row["sql_text"]
                            st.rerun()
                        st.markdown(
                            f'<div style="font-family:Courier New,monospace;'
                            f'font-size:10.5px;color:{TEXT_MUTED};'
                            f'margin:-6px 0 4px 4px;word-break:break-all">'
                            f'{row["sql_text"][:110]}'
                            f'{"…" if len(row["sql_text"]) > 110 else ""}</div>',
                            unsafe_allow_html=True,
                        )
                    with r2:
                        if st.button(
                            "🗑",
                            key=f"del_{row['id']}",
                            help="Delete this saved query",
                        ):
                            ScannerService.delete_query(int(row["id"]))
                            st.rerun()

    # ── Apply filters ─────────────────────────────────────────────────────────
    result = _apply_filters(df, conditions)

    # ── Apply sort ────────────────────────────────────────────────────────────
    sort_col = _FIELDS.get(sort_field, {}).get("col", sort_field)
    if sort_col in result.columns:
        result = result.sort_values(
            sort_col, ascending=sort_asc, na_position="last",
        ).reset_index(drop=True)

    return result
