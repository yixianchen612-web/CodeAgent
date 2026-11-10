# CodeAgent 容器化运行（CLI 模式，选做）
# 用法:
#   docker build -t codeagent .
#   docker run -e DEEPSEEK_API_KEY=sk-xxx \
#     -v "$PWD/input:/app/input" -v "$PWD/output:/app/generated" \
#     codeagent
FROM python:3.12-slim

WORKDIR /app
COPY codeagent/ codeagent/
COPY tests/ tests/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "codeagent", \
     "--doc", "/app/input/Architecture_Documentation.md", \
     "--views", "/app/input/Architecture_View.md", \
     "--out", "/app/generated"]
