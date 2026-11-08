import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.services.deepseek_client import complete_json


class ConnectionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]


def main() -> None:
    result = complete_json(
        system_prompt=(
            '这是接口连通性检查。'
            '请严格输出JSON：{"status":"ok"}，'
            '不要添加其他字段。'
        ),
        user_payload={"task": "connection_check"},
        max_tokens=128,
    )

    checked = ConnectionCheck.model_validate(result["data"])

    print("API与JSON校验通过")
    print("结果：", checked.model_dump())
    print("请求模型：", result["requested_model"])
    print("响应模型：", result["reported_model"])
    print("耗时秒数：", round(result["elapsed_seconds"], 3))
    print(
        "Token用量：",
        json.dumps(result["usage"], ensure_ascii=False),
    )


if __name__ == "__main__":
    main()