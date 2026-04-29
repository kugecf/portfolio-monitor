import os
import sys
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from fredapi import Fred
from datetime import datetime, timedelta

# =========================
# 固定参数（终版）
# =========================

FRED_API_KEY = os.getenv("FRED_API_KEY")
SCKEY        = os.getenv("SCKEY")

ACWI_CODE   = "ACWI"
VIX_CODE    = "^VIX"
HY_OAS_CODE = "BAMLH0A0HYM2"

MA_DAYS          = 200
TREND_CHECK_DAYS = 20

HY_75 = 0.75
HY_90 = 0.90

VIX_LIMIT = 35
VIX_RISE  = 0.20


# =========================
# 交易日检测（跳过周末和美股节假日）
# =========================

def is_market_closed_today():
    """
    判断今天是否是美股交易日。
    方法：获取 ACWI 最近 1 天的收盘价，如果最近一个交易日的日期不等于今天，
    则说明今天是非交易日（周末或假日），应当跳过推送。
    """
    try:
        test = yf.download(ACWI_CODE, period="1d", auto_adjust=True, progress=False)
        if test.empty:
            return True  # 无数据，视为休市
        last_trade_date = test.index[-1].date()  # 最近一个交易日的日期
        today = datetime.today().date()
        return last_trade_date != today
    except Exception:
        # 异常时保守起见，不发推送
        return True


# =========================
# 微信推送
# =========================

def send_wechat(title, content):
    try:
        url = f"https://sctapi.ftqq.com/{SCKEY}.send"
        requests.post(
            url,
            data={"title": title, "desp": content},
            timeout=10
        )
        print("✅ 微信推送成功")
    except Exception as e:
        print(f"❌ 微信推送失败：{e}")


# ======================
# 主程序入口
# ======================

