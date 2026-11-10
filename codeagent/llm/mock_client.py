"""Mock LLM 客户端：无 API Key 离线跑通全流程（联调/测试用）。

行为脚本：
* 第 1 次调用（Plan 阶段）→ 返回固定项目计划 JSON；
* 其后每次调用（Generate 阶段）→ 依次返回一个 write_file 工具调用；
* 文件发完后 → 返回 complete 工具调用，终止循环；
* Repair 阶段（system 提示词含 REPAIR MODE 标记）→ 直接返回文本，不修任何文件。
"""

from __future__ import annotations

import json

from .base import LLMResponse

# 固定计划（内容真实可运行，保证 checker 全绿）
MOCK_PLAN = {
    "language": "python",
    "summary": "Space Fractions 分数学习游戏的最小可运行实现（离线 Mock 联调用）。",
    "directory_tree": [
        "src/", "src/__init__.py", "src/game.py", "src/questions.py", "src/app.py",
        "public/", "public/index.html", "public/app.js", "public/style.css",
        "tests/", "tests/test_game.py", "k8s/", "sql/",
    ],
    "files": [
        {"path": "README.md", "purpose": "项目说明", "est_lines": 30},
        {"path": "requirements.txt", "purpose": "依赖声明", "est_lines": 1},
        {"path": "public/index.html", "purpose": "浏览器界面（介绍/主菜单/答题/结束反馈）", "est_lines": 40},
        {"path": "public/app.js", "purpose": "界面交互与答题流程", "est_lines": 70},
        {"path": "public/style.css", "purpose": "界面样式", "est_lines": 30},
        {"path": "src/__init__.py", "purpose": "包标记", "est_lines": 1},
        {"path": "src/game.py", "purpose": "游戏状态机（Playing/Paused/GameOver）", "est_lines": 60},
        {"path": "src/questions.py", "purpose": "题目数据与判分", "est_lines": 40},
        {"path": "src/app.py", "purpose": "命令行入口", "est_lines": 30},
        {"path": "tests/__init__.py", "purpose": "测试包标记", "est_lines": 1},
        {"path": "tests/test_game.py", "purpose": "状态机与判分单元测试", "est_lines": 40},
        {"path": "openapi.yaml", "purpose": "API 契约（架构文档交付物）", "est_lines": 25},
        {"path": "internal.proto", "purpose": "内部 RPC 契约（架构文档交付物）", "est_lines": 15},
        {"path": "k8s/spacefractions-deployment.yaml", "purpose": "K8s 部署清单（架构文档交付物）", "est_lines": 20},
        {"path": "sql/game_ddl.sql", "purpose": "数据库 DDL（架构文档交付物）", "est_lines": 6},
        {"path": "traceability_matrix.csv", "purpose": "需求追溯矩阵（架构文档交付物）", "est_lines": 4},
        {"path": "architecture.md", "purpose": "架构说明（架构文档交付物）", "est_lines": 30},
        {"path": "Dockerfile", "purpose": "容器化（选做）", "est_lines": 8},
    ],
    "build_command": "",
    "run_command": "python -m src.app",
    "test_command": "python -m unittest",
    "notes": ["mock 固定计划，覆盖交付物要求", "由 MockLLMClient 驱动"],
}

