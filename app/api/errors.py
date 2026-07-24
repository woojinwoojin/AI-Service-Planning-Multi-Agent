"""통일 오류 응답 형식 (로드맵 Phase 5).

모든 오류를 동일한 봉투(envelope)로 반환해 클라이언트가 일관되게 처리하도록 한다.

    {"error": {"code": "not_found", "message": "...", "status": 404, "details": [...]?}}

- `code`: 기계 판독용 안정 코드(HTTP status 에서 매핑). UI 분기·로깅 키.
- `message`: 사람이 읽는 안내(한국어).
- `status`: HTTP status code(본문에도 실어 로깅·표시 편의).
- `details`: (선택) 검증 오류의 필드별 사유.

세 가지 예외를 처리한다: HTTPException(명시적 4xx), RequestValidationError(422 입력 검증),
그 외 Exception(500 — 내부 상세는 응답에 노출하지 않고 일반 메시지만).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

_log = logging.getLogger("app.errors")

# HTTP status → 안정 코드. 목록에 없으면 4xx=error, 5xx=internal_error 로 폴백.
_CODE_BY_STATUS: dict[int, str] = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    405: "method_not_allowed", 409: "conflict", 413: "payload_too_large",
    422: "validation_error", 429: "rate_limited",
    500: "internal_error", 502: "bad_gateway", 503: "service_unavailable", 504: "timeout",
}


class ErrorBody(BaseModel):
    code: str = Field(..., description="기계 판독용 오류 코드")
    message: str = Field(..., description="사람이 읽는 오류 메시지")
    status: int = Field(..., description="HTTP status code")
    details: list[dict] | None = Field(None, description="(선택) 검증 오류 필드별 사유")


class ErrorResponse(BaseModel):
    """통일 오류 응답 스키마(OpenAPI 문서화용)."""
    error: ErrorBody


def _code_for(status: int) -> str:
    return _CODE_BY_STATUS.get(status, "internal_error" if status >= 500 else "error")


def error_payload(status: int, message: str, code: str | None = None,
                  details: list[dict] | None = None) -> dict:
    """통일 오류 봉투 dict 를 만든다(핸들러·직접 호출 공통)."""
    body: dict = {"code": code or _code_for(status), "message": message, "status": status}
    if details:
        body["details"] = details
    return {"error": body}


def register_error_handlers(app: FastAPI) -> None:
    """FastAPI 앱에 통일 오류 핸들러 3종을 등록한다(main.py 에서 1회 호출)."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # FastAPI HTTPException 은 Starlette 의 하위 클래스라 이 핸들러가 둘 다 처리한다.
        message = exc.detail if isinstance(exc.detail, str) else "요청을 처리할 수 없습니다."
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.status_code, message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in e.get("loc", [])),
                    "message": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(
            status_code=422,
            content=error_payload(422, "입력 형식이 올바르지 않습니다.", details=details),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc(request: Request, exc: Exception) -> JSONResponse:
        # 내부 오류 상세는 로그로만 남기고(정보 누출 방지), 응답엔 일반 메시지만 준다.
        _log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_payload(500, "서버 내부 오류가 발생했습니다."),
        )
