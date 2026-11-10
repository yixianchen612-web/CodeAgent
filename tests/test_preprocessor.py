"""第 3 步解析器的单元测试。

真实文件测试读取仓库 input/ 下的任务输入文档；
文档缺失时自动跳过。运行方式（在 CodeAgent 目录下）:

    py -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codeagent.preprocessor import (  # noqa: E402
    _parse_plantuml,
    _parse_table_rows,
    parse_documentation_md,
    preprocess,
)

TASK_DIR = ROOT / "input"
if not TASK_DIR.is_dir():
    TASK_DIR = ROOT.parent / "Task (1)"  # compatibility with the original checkout
DOC_PATH = TASK_DIR / "Architecture_Documentation.md"
VIEWS_PATH = TASK_DIR / "Architecture_View.md"


# ---------------------------------------------------------------------------
# 合成用例（不依赖真实文件）
# ---------------------------------------------------------------------------

class TestTableParser(unittest.TestCase):
    def test_simple_table(self):
        t = _parse_table_rows(["| A | B |", "| --- | --- |", "| 1 | 2 |"])
        assert t is not None
        self.assertEqual(t.headers, ["A", "B"])
        self.assertEqual(t.rows, [["1", "2"]])


class TestPlantUmlParser(unittest.TestCase):
    def test_class_diagram(self):
        raw = """@startuml T
class Game {
  - id: string
  + play(): void
}
Game --* Question
note right of Game: hello
@enduml"""
        d = _parse_plantuml(raw, "LogicView", "Class Diagram")
        self.assertEqual(d.name, "T")
        self.assertEqual(len(d.elements["classes"]), 1)
        self.assertEqual(len(d.elements["classes"][0]["members"]), 2)
        self.assertEqual(d.elements["classes"][0]["members"][1]["type"], "void")
        self.assertEqual(d.relations, [{"from": "Game", "to": "Question",
                                        "arrow": "--*", "label": ""}])
        self.assertEqual(d.notes, ["note right of Game: hello"])

    def test_usecase_diagram(self):
        raw = """@startuml U
actor EndUser as "End User"
usecase "Play Game" as (PlayGame)
EndUser -- (PlayGame)
@enduml"""
        d = _parse_plantuml(raw, "ScenarioView", "Use Case Diagram")
        self.assertEqual(d.elements["actors"][0]["label"], "End User")
        self.assertEqual(d.elements["usecases"][0]["name"], "PlayGame")
        self.assertEqual(d.relations[0]["to"], "PlayGame")

    def test_activity_flow(self):
        raw = """@startuml F
start
:play game;
if (game over?) then (yes)
  :game over;
else (no)
  :continue playing;