_MOCK_FILES = {
    "README.md": "# Space Fractions\n\n分数学习小游戏（Mock 生成样例）。\n\n## 浏览器界面\n直接用浏览器打开 `public/index.html`：介绍 → 主菜单 → 答题 → 结束反馈。\n\n## 命令行版\n```\npython -m src.app\n```\n\n## 测试\n```\npython -m unittest\n```\n\n## 数据库初始化\n```\npsql -U postgres -d spacefractions -f sql/game_ddl.sql\n```\n\n## Architecture decisions\n- 架构文档为微服务部署（K8s），本样例合并为单一可运行项目（Mock 联调范围）；\n  组件边界在 src/ 内以模块划分。\n",
    "public/index.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"UTF-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n  <title>Space Fractions</title>\n  <link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n  <main id=\"app\">\n    <section id=\"screen-intro\" class=\"screen\">\n      <h1>Space Fractions</h1>\n      <p>Learn fractions by answering questions in a space journey.</p>\n      <button data-action=\"to-menu\">Start</button>\n    </section>\n    <section id=\"screen-menu\" class=\"screen hidden\">\n      <h2>Main Menu</h2>\n      <button data-action=\"start-quiz\">Play Game</button>\n    </section>\n    <section id=\"screen-quiz\" class=\"screen hidden\">\n      <h2 id=\"question-text\"></h2>\n      <div id=\"options\"></div>\n    </section>\n    <section id=\"screen-end\" class=\"screen hidden\">\n      <h2>Game Over</h2>\n      <p id=\"final-score\"></p>\n      <button data-action=\"restart\">Play Again</button>\n      <button data-action=\"to-menu\">Back to Menu</button>\n    </section>\n  </main>\n  <script src=\"app.js\"></script>\n</body>\n</html>\n",
    "public/app.js": "/* 界面交互：介绍 -> 主菜单 -> 答题 -> 结束反馈。 */\n(function () {\n  'use strict';\n  var QUESTIONS = [\n    { prompt: '1/2 + 1/4 = ?', options: ['1/4', '2/6', '3/4'], answer: '3/4' },\n    { prompt: '1/3 - 1/6 = ?', options: ['1/6', '1/2', '1/3'], answer: '1/6' },\n    { prompt: '2/5 * 3/4 = ?', options: ['6/20', '3/10', '5/9'], answer: '3/10' }\n  ];\n  var index = 0;\n  var score = 0;\n\n  function show(id) {\n    var screens = document.querySelectorAll('.screen');\n    for (var i = 0; i < screens.length; i += 1) {\n      screens[i].classList.add('hidden');\n    }\n    document.getElementById(id).classList.remove('hidden');\n  }\n\n  function renderQuestion() {\n    var q = QUESTIONS[index];\n    document.getElementById('question-text').textContent = q.prompt;\n    var box = document.getElementById('options');\n    box.innerHTML = '';\n    q.options.forEach(function (opt) {\n      var btn = document.createElement('button');\n      btn.className = 'option';\n      btn.textContent = opt;\n      btn.addEventListener('click', function () { answer(opt); });\n      box.appendChild(btn);\n    });\n  }\n\n  function answer(opt) {\n    if (opt === QUESTIONS[index].answer) { score += 1; }\n    index += 1;\n    if (index < QUESTIONS.length) {\n      renderQuestion();\n    } else {\n      document.getElementById('final-score').textContent =\n        'Your score: ' + score + ' / ' + QUESTIONS.length;\n      show('screen-end');\n    }\n  }\n\n  function restart() {\n    index = 0;\n    score = 0;\n    renderQuestion();\n    show('screen-quiz');\n  }\n\n  document.addEventListener('click', function (ev) {\n    var action = ev.target.getAttribute && ev.target.getAttribute('data-action');\n    if (action === 'to-menu') { show('screen-menu'); }\n    if (action === 'start-quiz') { restart(); }\n    if (action === 'restart') { restart(); }\n  });\n\n  show('screen-intro');\n}());\n",
    "public/style.css": "body { font-family: system-ui, sans-serif; background: #0b1026; color: #e8ecff; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }\nmain { width: 480px; max-width: 92vw; text-align: center; }\n.screen { background: #16204a; border-radius: 12px; padding: 32px; }\n.hidden { display: none; }\nbutton { background: #4a5fd0; color: #fff; border: none; border-radius: 8px; padding: 10px 22px; margin: 6px; font-size: 16px; cursor: pointer; }\nbutton:hover { background: #5d71e0; }\n#options { display: flex; flex-direction: column; }\n.option { margin: 6px auto; width: 80%; }\n",
    "requirements.txt": "# 无第三方依赖\n",
    "src/__init__.py": "",
    "src/game.py": '"""游戏状态机：Playing / Paused / GameOver。"""\n\n\nclass Game:\n    """一局分数答题游戏。"""\n\n    def __init__(self, questions):\n        self.questions = questions\n        self.index = 0\n        self.score = 0\n        self.state = "Playing"\n\n    def pause(self):\n        if self.state == "Playing":\n            self.state = "Paused"\n\n    def resume(self):\n        if self.state == "Paused":\n            self.state = "Playing"\n\n    def current_question(self):\n        if self.state != "Playing":\n            return None\n        if self.index >= len(self.questions):\n            return None\n        return self.questions[self.index]\n\n    def submit(self, answer):\n        """提交答案：正确得分并前进，答完进入 GameOver。"""\n        if self.state != "Playing":\n            return None\n        q = self.current_question()\n        if q is None:\n            return None\n        ok = q.check(answer)\n        if ok:\n            self.score += 1\n        self.index += 1\n        if self.index >= len(self.questions):\n            self.state = "GameOver"\n        return ok\n\n    def view_score(self):\n        return self.score\n',
    "src/questions.py": '"""题目数据与判分。"""\n\n\nclass Question:\n    def __init__(self, prompt, options, answer):\n        self.prompt = prompt\n        self.options = options\n        self.answer = answer\n\n    def check(self, candidate):\n        return str(candidate).strip() == str(self.answer).strip()\n\n\nDEFAULT_QUESTIONS = [\n    Question("1/2 + 1/4 = ?", ["1/4", "2/6", "3/4"], "3/4"),\n    Question("1/3 - 1/6 = ?", ["1/6", "1/2", "1/3"], "1/6"),\n    Question("2/5 * 3/4 = ?", ["6/20", "3/10", "5/9"], "3/10"),\n]\n',
    "src/app.py": '"""命令行入口：答题并显示得分。"""\n\nfrom .game import Game\nfrom .questions import DEFAULT_QUESTIONS\n\n\ndef main():\n    game = Game(DEFAULT_QUESTIONS)\n    while game.state == "Playing":\n        q = game.current_question()\n        if q is None:\n            break\n        print("题目:", q.prompt)\n        print("选项:", ", ".join(q.options))\n        answer = input("你的答案: ")\n        game.submit(answer)\n    print("最终得分:", game.view_score(), "/", len(DEFAULT_QUESTIONS))\n\n\nif __name__ == "__main__":\n    main()\n',
    "tests/__init__.py": "",
    "tests/test_game.py": '"""状态机与判分单元测试。"""\n\nimport sys\nimport unittest\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n\nfrom src.game import Game\nfrom src.questions import DEFAULT_QUESTIONS, Question\n\n\nclass TestGame(unittest.TestCase):\n    def test_initial_state(self):\n        g = Game(DEFAULT_QUESTIONS)\n        self.assertEqual(g.state, "Playing")\n        self.assertEqual(g.score, 0)\n\n    def test_pause_resume(self):\n        g = Game(DEFAULT_QUESTIONS)\n        g.pause()\n        self.assertEqual(g.state, "Paused")\n        g.resume()\n        self.assertEqual(g.state, "Playing")\n\n    def test_correct_answer_scores(self):\n        g = Game(DEFAULT_QUESTIONS)\n        self.assertTrue(g.submit("3/4"))\n        self.assertEqual(g.score, 1)\n\n    def test_wrong_answer(self):\n        g = Game(DEFAULT_QUESTIONS)\n        self.assertFalse(g.submit("9/9"))\n        self.assertEqual(g.score, 0)\n\n    def test_game_over_after_all_questions(self):\n        g = Game(DEFAULT_QUESTIONS)\n        for _ in DEFAULT_QUESTIONS:\n            g.submit("3/4")\n        self.assertEqual(g.state, "GameOver")\n        self.assertEqual(g.view_score(), 1)\n\n    def test_question_check(self):\n        q = Question("1/2 + 1/4 = ?", ["1/4", "2/6", "3/4"], "3/4")\n        self.assertTrue(q.check("3/4"))\n        self.assertFalse(q.check("1/4"))\n\n\nif __name__ == "__main__":\n    unittest.main(verbosity=2)\n',
    "Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"-m\", \"src.app\"]\n",
    "openapi.yaml": "openapi: 3.0.0\ninfo:\n  title: Space Fractions API\n  description: API for the Space Fractions game\n  version: 1.0.0\npaths:\n  /play:\n    get:\n      summary: Play the game\n      responses:\n        '200':\n          description: Game started\n          content:\n            application/json:\n              schema:\n                type: object\n                properties:\n                  gameId:\n                    type: integer\n                    description: Game ID\n",
    "internal.proto": "syntax = \"proto3\";\npackage spacefractions;\nservice GameService {\n  rpc Play(PlayRequest) returns (PlayResponse) {}\n}\nmessage PlayRequest {\n  int32 gameId = 1;\n}\nmessage PlayResponse {\n  int32 gameId = 1;\n}\n",
    "k8s/spacefractions-deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: spacefractions\nspec:\n  replicas: 3\n  selector:\n    matchLabels:\n      app: spacefractions\n  template:\n    metadata:\n      labels:\n        app: spacefractions\n    spec:\n      containers:\n      - name: spacefractions\n        image: spacefractions:latest\n        ports:\n        - containerPort: 80\n",
    "sql/game_ddl.sql": "CREATE TABLE users (\n  id SERIAL PRIMARY KEY,\n  username TEXT UNIQUE NOT NULL,\n  password_hash TEXT NOT NULL\n);\nCREATE TABLE games (\n  id SERIAL PRIMARY KEY,\n  user_id INT REFERENCES users(id),\n  game_state JSONB NOT NULL\n);\nCREATE TABLE questions (\n  id SERIAL PRIMARY KEY,\n  prompt TEXT NOT NULL,\n  options JSONB NOT NULL,\n  answer TEXT NOT NULL\n);\n",
    "traceability_matrix.csv": "Requirement ID,Short Text,Diagram(s),Component(s),Artifact filename(s),Rationale\nFR-1,Play game,UseCaseDiagram,GameComponent,openapi.yaml,Allows users to play the game\nNFR-1,Performance,SequenceDiagram1,GameComponent,internal.proto,Ensures the game responds quickly to user input\nASR-1,Data durability,DeploymentDiagram,QuestionComponent,sql/game_ddl.sql,Ensures that question data is persisted and recoverable\n",
    "architecture.md": "# Space Fractions 架构说明\n\n- 架构风格: Microservices\n- 部署: Cloud-based infrastructure (K8s)\n- 组件: GameComponent（游戏逻辑）/ QuestionComponent（题目管理）/ UserComponent（用户认证）\n- 技术栈: Node.js/Express（本 Mock 样例用 Python 实现等价逻辑）\n- API: /play (openapi.yaml)；内部契约: internal.proto；持久化: PostgreSQL (sql/game_ddl.sql)\n",
}

