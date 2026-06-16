import json
import os

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
except ImportError:
    lark = None
    CreateMessageRequest = None
    CreateMessageRequestBody = None

try:
    from .feishu_config import get_feishu_config
except ImportError:
    from feishu_config import get_feishu_config


FEISHU_NO_PROXY_HOSTS = ("open.feishu.cn", ".feishu.cn")


def configure_feishu_network():
    """Keep Feishu API traffic independent from a transient system proxy."""
    entries = []
    for name in ("NO_PROXY", "no_proxy"):
        entries.extend(
            item.strip()
            for item in os.environ.get(name, "").split(",")
            if item.strip()
        )
    for host in FEISHU_NO_PROXY_HOSTS:
        if host not in entries:
            entries.append(host)
    value = ",".join(dict.fromkeys(entries))
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    return value


configure_feishu_network()


class FeishuClient:
    def __init__(self, client=None, config=None):
        self.config = config or get_feishu_config()
        if client is not None:
            self.client = client
        else:
            if lark is None:
                raise RuntimeError("lark-oapi is not installed. Run: pip install lark-oapi")
            if not self.config.bot_ready:
                raise RuntimeError("Missing FEISHU_APP_ID or FEISHU_APP_SECRET.")
            self.client = (
                lark.Client.builder()
                .app_id(self.config.app_id)
                .app_secret(self.config.app_secret)
                .build()
            )

    def send_text(self, receive_id, text, receive_id_type="chat_id"):
        return self._send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="text",
            content={"text": str(text)},
        )

    def send_card(self, receive_id, card, receive_id_type="chat_id"):
        return self._send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="interactive",
            content=card,
        )

    def _send_message(self, receive_id, receive_id_type, msg_type, content):
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(json.dumps(content, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        result = response_result(response)
        result.pop("data", None)
        return result


def response_result(response):
    success = bool(response.success())
    return {
        "success": success,
        "code": int(getattr(response, "code", 0) or 0),
        "message": str(getattr(response, "msg", "") or ""),
        "log_id": str(response.get_log_id() or ""),
        "data": getattr(response, "data", None),
    }
