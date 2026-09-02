from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from topgun_myday.resources import resource_path, resource_root


class ResourcePathTests(unittest.TestCase):
    def test_source_tree_resources_are_resolved_from_project_root(self) -> None:
        expected_root = Path(__file__).resolve().parent.parent
        self.assertEqual(resource_root(), expected_root)
        self.assertEqual(
            resource_path("config", "business_rules.json"),
            expected_root / "config" / "business_rules.json",
        )

    def test_pyinstaller_resources_are_resolved_from_bundle_directory(self) -> None:
        with patch.object(sys, "_MEIPASS", "C:/temporary/TOPGUN.bundle", create=True):
            self.assertEqual(resource_root(), Path("C:/temporary/TOPGUN.bundle"))
            self.assertEqual(
                resource_path("data", "demo_topgun_v02.xlsx"),
                Path("C:/temporary/TOPGUN.bundle/data/demo_topgun_v02.xlsx"),
            )


if __name__ == "__main__":
    unittest.main()
