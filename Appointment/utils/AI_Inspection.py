from Appointment.config import appointment_config as CONFIG
import requests

def AI_Inspection(room_name, reason) -> tuple[bool, str]:
    # AI 审核功能接口，将预约房间（名称）和事由发送至 API，然后接收判断结果（合格/不合格）
    # Additional：最好能在不合格时附带理由

    if not CONFIG.AI_Inspection_Enabled:
        # 默认通过
        return True, "AI Inspection is disabled"

    # 暂行处理方法
    # TODO: 在此处实现 API 的调用，并处理 API 调用失败的情况
    return Ollama_Inspection(room_name, reason)


def Ollama_Inspection(room_name, reason) -> tuple[bool, str]:
    # Ollama 审核功能接口，将预约房间（名称）和事由发送至 API，然后接收判断结果（合格/不合格）
    # 完整的处理办法（需要在API调用失败时抛出错误）
    try:
        response = requests.post(
            CONFIG.AI_Inspection_API + "/api/generate",
            json={
                "model": "gpt-oss:20b",
                "prompt": f"你是一个审查学生预约功能房间信息的老师，请审核以下预约信息，审核要求是预约事由和房间用途匹配，且不能包含政治敏感信息和色情内容。房间名称：{room_name}。事由：{reason}。如果审核通过，只输出 1，不通过，则输出 0，然后接一个空格，在空格后输出不通过的原因，原因尽量简要；如无法判断，默认不通过",
                "stream": False,
                "think": False
            }
        )
        response.raise_for_status()  # 如果响应状态码不是 200，将抛出异常
        print(response)
        result = response.json()
        print(result)
        # 调用完成后通过 API 发送卸载模型的命令
        requests.delete(
            CONFIG.AI_Inspection_API + "/model/gpt-oss:20b"
        )
        # 根据上述格式解析 response 部分，给出返回值
        output = result.get("response", "").strip()
        if output.startswith("1"):
            return True, "Approved"
        elif output.startswith("0"):
            reason = output[1:].strip()  # 获取不通过的原因
            return False, reason if reason else "Not approved"
        else:
            return False, "Unexpected response format"

    except requests.RequestException as e:
        # 处理请求异常
        return False, str(e)