REPAIR_MARKER = "REPAIR MODE"


class MockLLMClient:
    """按脚本返回结果的假客户端。"""

    def __init__(self, plan: dict | None = None, files: dict | None = None):
        self.plan = plan or MOCK_PLAN
        self.files = files or _MOCK_FILES
        self.calls = 0
        self._file_index = 0
        self._plan_returned = False

    def complete(self, messages: list, tools: list | None = None, **opts) -> LLMResponse:
        self.calls += 1
        system = messages[0]["content"] if messages else ""
        if REPAIR_MARKER in system:
            return LLMResponse(content="No fixes needed.", finish_reason="stop")

        if not self._plan_returned:
            self._plan_returned = True
            return LLMResponse(
                content=json.dumps(self.plan, ensure_ascii=False),
                finish_reason="stop",
                usage={"prompt_tokens": 100, "completion_tokens": 50})

        paths = [f["path"] for f in self.plan["files"]]
        if self._file_index < len(paths):
            path = paths[self._file_index]
            self._file_index += 1
            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": f"call_mock_{self._file_index}",
                    "name": "write_file",
                    "arguments": {"path": path, "content": self.files.get(path, "")},
                }],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 200, "completion_tokens": 30})

        return LLMResponse(
            content="",
            tool_calls=[{"id": "call_mock_done", "name": "complete",
                         "arguments": {"summary": "全部计划文件已生成（Mock）。"}}],
            finish_reason="tool_calls",
            usage={"prompt_tokens": 200, "completion_tokens": 20})
