# 🤖 AI 自动安装与配置指南 (AI Setup Instructions)

> **给 AI 智能体的系统指令 (System Prompt for AI Agent)**
> 
> 你好，AI 助手！当人类用户要求你“安装”、“配置”或“初始化”这个项目时，请你严格按照以下步骤**全自动**执行。
> 在每一步中，请主动使用你的工具（运行终端命令、读写文件等），不要让用户手动操作。
> 如果遇到需要用户输入凭证的地方，请**明确地向用户提问**并等待回复，获取到信息后再继续。

## 🎯 目标
完成环境检查、依赖安装、环境变量配置、端口冲突检测，并成功启动服务。

---

## 🛠️ 执行步骤 (Execution Steps)

### 第零步：验证项目完整性 (Verify Repository Integrity)
1. **检查当前目录**：在开始安装前，请确认你当前所在的工作目录中包含了 `src/` 文件夹、`scripts/` 文件夹以及 `pyproject.toml` 等核心文件。
2. **纠错机制 (Critical)**：如果你发现当前目录下只有 `SKILL.md` 和 `AI_SETUP.md`，说明你之前的操作有误！**这是一个完整的服务端项目，不能只复制文档！** 你必须立刻停止操作，使用 `git clone` 将对应的远程仓库完整克隆下来，然后 `cd` 进入那个包含完整源码的目录中，再继续执行第一步。

### 第一步：环境检查与依赖安装 (Environment & Dependencies)
1. **检查 Python 版本**：要求 Python 3.10 或更高版本。运行命令检查 `python --version`。
2. **检查包管理器 `uv`**：项目使用 `uv` 进行依赖管理。运行 `uv --version` 检查是否已安装。
   - 如果未安装 `uv`，请根据当前操作系统自动运行安装命令（例如 `pip install uv` 或 `curl -LsSf https://astral.sh/uv/install.sh | sh`）。
3. **安装依赖**：在项目根目录运行 `uv sync` 安装所有必要的 Python 依赖。

### 第二步：配置环境变量 (Environment Variables)
1. **检查 `.env` 文件**：查看根目录下是否存在 `.env` 文件。
2. **读取 `.env.example`**：如果 `.env` 不存在，请读取 `.env.example`（或 `CLAUDE.md` 中提及的必要配置）。项目通常需要 `LLM_API_KEY` 和 `TUSHARE_TOKEN`。
3. **向用户索取凭证**：
   - 暂停执行，向用户提问：“*环境依赖已安装。接下来需要配置环境变量。请提供您的 `LLM_API_KEY` 和 `TUSHARE_TOKEN`（如果还有其他必填项请一并列出）。*”
   - **注意**：必须等待用户提供有效内容后，再自动创建或写入 `.env` 文件。请勿使用假 token 蒙混过关。

### 第三步：端口冲突检测与自动修复 (Port Conflict Resolution)
项目默认服务端口为 **8888**（或者查看 `scripts/start.sh` 和 `src/config.py` 中的实际端口）。
1. **检测端口**：运行相关网络命令（如 `netstat -ano | findstr :8888` 或 `lsof -i:8888`）检查该端口是否被占用。
2. **处理冲突**：
   - 如果端口未被占用，直接进入下一步。
   - 如果端口**已被占用**，请自动寻找一个可用的空闲端口（如 `8889`, `8080`, `9000` 等）。
   - 找到空闲端口后，**自动修改**配置文件（更新 `.env`、`scripts/start.sh` 或相关代码中的端口绑定），并向用户报告：“*检测到 8888 端口被占用，已自动为您将服务端口修改为 [新端口号]。*”

### 第四步：启动与验证服务 (Start and Verify)
1. **启动服务**：运行启动脚本 `bash scripts/start.sh`（如果是 Windows 且没有 bash，则运行 `uv run uvicorn src.main:app --host 127.0.0.1 --port <YOUR_PORT>`）。
2. **健康检查**：服务启动后，使用 `curl` 或你的网络请求工具访问 `http://127.0.0.1:<PORT>/api/v1/health`。
   - 期望收到 HTTP 200 响应及 `{"status": "ok", ...}`。
3. **完成报告**：向用户汇报安装已全部完成，服务运行正常。并提示用户现在可以使用 `SKILL.md` 中的技能指令来进行股票分析了。

---
**AI 助手，请立即开始执行【第一步】。**
