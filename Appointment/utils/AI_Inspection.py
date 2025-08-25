from Appointment.config import appointment_config as CONFIG

import os
import json
import requests

ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4.5-flash"

# 模型人设：只返回“合规/不合规”，JSON格式
SYSTEM_PROMPT = (
    "你是一个地下室内容审核员。"
    "请判断用户内容是否安全、合规、健康。"
    "你的输出必须严格为JSON，字段为："
    '{"decision":"合规" 或 "不合规", "reason":"一句话理由"}'
)


def _call_glm_decision(text: str, timeout: int = 30) -> tuple[str, str]:
    """
    调用智谱 HTTP 接口，返回 '合规' 或 '不合规'，超时/网络错误报出异常。
    """
    ENDPOINT = os.getenv("ENDPOINT", ENDPOINT)
    MODEL = os.getenv("MODEL", MODEL)
    api_key = os.getenv("ZHIPUAI_API_KEY")
    if not api_key:
        raise RuntimeError("缺少环境变量 ZHIPUAI_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "temperature": 0.2,  # 可调
        "top_p": 0.7,        # 可调
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"待审核文本：\n{text}"},
        ],
    }

    resp = requests.post(ENDPOINT, headers=headers,
                         json=payload, timeout=timeout)
    if resp.status_code != 200:
        # 把上游错误透出一小段，便于排查
        raise RuntimeError(f"Upstream {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    try:
        content_str = data["choices"][0]["message"]["content"]
        obj = json.loads(content_str)
    except Exception as e:
        raise RuntimeError(f"Bad JSON content from provider: {e}; raw={data}")

    decision = obj.get("decision")
    reason = obj.get("reason", "")
    if decision not in ("合规", "不合规"):
        raise ValueError(f"unexpected decision: {decision};obj={obj}")
    if not isinstance(reason, str):
        reason = str(reason)

    return decision, reason


def AI_Inspection(room_name, reason) -> tuple[bool, str]:
    # AI 审核功能接口，将预约房间（名称）和事由发送至 API，然后接收判断结果（合格/不合格）
    # Additional：最好能在不合格时附带理由

    if not CONFIG.AI_Inspection_Enabled:
        # 默认通过
        return True, "AI Inspection is disabled"

    # 在此处实现 API 的调用，并处理 API 调用失败的情况
    # 组装要审核的文本
    content = f"房间：{room_name}\n事由：{reason}"

    # 超时时间可从配置读取，默认30秒
    timeout = getattr(CONFIG, "AI_Inspection_Timeout", 30)
    try:
        decision, why = _call_glm_decision(content, timeout=timeout)
        passed = (decision == "合规")
        # 返回是否通过和理由 True通过，False不通过
        if passed:
            return True, "合规"
        else:
            return False, f"不合规：{why or '无具体理由'}"
    except Exception as e:
        # API 调用失败：不通过并附带错误信息
        return False, f"AI Inspection is not implemented: {e}"
