# 贡献指南

感谢你对本项目的关注！欢迎任何形式的贡献。

## 🐛 报告 Bug

1. 先搜索 [Issues](https://github.com/ZhuLinsen/daily_stock_analysis/issues) 确认问题未被报告
2. 使用 Bug Report 模板创建新 Issue
3. 提供详细的复现步骤和环境信息

## 💡 功能建议

1. 先搜索 Issues 确认建议未被提出
2. 使用 Feature Request 模板创建新 Issue
3. 详细描述你的使用场景和期望功能

## 🔧 提交代码

### 开发环境

```bash
# 克隆仓库
git clone https://github.com/ZhuLinsen/daily_stock_analysis.git
cd daily_stock_analysis

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 安装依赖
./scripts/dev_bootstrap.sh --backend-only

# 配置环境变量
cp .env.example .env
```

### 提交流程

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交改动：`git commit -m 'feat: add some feature'`
4. 推送分支：`git push origin feature/your-feature`
5. 创建 Pull Request

### Commit 规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
feat: 新功能
fix: Bug 修复
docs: 文档更新
style: 代码格式（不影响功能）
refactor: 重构
perf: 性能优化
test: 测试相关
chore: 构建/工具相关
```

示例：
```
feat: 添加钉钉机器人支持
fix: 修复 429 限流重试逻辑
docs: 更新 README 部署说明
```

### 代码规范

- Python 代码遵循 PEP 8
- 函数和类需要添加 docstring
- 重要逻辑添加注释
- 新功能需要更新相关文档

### CI 自动检查

提交 PR 后，CI 会自动运行以下检查：

| 检查项 | 说明 | 必须通过 |
|--------|------|:--------:|
| backend-gate | `scripts/ci_gate.sh`（py_compile + flake8 严重错误 + 本地核心脚本 + offline pytest） | ✅ |
| docker-build | Docker 镜像构建与关键模块导入 smoke | ✅ |
| web-gate | 前端变更时执行 `npm run lint` + `npm run build` | ✅（触发时） |
| network-smoke | 定时/手动执行 `pytest -m network` + `test.sh quick`（非阻断） | ❌（观测项） |

### 本地开发与验证基线

新 checkout 推荐先运行 bootstrap 脚本准备本地依赖：

```bash
# 仅安装后端依赖、flake8 和 pytest
./scripts/dev_bootstrap.sh --backend-only

# 同时准备后端和 Web 依赖
./scripts/dev_bootstrap.sh --with-web

# 同时准备后端、Web 和 Desktop 依赖
./scripts/dev_bootstrap.sh --all
```

`scripts/dev_bootstrap.sh` 默认使用 `.venv`，如需自定义虚拟环境目录，可设置 `DSA_VENV_DIR=/path/to/venv`。如果你不使用 bootstrap，也可以手动执行：

```bash
pip install -r requirements.txt
pip install flake8 pytest
```

**本地运行检查：**

| 场景 | 命令 | 说明 |
| --- | --- | --- |
| AI 协作资产 | `python scripts/check_ai_assets.py` | 修改 `AGENTS.md`、Copilot 指令或 `.claude/skills` 时运行 |
| 后端语法 | `./scripts/ci_gate.sh syntax` | 快速 py_compile 检查 |
| 后端完整门禁 | `./scripts/ci_gate.sh` | 与 CI backend-gate 行为一致 |
| 离线 pytest | `python -m pytest -m "not network"` | 不依赖 live API / 外部网络的确定性测试 |
| Web | `cd apps/dsa-web && npm run lint && npm run build` | 修改 Web 前端时运行 |
| Desktop | `cd apps/dsa-desktop && npm run test` | 修改桌面端时运行；打包改动还需补充构建验证 |

后端推荐直接跑完整 gate：

```bash
./scripts/ci_gate.sh
```

如修改前端：

```bash
cd apps/dsa-web
npm ci
npm run lint
npm run build
```

网络/API smoke 与离线检查分开处理：`python -m pytest -m network`、`./test.sh quick` 等会访问外部数据源或三方 API，主要用于手动/定时观测，不应替代离线 deterministic gate。若因为网络、凭证或地区限制未执行在线 smoke，请在 PR 中说明。

## 📋 优先贡献方向

查看 [Roadmap](README.md#-roadmap) 了解当前需要的功能：

- 🔔 新通知渠道（钉钉、飞书、Telegram）
- 🤖 新 AI 模型支持（GPT-4、Claude）
- 📊 新数据源接入
- 🐛 Bug 修复和性能优化
- 📖 文档完善和翻译

## ❓ 问题解答

如有任何问题，欢迎：
- 创建 Issue 讨论
- 查看已有 Issue 和 Discussion

再次感谢你的贡献！ 🎉