endif
stop
@enduml"""
        d = _parse_plantuml(raw, "", "")
        kinds = [f["kind"] for f in d.flow]
        self.assertEqual(kinds, ["start", "action", "if", "action", "else",
                                 "action", "endif", "stop"])


# ---------------------------------------------------------------------------
# 真实输入文档测试
# ---------------------------------------------------------------------------

@unittest.skipUnless(DOC_PATH.exists() and VIEWS_PATH.exists(),
                     "未找到任务输入文档，跳过真实文件测试")
class TestRealInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = preprocess(DOC_PATH, VIEWS_PATH)
        cls.doc = cls.result["documentation"]
        cls.views = cls.result["views"]
        cls.analysis = cls.result["analysis"]

    # -- 文档结构 --

    def test_sections(self):
        ids = {s["id"] for s in self.doc["sections"]}
        self.assertTrue({"A", "B", "C", "D", "E", "F", "G", "H", "I", "J",
                         "K", "L"} <= ids)

    def test_executive_summary(self):
        es = self.doc["executive_summary"]
        self.assertEqual(es["architectural_style"], "Microservices")
        self.assertEqual(es["deployment_topology"], "Cloud-based infrastructure")
        self.assertEqual(len(es["risks"]), 3)
        self.assertEqual(len(es["qa_coverage"]), 3)

    def test_traceability(self):
        tr = self.doc["traceability"]
        self.assertEqual({r["id"] for r in tr}, {"FR-1", "NFR-1", "ASR-1"})
        self.assertTrue(all(r["diagrams"] and r["components"] and r["artifacts"]
                            for r in tr))

    def test_components_and_details(self):
        self.assertEqual({c["name"] for c in self.doc["components"]},
                         {"GameComponent", "QuestionComponent", "UserComponent"})
        self.assertEqual(len(self.doc["component_details"]), 1)
        det = self.doc["component_details"][0]
        self.assertEqual(det["name"], "GameComponent")
        self.assertIn("Node.js 18-20", det["technology_options"]["Language/runtime"])

    def test_deliverables(self):
        self.assertIn("openapi.yaml", self.doc["deliverables"])
        self.assertIn("traceability_matrix.csv", self.doc["deliverables"])

    # -- 视图结构 --

    def test_all_diagrams_parsed(self):
        names = [d["name"] for d in self.views["diagrams"]]
        # 任务书编号 1-11，实际文件含 13 个 @startuml 块（时序/协作各 2 张）
        self.assertEqual(len(names), 13)
        for expected in ["UseCaseDiagram", "ClassDiagram", "ObjectDiagram",
                         "StateDiagram", "ActivityDiagram", "SequenceDiagram1",
                         "SequenceDiagram2", "CollaborationDiagram1",
                         "CollaborationDiagram2", "PackageDiagram",
                         "ComponentDiagram", "DeploymentDiagram",
                         "ContainerDiagram"]:
            self.assertIn(expected, names)

    def test_usecase_diagram_content(self):
        d = next(d for d in self.views["diagrams"] if d["name"] == "UseCaseDiagram")
        self.assertEqual(len(d["elements"]["actors"]), 2)
        self.assertEqual(len(d["elements"]["usecases"]), 4)
        # EndUser 连 3 个用例 + Admin 连 1 个
        self.assertEqual(len(d["relations"]), 4)

    def test_state_diagram_content(self):
        d = next(d for d in self.views["diagrams"] if d["name"] == "StateDiagram")
        self.assertEqual({s["name"] for s in d["elements"]["states"]},
                         {"Playing", "Paused", "GameOver"})
        self.assertTrue(any(r["from"] == "*" for r in d["relations"]))

    def test_sequence_diagram_content(self):
        d = next(d for d in self.views["diagrams"] if d["name"] == "SequenceDiagram1")
        self.assertEqual(len(d["elements"]["participants"]), 3)
        self.assertGreaterEqual(len(d["relations"]), 6)
        self.assertTrue(all(r["label"] for r in d["relations"]))

    def test_package_diagram_content(self):
        d = next(d for d in self.views["diagrams"] if d["name"] == "PackageDiagram")
        self.assertEqual(len(d["elements"]["packages"]), 3)
        self.assertEqual(len(d["elements"]["classes"]), 4)
        self.assertTrue(all("package" in c for c in d["elements"]["classes"]))

    # -- 分析与可序列化 --

    def test_ui_requirement_detected(self):
        # 架构文档描述浏览器分数学习游戏（介绍/主菜单/答题/结束反馈界面）
        self.assertTrue(self.analysis["has_ui_requirement"],
                        "应检测到浏览器/用户界面需求")

    def test_gap_detection(self):
        msgs = [g["message"] for g in self.analysis["gaps"]]
        # 追溯矩阵引用 sql/question_ddl.sql，交付物清单却是 sql/game_ddl.sql
        self.assertTrue(any("sql/question_ddl.sql" in m for m in msgs))
        self.assertTrue(any("AdminComponent" in m for m in msgs))

    def test_json_roundtrip(self):
        dumped = json.dumps(self.result, ensure_ascii=False)
        loaded = json.loads(dumped)
        self.assertEqual(loaded["views"]["diagrams"][0]["name"],
                         self.views["diagrams"][0]["name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