if __name__ == "__main__":
    # 第一步：交易日检查，非交易日直接退出，不消耗推送额度
    if is_market_closed_today():
        print("ℹ️ 今日非美股交易日，跳过推送")
        sys.exit(0)

    # ---------- 以下逻辑只在交易日执行 ----------

    # =========================
    # 1. ACWI 趋势判断
    # =========================
    try:
        acwi = yf.download(
            ACWI_CODE,
            period="1y",
            auto_adjust=True,
            progress=False
        )

        acwi["ma200"] = acwi["Close"].rolling(MA_DAYS).mean()

        latest_close = acwi["Close"].iloc[-1].item()
        latest_ma    = acwi["ma200"].iloc[-1].item()

        close_vals = acwi["Close"].values.ravel()
        ma_vals    = acwi["ma200"].values.ravel()
        above_ma   = close_vals > ma_vals

        trend_status = bool(above_ma[-1])

        trend_days = 0
        for val in reversed(above_ma):
            if val == trend_status:
                trend_days += 1
            else:
                break

    except Exception as e:
        send_wechat("❌ 数据失败", f"ACWI 获取异常：{e}")
        sys.exit(1)

    # =========================
    # 2. 信用利差（增加最后更新日期）
    # =========================
    hy_last_date = None
    hy_data_lag = None
    try:
        fred = Fred(api_key=FRED_API_KEY)

        end_date = datetime.today()

        hy_data = fred.get_series(
            HY_OAS_CODE,
            observation_start=end_date - timedelta(days=180)
        ).dropna()

        hy_current = hy_data.iloc[-1].item()
        hy_last_date = hy_data.index[-1].date()       # 数据最新日期
        hy_data_lag = (datetime.today().date() - hy_last_date).days

        hy_p75     = hy_data.quantile(HY_75).item()
        hy_p90     = hy_data.quantile(HY_90).item()

        hy_percent = round(
            (hy_data < hy_current).mean() * 100,
            1
        )

    except Exception as e:
        send_wechat("❌ 数据失败", f"FRED 获取异常：{e}")
        sys.exit(1)

    # =========================
    # 3. VIX 恐慌指数（加强健壮性）
    # =========================
    vix_current = None
    vix_change  = None
    vix_date_str = ""
    try:
        vix = yf.download(
            VIX_CODE,
            period="10d",
            auto_adjust=True,
            progress=False
        )["Close"].dropna()

        if len(vix) < 2:
            print("⚠️ VIX数据不足2个交易日，跳过计算")
        else:
            vix_current = vix.iloc[-1].item()
            vix_prev    = vix.iloc[-2].item()
            vix_date    = vix.index[-1].date()
            vix_date_str = vix_date.strftime("%Y-%m-%d")
            if vix_prev != 0:
                vix_change = round((vix_current - vix_prev) / vix_prev, 3)
            else:
                vix_change = None
    except Exception as e:
        print(f"⚠️ VIX 获取异常：{e}")

    # =========================
    # 4. 状态机判断
    # =========================
    defense_reasons = []

    if (not trend_status) and trend_days >= TREND_CHECK_DAYS:
        defense_reasons.append(
            f"ACWI连续跌破200日均线 {trend_days} 天"
        )

    if hy_percent > 90:
        defense_reasons.append(
            f"信用利差分位 {hy_percent}%（超90%）"
        )

    if (vix_current is not None and vix_change is not None
            and vix_current > VIX_LIMIT and vix_change >= VIX_RISE):
        defense_reasons.append(
            f"VIX={vix_current:.2f}，单日上涨 {vix_change*100:.0f}%"
        )

    # =========================
    # 5. 仓位模式
    # =========================
    today = datetime.today().strftime("%Y-%m-%d")

    if defense_reasons:
        mode     = "🔴 防守"
        title    = f"⚠️ 防守模式触发 | {today}"
        position = "权益30% ｜ 债券50% ｜ 黄金20%"

    elif trend_status and trend_days >= TREND_CHECK_DAYS and hy_percent < 75:
        mode     = "🟢 进攻"
        title    = f"📈 进攻模式 | {today}"
        position = "权益70% ｜ 债券20% ｜ 黄金10%"

    else:
        mode     = "🟡 中性"
        title    = f"📊 中性模式 | {today}"
        position = "权益60% ｜ 债券30% ｜ 黄金10%"

    # =========================
    # 6. 推送内容（增加数据源链接）
    # =========================
    # 格式化VIX显示
    vix_display = f"{vix_current:.2f}" if vix_current is not None else "获取失败"
    vix_change_display = f"{vix_change*100:.1f}%" if vix_change is not None else "获取失败"
    vix_date_line = f"（日期: {vix_date_str}）" if vix_date_str else ""

    hy_lag_info = ""
    if hy_last_date is not None:
        hy_lag_info = f"数据更新至 {hy_last_date}（滞后 {hy_data_lag} 天）"

    content = f"""
**{mode}**

日期：{today}

━━━━━━━━━━
📊 核心信号
━━━━━━━━━━

ACWI：{latest_close:.2f}
200日均线：{latest_ma:.2f}

状态：
{'站上' if trend_status else '跌破'} {trend_days} 天

HY OAS：
当前 {hy_current:.2f}%
{hy_lag_info}

历史分位：
{hy_percent}%

VIX：
{vix_display} {vix_date_line}

单日变化：
{vix_change_display}

━━━━━━━━━━
💼 当前目标仓位
━━━━━━━━━━

{position}
"""

    if defense_reasons:
        content += "\n\n⚠️ 防守触发原因：\n"
        for x in defense_reasons:
            content += f"\n• {x}"

    # 数据源引用（方便人工核实）
    content += """

━━━━━━━━━━
📎 数据源（点击核实）
━━━━━━━━━━

• ACWI：https://finance.yahoo.com/quote/ACWI/
• VIX：https://finance.yahoo.com/quote/%5EVIX/
• HY OAS（FRED）：https://fred.stlouisfed.org/series/BAMLH0A0HYM2
"""

    send_wechat(title, content)
    print(f"✅ 当前状态：{mode}")
