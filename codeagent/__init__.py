"""CodeAgent —— 简易代码智能体。

根据架构文档（Architecture_Documentation.md + Architecture_View.md）
自动生成完整项目的 Windows 桌面应用。
底层模型: DeepSeek deepseek-v4-pro（官方 API，无需本地部署）。

模块划分:
* codeagent.preprocessor   第 3 步: 输入文档 → 结构化 JSON（确定性解析，不依赖 LLM）
* codeagent.deepseek       第 1 步: DeepSeek API 客户端
* codeagent.prompts        第 4 步: 提示词构建
* codeagent.tools          第 5 步: Agent 文件工具集
* codeagent.orchestrator   第 5-6 步: Agent 运行循环
* codeagent.checker        第 7 步: 检查与自动修复
* codeagent.gui            第 8 步: Tkinter 桌面界面
"""

__version__ = "1.0.0"
