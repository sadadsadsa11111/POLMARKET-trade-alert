import requests
import time
import json
import os
from typing import Dict, Any, List

# ================== 配置区 ==================

API_URL = "https://data-api.polymarket.com/positions"
USER_ADDRESS = "" #POLYMARKET钱包地址

FEISHU_WEBHOOK = ""

STATE_FILE = "state.json"
POLL_INTERVAL = 30  # 秒

# 🎯 变化容忍阈值：小于此值的变化将被忽略
CHANGE_THRESHOLD = 0.01  # 可根据实际情况调整，例如 0.01 = 1% 或 0.01 个单位

# ================== API ==================

def fetch_positions() -> List[Dict[str, Any]]:
    params = {
        "user": USER_ADDRESS,
        "limit": 100,
        "sizeThreshold": 1
    }
    r = requests.get(API_URL, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

# ================== 状态读写 ==================

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state: Dict[str, Any]):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ================== 飞书推送 ==================

def send_feishu(text: str):
    payload = {
        "msg_type": "text",
        "content": {
            "text": text
        }
    }
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
    r.raise_for_status()

# ================== 判断数值变化是否显著 ==================

def is_significant_change(old_size: float, new_size: float, threshold: float = CHANGE_THRESHOLD) -> bool:
    """
    判断仓位变化是否显著（超过阈值）
    
    Args:
        old_size: 旧仓位大小
        new_size: 新仓位大小
        threshold: 容忍阈值
    
    Returns:
        True 表示变化显著，False 表示可能是浮点噪音
    """
    diff = abs(new_size - old_size)
    
    # 方案1: 绝对差值判断（适合固定单位的变化）
    if diff < threshold:
        return False
    
    # 方案2: 相对变化判断（可选，适合百分比变化）
    # if old_size > 0:
    #     relative_change = diff / old_size
    #     if relative_change < threshold:  # threshold 可设为 0.01 即 1%
    #         return False
    
    return True

# ================== 仓位变化检测 ==================

def detect_position_changes(
    old_state: Dict[str, Any],
    new_positions: List[Dict[str, Any]]
):
    alerts = []
    new_state = {}

    for p in new_positions:
        cid = p["conditionId"]
        size = float(p["size"])
        
        # 🚫 忽略已经清仓的历史仓位
        if size == 0:
            continue

        title = p.get("title", "")
        outcome = p.get("outcome", "")
        avg_price = p.get("avgPrice")
        cur_price = p.get("curPrice")
        percent_pnl = p.get("percentPnl")

        new_state[cid] = {
            "size": size,
            "avgPrice": avg_price,
            "title": title,
            "outcome": outcome
        }

        old = old_state.get(cid)

        # 新开仓
        if old is None and size > 0:
            alerts.append(
                f"【新开仓】\n"
                f"{title} - {outcome}\n"
                f"数量：{size}\n"
                f"均价：{avg_price}\n"
                f"现价：{cur_price}"
            )

        elif old:
            old_size = float(old["size"])
            
            # 🎯 过滤浮点精度抖动：只有显著变化才触发提醒
            if not is_significant_change(old_size, size):
                continue

            if size > old_size:
                alerts.append(
                    f"【加仓】\n"
                    f"{title} - {outcome}\n"
                    f"{old_size:.4f} → {size:.4f} (+{size - old_size:.4f})\n"
                    f"均价：{avg_price}\n"
                    f"PNL：{percent_pnl}%"
                )

            elif 0 < size < old_size:
                alerts.append(
                    f"【减仓】\n"
                    f"{title} - {outcome}\n"
                    f"{old_size:.4f} → {size:.4f} (-{old_size - size:.4f})\n"
                    f"现价：{cur_price}\n"
                    f"PNL：{percent_pnl}%"
                )

            elif size == 0 and old_size > 0:
                alerts.append(
                    f"【清仓】\n"
                    f"{title} - {outcome}\n"
                    f"{old_size:.4f} → 0"
                )
    
    # 🔍 检测已清仓的仓位（从 old_state 中消失）
    for cid, old_data in old_state.items():
        if cid not in new_state:
            old_size = float(old_data["size"])
            if old_size > 0:
                alerts.append(
                    f"【清仓】\n"
                    f"{old_data['title']} - {old_data['outcome']}\n"
                    f"{old_size:.4f} → 0"
                )

    return alerts, new_state

# ================== 主循环 ==================

def main():
    old_state = load_state()
    send_feishu(f"✅ Polymarket 仓位监控 Bot 已启动\n阈值设置：{CHANGE_THRESHOLD}")

    while True:
        try:
            positions = fetch_positions()
            alerts, new_state = detect_position_changes(old_state, positions)

            for msg in alerts:
                send_feishu(msg)

            save_state(new_state)
            old_state = new_state

        except Exception as e:
            send_feishu(f"⚠️ 仓位监控异常：{e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":

    main()
