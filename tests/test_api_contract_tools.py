# -*- coding: utf-8 -*-
"""Regression tests for OpenAPI contract export and generated Web types."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.api_contract import build_contract_artifacts


class ApiContractToolsTestCase(unittest.TestCase):
    def test_build_contract_artifacts_exports_openapi_and_web_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = build_contract_artifacts(root)
            api_spec = json.loads((root / "docs/architecture/api_spec.json").read_text())
            web_types = (root / "apps/dsa-web/src/types/openapi.generated.ts").read_text()

        self.assertIn("/api/v1/analysis/analyze", api_spec["paths"])
        self.assertIn("export interface TaskInfo", web_types)
        self.assertIn("status:", web_types)
        self.assertIn('"cancelled"', web_types)

    def test_generated_artifacts_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = build_contract_artifacts(root)
            second = build_contract_artifacts(root)

        self.assertEqual(first.openapi_json, second.openapi_json)
        self.assertEqual(first.web_types_ts, second.web_types_ts)
