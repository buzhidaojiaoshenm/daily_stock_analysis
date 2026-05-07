# -*- coding: utf-8 -*-
"""Tests for market review API endpoint helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from api.v1.router import router as api_v1_router
from api.v1.schemas.market_review import MarketReviewRunRequest
from api.v1.endpoints.market_review import run_market_review_endpoint


class MarketReviewApiTestCase(unittest.TestCase):
    def test_run_market_review_returns_report_without_sending_notification_by_default(self) -> None:
        notifier = MagicMock()
        notifier.save_report_to_file.return_value = "/tmp/market_review.md"
        request = MarketReviewRunRequest()

        with patch("api.v1.endpoints.market_review.NotificationService", return_value=notifier), \
             patch("api.v1.endpoints.market_review.run_market_review", return_value="## 大盘复盘\n\n内容") as run_review:
            response = run_market_review_endpoint(request)

        self.assertEqual(response.status, "completed")
        self.assertEqual(response.region, "cn")
        self.assertEqual(response.report, "## 大盘复盘\n\n内容")
        self.assertFalse(response.notification_sent)
        run_review.assert_called_once()
        self.assertIs(run_review.call_args.kwargs["notifier"], notifier)
        self.assertFalse(run_review.call_args.kwargs["send_notification"])
        self.assertEqual(run_review.call_args.kwargs["override_region"], "cn")

    def test_run_market_review_can_request_notification(self) -> None:
        request = MarketReviewRunRequest(send_notification=True, region="us")

        with patch("api.v1.endpoints.market_review.NotificationService"), \
             patch("api.v1.endpoints.market_review.run_market_review", return_value="US report") as run_review:
            response = run_market_review_endpoint(request)

        self.assertEqual(response.region, "us")
        self.assertTrue(response.notification_sent)
        self.assertTrue(run_review.call_args.kwargs["send_notification"])
        self.assertEqual(run_review.call_args.kwargs["override_region"], "us")

    def test_run_market_review_empty_result_returns_502(self) -> None:
        request = MarketReviewRunRequest()

        with patch("api.v1.endpoints.market_review.NotificationService"), \
             patch("api.v1.endpoints.market_review.run_market_review", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                run_market_review_endpoint(request)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.detail["error"], "market_review_empty")

    def test_run_market_review_rejects_invalid_region(self) -> None:
        with self.assertRaises(ValidationError):
            MarketReviewRunRequest(region="bad")

    def test_market_review_route_is_registered(self) -> None:
        paths = {route.path for route in api_v1_router.routes}

        self.assertIn("/api/v1/market-review/run", paths)
