# 自治运行基础设施（给仓库所有者）

本目录负责在隔离的 Docker 容器里，以 [instruction.md](../instruction.md) 为使命，无人值守地运行 Claude Code。给 agent 看的运行环境与授权说明在 [ENVIRONMENT.md](ENVIRONMENT.md)。

## 工作方式

- `run.sh start` 在宿主机上构建镜像，以后台方式启动容器 `cli-agent-autonomy`。
- 容器内 [loop.sh](loop.sh) 在 12 小时截止前反复启动全新的 headless 会话（`claude -p --dangerously-skip-permissions`），每轮提示词见 [prompt.md](prompt.md)。每个会话都从仓库和 GitHub 状态恢复进度。
- 运行会提前结束的情况：连续 3 个会话没有让任何 Git ref 变化，连续 5 个会话失败，或者你执行了 `stop`。
- 仓库在容器里是从 GitHub 克隆到 Docker 卷 `cli-agent-workspace` 的副本，宿主机这份 checkout 不会被改动。agent 的成果通过 GitHub 查看，或者 `git pull`。
- 模型 API 复用宿主机 `~/.claude/settings.json` 里的 `env`，其中 `127.0.0.1` 会改写为 `host.docker.internal`。cc-switch 必须保持运行。
- `loop.sh` 和 `prompt.md` 在构建镜像时固化。agent 改了仓库里的版本，要等你 `git pull` 后重新 `start` 才会生效。

## 一次性准备

1. **创建 GitHub fine-grained PAT**：GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token
   - Repository access：Only select repositories → `CLI-Agent`
   - Repository permissions 设为 Read and write：Administration、Contents、Issues、Pull requests、Actions、Workflows、Commit statuses（Metadata 会自动设为只读）
   - 其中 Workflows 必须勾选，否则 agent 推不了 `.github/workflows/`。
2. **写入密钥文件**（在仓库之外）：
   ```bash
   mkdir -p ~/.config/cli-agent-autonomy && chmod 700 ~/.config/cli-agent-autonomy
   printf 'GH_TOKEN=%s\n' '<你的PAT>' > ~/.config/cli-agent-autonomy/secrets.env
   chmod 600 ~/.config/cli-agent-autonomy/secrets.env
   ```
   这个文件里也可以覆盖 `ANTHROPIC_*` 等变量；不设的话沿用 `~/.claude/settings.json`。
3. **推送初始提交**：容器要从 GitHub 克隆仓库，所以 `main` 上必须先有提交。
4. **自检**：`./autonomy/run.sh smoke`，预期输出模型回复 `OK`、你的 GitHub 用户名，以及 `"admin":true` 等权限。

## 日常操作

| 命令 | 作用 |
|---|---|
| `./autonomy/run.sh start` | 开始一次运行（默认 12 小时，可用 `AUTONOMY_HOURS=2` 覆盖） |
| `./autonomy/run.sh logs` | 实时查看可读进度（💬 模型输出，🔧 工具调用） |
| `./autonomy/run.sh status` | 容器状态和驱动日志末尾 |
| `./autonomy/run.sh stop` | 优雅停止：当前会话做完后不再开新会话 |
| `./autonomy/run.sh stop --now` | 立即终止 |
| `./autonomy/run.sh shell` | 进容器查看现场；运行已结束时会开一个不带凭证的临时容器 |
| `./autonomy/run.sh fetch-logs` | 把完整的 stream-json 日志拷到 `autonomy/logs/`（已加入 gitignore） |

每次运行结束后，看一下 GitHub 上带 `needs-human` 标签的 issue，在 issue 里回复即可，下次运行的会话会去读。

从头再来（会丢掉容器里未推送的工作）：`docker volume rm cli-agent-workspace cli-agent-home`。

## 安全边界与已知限制

- agent 在容器里有免密 sudo，网络不受限，能读到 `GH_TOKEN` 和模型 API token。PAT 只授权本仓库，泄露后的影响范围也就是这一个仓库。
- 宿主机文件系统、Docker daemon 和你的其他凭证都没有挂进容器。
- 容器内没有 Docker。如果 agent 选择用 Docker 给产品做沙箱，它没法在这里测试。
- 仓库是公开的，agent 写的 issue、PR 和 CI 日志全网可见。
- 模型代理只有容器能访问，GitHub Actions 访问不到，所以 CI 只能跑不调用真实模型的测试。
