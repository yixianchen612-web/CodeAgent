"""规格摘要器测试（真实输入文档，缺失时跳过）。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codeagent.preprocessor import preprocess  # noqa: E402
from codeagent.spec_digest import MAX_CHARS, build_digest  # noqa: E402

TASK_DIR = ROOT / "input"
if not TASK_DIR.is_dir():
    TASK_DIR = ROOT.parent / "Task (1)"  # compatibility with the original checkout
DOC_PATH = TASK_DIR / "Architecture_Documentation.md"
VIEWS_PATH = TASK_DIR / "Architecture_View.md"


@unittest.skipUnless(DOC_PATH.exists() and VIEWS_PATH.exists(),
                     "未找到任务输入文档，跳过")
class TestSpecDigest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = preprocess(DOC_PATH, VIEWS_PATH)
        cls.digest = build_digest(cls.spec)

    def test_contains_core_info(self):
        d = self.digest
        self.assertEqual(d["executive_summary"]["architectural_style"], "Microservices")
        self.assertEqual(len(d["traceability"]), 3)
        self.assertEqual(len(d["diagrams"]), 13)
        comps = {c["name"] for c in d["components"]}
        self.assertEqual(comps, {"GameComponent", "QuestionComponent", "UserComponent"})
        self.assertIn("openapi.yaml", d["deliverables"])

    def test_keeps_api_contracts(self):
        det = self.digest["component_details"][0]
        blocks = [cb["language"]
                  for sub in det["subsections"].values()
                  for cb in sub["code_blocks"]]
        self.assertIn("yml", blocks)   # OpenAPI
        self.assertIn("proto", blocks)  # 内部契约
        self.assertIn("sql", blocks)    # DDL

    def test_relations_present_but_capped(self):
        for d in self.digest["diagrams"]:
            self.assertLessEqual(len(d["relations"]), 12)

    def test_size_bound(self):
        text = json.dumps(self.digest, ensure_ascii=False)
        self.assertLessEqual(len(text), MAX_CHARS + 2000)  # 截断兜底后再超则失败

    def test_analysis_included(self):
        self.assertIn("gaps", self.digest["analysis"])
        self.assertGreater(len(self.digest["analysis"]["gaps"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
