from typing import Any


def build_response(data: Any, status: str = "ok") -> dict[str, Any]:
    return {"status": status, "data": data}
