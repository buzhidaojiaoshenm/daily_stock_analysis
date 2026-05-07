# -*- coding: utf-8 -*-
"""Market review endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.market_review import (
    MarketReviewRunRequest,
    MarketReviewRunResponse,
)
from src.core.market_review import run_market_review
from src.notification import NotificationService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/run",
    response_model=MarketReviewRunResponse,
    responses={
        502: {"description": "Market review returned no report", "model": ErrorResponse},
        500: {"description": "Market review failed", "model": ErrorResponse},
    },
    summary="Run market review",
    description="Run a CN/US/both market review and return the generated Markdown report.",
)
def run_market_review_endpoint(request: MarketReviewRunRequest) -> MarketReviewRunResponse:
    notifier = NotificationService()
    try:
        report = run_market_review(
            notifier=notifier,
            send_notification=request.send_notification,
            override_region=request.region,
        )
    except Exception as exc:
        logger.exception("Market review API failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail={"error": "market_review_failed", "message": str(exc)},
        ) from exc

    if not report:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "market_review_empty",
                "message": "大盘复盘未返回有效报告",
            },
        )

    return MarketReviewRunResponse(
        status="completed",
        region=request.region,
        report=report,
        notification_sent=request.send_notification,
    )

