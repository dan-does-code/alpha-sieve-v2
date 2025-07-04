import re
import json
import pandas as pd
import statistics
from collections import Counter
import math

# --- Utility functions ---

def sanitize(raw: str):
    """Clean a raw spreadsheet cell string into number, dict, or original."""
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw in ("", "N/A", "-"):
        return None
    if raw.endswith("%"):
        try:
            return float(raw[:-1])
        except ValueError:
            return raw
    m = re.match(r"^([+-]?\d+(\.\d+)?)\s*(SOL|USDC)$", raw, re.IGNORECASE)
    if m:
        return {"value": float(m.group(1)), "currency": m.group(3).upper()}
    m = re.match(r"^([+-]?\d+(\.\d+)?)\s*(sec|min|h|day)s?$", raw, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        unit = m.group(3).lower()
        factors = {"sec": 1, "min": 60, "h": 3600, "day": 86400}
        return v * factors[unit]
    if re.match(r"^[\d,]+\.\d+$", raw) or re.match(r"^[\d,]+$", raw):
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            return raw
    try:
        return float(raw)
    except ValueError:
        return raw

def parse_si(s: str):
    """Convert SI suffixes K/M/B into floats."""
    if s is None:
        return None
    s = str(s).strip().upper()
    m = re.match(r"^([+-]?\d+(\.\d+)?)([KMB])$", s)
    if m:
        num = float(m.group(1))
        suf = m.group(3)
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}[suf]
        return num * mult
    try:
        return float(s)
    except (ValueError, TypeError):
        return None

def _safe_divide(numerator, denominator):
    """Helper for safe division to avoid ZeroDivisionError."""
    if denominator is None or numerator is None or denominator == 0 or (isinstance(denominator, float) and math.isnan(denominator)):
        return 0.0
    return numerator / denominator

