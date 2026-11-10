# CodeAgent v1.0.0

Windows 桌面代码智能体，读取架构说明和 PlantUML 架构视图，通过 DeepSeek
官方 API 生成项目代码、依赖声明、README 和测试文件。

本版本包含 GUI、命令行入口、架构预处理、工具调用、检查与修复循环，以及离线自检。

验证结果：Agent 测试 141/141，通过当前样例测试 27/27，exe 的 Mock 生成项目测试 6/6。
打包后的 exe 已在本机限制工具路径的环境中完成 GUI Mock 生成，验证时外部
python、py、node、npm 均不可查找。

下载 Windows ZIP 后解压并双击 CodeAgent.exe。真实 API 模式需要网络和自己的
DeepSeek API Key；离线体验可使用 Mock 模式。生成项目的运行环境按其 README 准备。

本次发布记录的离线验收未调用真实 API，也没有在全新 Windows 系统上进行验证。
