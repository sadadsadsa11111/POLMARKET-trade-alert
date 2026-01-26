import requests
import time
import json
import os
from typing import Dict, Any, List
from datetime import datetime, timedelta

# ================== 配置区 ==================

API_URL = "https://data-api.polymarket.com/positions"
USER_ADDRESS = ""

FEISHU_WEBHOOK = "b"

STATE_FILE = "state.json"
POLL_INTERVAL = 30  # 秒

# 🎯 变化容忍阈值：小于此值的变化将被忽略
CHANGE_THRESHOLD = 0.01  # 可根据实际情况调整，例如 0.01 = 1% 或 0.01 个单位

# 🔁 重试配置
MAX_RETRY_DURATION = 60  # 最大重试时长（秒）
RETRY_INTERVAL = 10  # 每次重试间隔（秒）

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
    try:
        r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"飞书推送失败: {e}")

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
        outcome = p.get("outcome", "")
        size = float(p["size"])
        
        # 🔑 使用复合键区分同一市场的不同仓位
        key = f"{cid}:{outcome}"
        
        # 忽略已清仓的仓位
        if size == 0:
            continue

        title = p.get("title", "")
        avg_price = p.get("avgPrice")
        cur_price = p.get("curPrice")
        percent_pnl = p.get("percentPnl")

        new_state[key] = {
            "size": size,
            "avgPrice": avg_price,
            "title": title,
            "outcome": outcome
        }

        old = old_state.get(key)  # 用复合键查找

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
    
    # 检测已清仓的仓位
    for key, old_data in old_state.items():
        if key not in new_state:
            old_size = float(old_data["size"])
            if old_size > 0:
                alerts.append(
                    f"【清仓】\n"
                    f"{old_data['title']} - {old_data['outcome']}\n"
                    f"{old_size:.4f} → 0"
                )

    return alerts, new_state

# ================== 带重试的API调用 ==================

def fetch_positions_with_retry() -> List[Dict[str, Any]]:
    """
    带重试机制的仓位获取
    
    Returns:
        成功则返回仓位列表，超过重试时间则抛出异常
    """
    start_time = datetime.now()
    retry_count = 0
    
    while True:
        try:
            return fetch_positions()
        
        except Exception as e:
            elapsed = (datetime.now() - start_time).total_seconds()
            retry_count += 1
            
            error_msg = str(e)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"API调用失败 (第{retry_count}次): {error_msg}")
            
            # 检查是否超过最大重试时长
            if elapsed >= MAX_RETRY_DURATION:
                raise Exception(
                    f"连续 {MAX_RETRY_DURATION} 秒无法获取数据，程序停止。"
                    f"最后错误: {error_msg}"
                )
            
            # 等待后重试
            remaining = MAX_RETRY_DURATION - elapsed
            wait_time = min(RETRY_INTERVAL, remaining)
            print(f"等待 {wait_time:.0f} 秒后重试... (剩余重试时间: {remaining:.0f}秒)")
            time.sleep(wait_time)

# ================== 主循环 ==================

def main():
    old_state = load_state()
    send_feishu(
        f"✅ Polymarket 仓位监控 Bot 已启动\n"
        f"阈值设置：{CHANGE_THRESHOLD}\n"
        f"重试配置：{MAX_RETRY_DURATION}秒超时"
    )

    while True:
        try:
            positions = fetch_positions_with_retry()
            alerts, new_state = detect_position_changes(old_state, positions)

            for msg in alerts:
                send_feishu(msg)

            save_state(new_state)
            old_state = new_state
            
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 监控正常")

        except Exception as e:
            error_msg = f"❌ 程序停止运行\n错误: {e}"
            print(error_msg)
            send_feishu(error_msg)
            break  # 停止程序

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()