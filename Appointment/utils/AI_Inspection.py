from Appointment.config import appointment_config as CONFIG


def AI_Inspection(room_name, reason) -> tuple[bool, str]:
    # AI 审核功能接口，将预约房间（名称）和事由发送至 API，然后接收判断结果（合格/不合格）
    # Additional：最好能在不合格时附带理由

    if not CONFIG.AI_Inspection_Enabled:
        # 默认通过
        return True, "AI Inspection is disabled"

    # 暂行处理方法
    # TODO: 在此处实现 API 的调用，并处理 API 调用失败的情况
    return False, "AI Inspection is not implemented"