def _safe_stats(data, func, default=0.0):
    """Helper for safe statistics calculation on potentially empty lists."""
    clean_data = [x for x in data if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not clean_data:
        return default
    try:
        return func(clean_data)
    except statistics.StatisticsError:
        return default

def _skewness(data, default=0.0):
    """Calculates sample skewness."""
    clean_data = [x for x in data if x is not None]
    n = len(clean_data)
    if n < 3:
        return default
    mean = _safe_stats(clean_data, statistics.mean)
    std_dev = _safe_stats(clean_data, statistics.stdev) if n > 1 else 0.0
    if std_dev == 0:
        return 0.0
    third_moment = sum(((x - mean) / std_dev) ** 3 for x in clean_data)
    return (n / ((n - 1) * (n - 2))) * third_moment

def _max_drawdown(pnl_series):
    """Calculates the maximum drawdown from a series of PnLs."""
    clean_pnl = [p for p in pnl_series if p is not None]
    if not clean_pnl:
        return 0.0
    cumulative_pnl = 0
    peak = 0
    max_drawdown = 0
    for pnl in clean_pnl:
        cumulative_pnl += pnl
        peak = max(peak, cumulative_pnl)
        drawdown = peak - cumulative_pnl
        max_drawdown = max(max_drawdown, drawdown)
    return -max_drawdown

# --- Core Parsing ---

def parse_sheet_to_raw_data(df: pd.DataFrame):
    """Parse one sheet into a raw structure for later processing."""
    data = df.fillna("").astype(str).values.tolist()
    max_row = len(data)
    
    wallet_info = {}
    anchor_row = next((i for i, row in enumerate(data) if str(row[0]).strip().lower().startswith("wallet")), 0)
    for r in (anchor_row, anchor_row + 2):
        if r + 1 >= max_row: continue
        for c, raw_h in enumerate(data[r]):
            h = str(raw_h).strip()
            if not h: continue
            val = data[r + 1][c]
            if h.lower() != "balance":
                 wallet_info[h] = sanitize(val)

    token_anchor_row = next((i for i, row in enumerate(data) if str(row[0]).strip().lower() == "token"), -1)
    trades = []
    daily_pnl = {}
    if token_anchor_row != -1:
        headers = [str(h).strip() for h in data[token_anchor_row]]
        spl_col_idx = headers.index("SPL Income") if "SPL Income" in headers else -1
        mcap_col_idx = headers.index("MCAP 1st Buy / Last Tx / Now") if "MCAP 1st Buy / Last Tx / Now" in headers else -1
        
        current_date = None
        for r in range(token_anchor_row + 1, max_row):
            row_str = str(data[r][0]).strip()
            if row_str.lower().startswith("related wallets"): break
            
            m = re.match(r"(\d{2}\.\d{2}\.\d{4}):", row_str)
            if m:
                current_date = pd.to_datetime(m.group(1), dayfirst=True).date().isoformat()
                daily_pnl.setdefault(current_date, 0.0)
            
            if spl_col_idx == -1 or not data[r][spl_col_idx].strip(): continue

            trade = {}
            for c, h in enumerate(headers):
                raw = data[r][c]
                if c == mcap_col_idx:
                    parts = str(raw).strip().split()
                    trade["market_cap"] = {
                        "on_first_buy": parse_si(parts[0]) if len(parts) > 0 else None,
                        "on_last_tx": parse_si(parts[1]) if len(parts) > 1 else None,
                        "now": parse_si(parts[2]) if len(parts) > 2 else None,
                    }
                else:
                    trade[h] = sanitize(raw)
            
            if current_date and isinstance(trade.get('Delta Sol'), dict):
                daily_pnl[current_date] += trade['Delta Sol'].get('value', 0.0)
            trades.append(trade)

    return { "wallet_info": wallet_info, "trades": trades, "daily_pnl": daily_pnl }

# --- Enrichment Function ---

def create_enriched_profile(raw_data):
    """Transforms raw parsed data into the final, definitive JSON schema."""
    info = raw_data.get('wallet_info', {})
    trades = raw_data.get('trades', [])
    
    delta_sols = [t.get('Delta Sol', {}).get('value') for t in trades]
    delta_percents = [t.get('Delta %') for t in trades]
    spent_sols = [t.get('Spent Sol', {}).get('value') for t in trades]
    durations_sec = [t.get('Duration') for t in trades]
    mc_buys = [t.get('market_cap', {}).get('on_first_buy') for t in trades]
    platforms = [t.get('1st Buy On') for t in trades]
    tokens = [t.get('Token') for t in trades]

    winning_pnls = [p for p in delta_sols if p is not None and p > 0]
    losing_pnls = [p for p in delta_sols if p is not None and p <= 0]
    avg_win_pnl = _safe_stats(winning_pnls, statistics.mean)
    avg_loss_pnl = _safe_stats(losing_pnls, statistics.mean)
    win_rate = info.get('WinRate', 0.0) / 100.0 if info.get('WinRate') is not None else 0.0
    loss_rate = 1.0 - win_rate
    
    per_trade_pnl_std = _safe_stats(delta_sols, statistics.stdev) if len([x for x in delta_sols if x is not None]) > 1 else 0.0
    daily_pnls = list(raw_data.get('daily_pnl', {}).values())
    daily_pnl_std = _safe_stats(daily_pnls, statistics.stdev) if len(daily_pnls) > 1 else 0.0
    
    performance_and_risk = {
        "pnl_sol": info.get('PnL', {}).get('value'),
        "roi_percent": info.get('ROI'),
        "win_rate_percent": info.get('WinRate'),
        "sharpe_ratio_proxy": _safe_divide(_safe_stats(delta_sols, statistics.mean), per_trade_pnl_std),
        "expectancy_per_trade_sol": (win_rate * avg_win_pnl) + (loss_rate * avg_loss_pnl),
        "avg_win_pnl_sol": avg_win_pnl,
        "avg_loss_pnl_sol": avg_loss_pnl,
        "win_loss_pnl_ratio": _safe_divide(abs(avg_win_pnl), abs(avg_loss_pnl)),
        "per_trade_pnl_std_dev_sol": per_trade_pnl_std,
        "daily_pnl_std_dev_sol": daily_pnl_std,
        "pnl_skewness": _skewness(delta_sols),
        "max_drawdown_sol": _max_drawdown(delta_sols),
        "full_loss_trade_count": sum(1 for dp in delta_percents if dp is not None and dp <= -99.9),
        "pnl_to_fees_ratio": _safe_divide(info.get('PnL', {}).get('value'), info.get('Total Fees', {}).get('value'))
    }

    avg_trade_size = _safe_stats(spent_sols, statistics.mean)
    trade_size_std = _safe_stats(spent_sols, statistics.stdev) if len([x for x in spent_sols if x is not None]) > 1 else 0.0
    avg_mc_on_buy = _safe_stats(mc_buys, statistics.mean)
    mc_on_buy_std = _safe_stats(mc_buys, statistics.stdev) if len([x for x in mc_buys if x is not None]) > 1 else 0.0
    mc_winners = [mc for mc, pnl in zip(mc_buys, delta_sols) if pnl is not None and pnl > 0]
    mc_losers = [mc for mc, pnl in zip(mc_buys, delta_sols) if pnl is not None and pnl <= 0]
    
    pnl_by_token = {}
    for token, pnl in zip(tokens, delta_sols):
        if token is not None and pnl is not None:
            pnl_by_token[token] = pnl_by_token.get(token, 0) + pnl
    total_pnl = sum(pnl for pnl in pnl_by_token.values() if pnl > 0)
    top_token_pnl = sum(sorted([pnl for pnl in pnl_by_token.values() if pnl > 0], reverse=True)[:1])

    sizing_and_market_profile = {
        "avg_trade_size_sol": avg_trade_size,
        "median_trade_size_sol": _safe_stats(spent_sols, statistics.median),
        "trade_size_coeff_of_variation": _safe_divide(trade_size_std, avg_trade_size),
        "avg_mc_on_buy": avg_mc_on_buy,
        "median_mc_on_buy": _safe_stats(mc_buys, statistics.median),
        "mc_on_buy_coeff_of_variation": _safe_divide(mc_on_buy_std, avg_mc_on_buy),
        "avg_mc_on_buy_winners": _safe_stats(mc_winners, statistics.mean),
        "avg_mc_on_buy_losers": _safe_stats(mc_losers, statistics.mean),
        "token_concentration_pnl_percent": _safe_divide(top_token_pnl * 100, total_pnl)
    }
    
    total_trades = info.get('Tokens', len(trades))
    trading_period_in_seconds = info.get('Trades Period', 0.0)
    trading_period_in_days = _safe_divide(trading_period_in_seconds, 86400) if trading_period_in_seconds else 0.0
    platform_counts = Counter(p for p in platforms if p)
    max_platform_count = max(platform_counts.values()) if platform_counts else 0
    winners_durations = [d for d, pnl in zip(durations_sec, delta_sols) if pnl is not None and pnl > 0]
    losers_durations = [d for d, pnl in zip(durations_sec, delta_sols) if pnl is not None and pnl <= 0]
    avg_hold = _safe_stats(durations_sec, statistics.mean)
    median_hold = _safe_stats(durations_sec, statistics.median)

    timing_and_frequency = {
        "total_trades": total_trades,
        "trading_period_days": trading_period_in_days,
        "trades_per_day": _safe_divide(total_trades, trading_period_in_days) if trading_period_in_days > 0 else total_trades,
        "avg_hold_seconds": avg_hold,
        "median_hold_seconds": median_hold,
        "hold_time_avg_to_median_ratio": _safe_divide(avg_hold, median_hold),
        "avg_hold_winners_seconds": _safe_stats(winners_durations, statistics.mean),
        "avg_hold_losers_seconds": _safe_stats(losers_durations, statistics.mean),
        "platform_concentration_percent": _safe_divide(max_platform_count * 100, total_trades) if total_trades > 0 else 0
    }
    
    return {
        "wallet_address": info.get('Wallet'),
        "performance_and_risk": performance_and_risk,
        "sizing_and_market_profile": sizing_and_market_profile,
        "timing_and_frequency": timing_and_frequency
    }

# --- Main execution flow ---

def parse_workbook(path: str):
    """
    Parses an entire Excel workbook and returns a dictionary of enriched profiles,
    keyed by sheet name.
    """
    try:
        xls = pd.ExcelFile(path)
    except Exception as e:
        print(f"Error reading Excel file {path}: {e}")
        return {}
        
    all_profiles = {}
    for sheet_name in xls.sheet_names:
        try:
            df = xls.parse(sheet_name=sheet_name, header=None, dtype=str)
            raw_data = parse_sheet_to_raw_data(df)
            if not raw_data.get('wallet_info') or not raw_data['wallet_info'].get('Wallet'):
                print(f"Skipping sheet '{sheet_name}' in '{path}' - no wallet info found.")
                continue
            enriched_profile = create_enriched_profile(raw_data)
            all_profiles[sheet_name] = enriched_profile
        except Exception as e:
            print(f"Error processing sheet '{sheet_name}' in file '{path}': {e}")
            continue
    return all_profiles