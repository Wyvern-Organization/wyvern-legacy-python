from typing import Any


def success_response(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def error_response(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
    }
