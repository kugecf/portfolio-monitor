import os
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
SCKEY  = os.getenv("SCKEY")

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

    # 安全提取为标量
    latest_close = acwi["Close"].iloc[-1].item()
    latest_ma    = acwi["ma200"].iloc[-1].item()

    # 转为 numpy 数组避免对齐问题
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
    raise


# =========================
# 2. 信用利差
# =========================

try:
    fred = Fred(api_key=FRED_API_KEY)

    end_date = datetime.today()

    hy_data = fred.get_series(
        HY_OAS_CODE,
        observation_start=end_date - timedelta(days=180)
    ).dropna()

    hy_current = hy_data.iloc[-1].item()
    hy_p75     = hy_data.quantile(HY_75).item()
    hy_p90     = hy_data.quantile(HY_90).item()

    hy_percent = round(
        (hy_data < hy_current).mean() * 100,
        1
    )

except Exception as e:
    send_wechat("❌ 数据失败", f"FRED 获取异常：{e}")
    raise


# =========================
# 3. VIX 恐慌指数
# =========================

try:
    vix = yf.download(
        VIX_CODE,
        period="10d",
        auto_adjust=True,
        progress=False
    )["Close"].dropna()

    vix_current = vix.iloc[-1].item()
    vix_prev    = vix.iloc[-2].item()

    vix_change = round((vix_current - vix_prev) / vix_prev, 3)

except Exception:
    vix_current = 0.0
    vix_change  = 0.0


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

if vix_current > VIX_LIMIT and vix_change >= VIX_RISE:
    defense_reasons.append(
        f"VIX={vix_current}，单日上涨 {vix_change*100:.0f}%"
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
# 6. 推送内容
# =========================

content = f"""
**{mode}**

日期：{today}

━━━━━━━━━━
📊 核心信号
━━━━━━━━━━

ACWI：{latest_close}
200日均线：{latest_ma}

状态：
{'站上' if trend_status else '跌破'} {trend_days} 天

HY OAS：
当前 {hy_current}%

历史分位：
{hy_percent}%

VIX：
{vix_current}

单日变化：
{vix_change*100:.1f}%

━━━━━━━━━━
💼 当前目标仓位
━━━━━━━━━━

{position}
"""

if defense_reasons:
    content += "\n\n⚠️ 防守触发原因：\n"
    for x in defense_reasons:
        content += f"\n• {x}"

send_wechat(title, content)

print(f"✅ 当前状态：{mode}")
