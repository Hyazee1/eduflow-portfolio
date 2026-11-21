from typing import Any
from urllib.parse import urlparse

import httpx

from eduflow.domain.errors import BusinessError


class CompatibleModelGateway:
    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Invalid model endpoint")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Remote model endpoints require HTTPS")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key

    def respond(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=25, follow_redirects=False) as client:
                response = client.post(
                    self.url,
                    headers={"Authorization": "Bearer " + self.api_key},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": "auto",
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "eduflow_reply",
                                "strict": True,
                                "schema": {
                                    "type": "object",
                                    "properties": {"message": {"type": "string"}},
                                    "required": ["message"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                    },
                )
                response.raise_for_status()
                message = response.json()["choices"][0]["message"]
                return {k: message[k] for k in ("content", "tool_calls") if k in message}
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as error:
            raise BusinessError("在线模型接口不可用；请检查配置，或清空模型配置使用离线模式") from error
