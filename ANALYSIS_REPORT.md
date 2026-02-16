# aether-bot 多智能体稳定性分析报告

## 一、背景与目标

aether-bot 期望作为 PM/主管角色，稳定��制多个 Agent Team 操作 Claude 和 Codex 完成工作。
当前主要问题：**调用 Claude/Codex 子进程不稳定，频繁出错**。

本报告基于三个项目的源码对比分析：
- **aether-bot**（当前项目）：Python async agent 系统
- **codes CLI**：Go 语言的 Claude 多 Agent 调度系统
- **claude_code_bridge (ccb)**：Python 的多 AI 终端协作框架

---

## 二、三个项目的架构对比

### 2.1 aether-bot 当前架构

```
AgentLoop → ToolRegistry.execute() → ClaudeTool.execute()
  → asyncio subprocess 启动 runner.py（第一层子进程）
    → runner.py 内部：
       ├─ print 模式：subprocess.run(["claude", "-p", ...])（第二层子进程）
       └─ tty 模式：tmux session(固定名 "claude") + JSONL 日志读取
```

**核心特点**：两层子进程嵌套，TTY 模式依赖 tmux + 日志文件猜测。

### 2.2 codes CLI 架构

```
Daemon(3s 轮询) → findNextTask() → executeTask()
  → exec.Command("claude", "-p", prompt, "--output-format", "json")
    → 直接从 stdout 解析 JSON 结果
    → --session-id + --fork-session 实现会话延续
```

**核心特点**：单层子进程，纯 `-p` 模式，stdout JSON，无 tmux 依赖。

### 2.3 ccb 架构

```
LaskdDaemon → _SessionWorker
  → backend.send_text(pane_id, wrapped_prompt)  # 向 tmux pane 注入文本
  → ClaudeLogReader.wait_for_events()            # 从 JSONL 日志轮询输出
  → 检测 CCB_DONE 标记 → 返回结果
```

**核心特点**：终端原生 WYSIWYG，每个 AI 独立 pane，基于协议标记的请求-响应模型。

### 2.4 关键差异总结

| 维度 | aether-bot | codes | ccb |
|------|-----------|-------|-----|
| **调用方式** | 两层子进程嵌套 | 单层 `claude -p` | 终端 pane 注入 |
| **输出获取** | stdout 或 JSONL 日志猜测 | stdout JSON 直接解析 | JSONL 日志 + 协议标记 |
| **并发隔离** | 共享固定 tmux session | 独立进程天然隔离 | 每个 provider 独立 pane |
| **会话延续** | 无 | `--session-id --fork-session` | 持久化 session binding |
| **进程管理** | kill 一刀切 | 进程组隔离 + PID 检测 | pane 存活检测 + 健康检查 |
| **错误处理** | 全部转字符串 | 结构化 ClaudeResult | 结构化 CaskdResult + 指标 |
| **超时机制** | 双重 idle timeout（有缺陷） | context.Context 整体超时 | deadline + anchor 宽限期 |
| **日志追踪** | 有限审计日志 | 任务状态持久化 | 完整指标（anchor_ms, done_ms） |

---

## 三、根因分析：六大稳定性问题

### 问题 1：tmux 会话名硬编码冲突（🔴 Critical）

**位置**：`runner.py:175`
```python
session = "claude"  # 固定写死
```

**影响**：所有并发的 Claude TTY 调用共享同一个 tmux session。
- 提示词互相覆盖
- 输出结果串台
- 一个任务的 Ctrl+C 会杀掉另一个任务的 Claude 进程

**codes 的做法**：每次调用都是独立的 `exec.Command("claude", "-p", ...)` 子进程，
完全不用 tmux，天然隔离。

**ccb 的做法**：每个 provider 有独立的 pane（通过 pane ID 而非 session name 标识），
支持并发。每个请求通过唯一的 `CCB_REQ_ID` 标记（格式 `YYYYMMDD-HHMMSS-mmm-PID-counter`）
与响应精确配对，即使共享 pane 也不会混淆。

---

### 问题 2：日志路径发现不可靠（🔴 Critical）

**位置**：`claude_tty_bridge.py:107-114`

**影响**：
- 启发式猜测 `~/.claude/projects/<key>/*.jsonl` 路径
- 可能找到旧 session 的日志文件，静默读取错误的输出
- 回退路径可能根本不存在，但不会报错

**补充缺陷**：`_read_new_events()` 方法（`claude_tty_bridge.py:199`）存在 bug：
```python
carry + data        # 结果被丢弃！应该是 buf = carry + data
```
以及：
```python
events.append(("user", user_msg))
continue            # continue 导致 assistant 消息永远不会被读取
assistant_msg = _extract_message(entry, "assistant")  # 死代码
```

**codes 的做法**：通过 `--output-format json` 从 stdout 直接获取结果，
不依赖任何磁盘日志文件。

**ccb 的做法**：虽然也读日志，但有完善的三级查找机制和容错：
1. 优先使用 preferred_session（明确绑定的 session 路径）
2. 解析 `sessions-index.json`，按 mtime 选最新且匹配 projectPath 的
3. 兜底扫描目录下所有 `.jsonl` 文件按 mtime 排序
4. **关键差异**：session rebinding 机制 —— 如果 1.5s 内未检测到 anchor，
   自动 rebind 到最新 session（`laskd_daemon.py:210-215`），不会卡在错误的日志文件上

---

### 问题 3：进程生命周期管理粗暴（🟠 High）

**位置**：`claude.py:140-142`
```python
except asyncio.TimeoutError:
    proc.kill()      # 直接 SIGKILL，无优雅关闭
    return "Error: Claude tool timed out (runner did not exit)"
```

**影响**：
- 没有 SIGTERM ��� 等待 → SIGKILL 的优雅关闭流程
- runner.py 被杀后，其子进程（claude CLI）可能变成孤儿进程
- TTY 模式下 tmux session 不会被清理
- 僵尸进程随时间累积

**codes 的做法**：
- `setSysProcAttr(cmd)` 设置 `Setpgid: true`，子进程放入独立进程组
- 进程存活通过 `kill(pid, 0)` 检测
- 状态自动清理：`IsAgentAlive()` 发现死进程后自动标记为 Stopped

**ccb 的做法**：
- 不直接管理子进程 —— Claude 运行在独立 pane 中，由终端管理生命周期
- pane 存活检测：`terminal.is_alive(pane_id)` 每 2s 检查一次
- Daemon 有 idle timeout 自动关闭 + parent PID 监控
- 运行时目录垃圾回收：`_cleanup_stale_runtime_dirs()` 自动清理 24h 无活动的残留

---

### 问题 4：双重 idle timeout 逻辑缺陷（🟠 High）

**位置**：`runner.py:220-228`
```python
if last_assistant_ts is None:
    # 条件 A：从 prompt 发送时间算起
    if (time.time() - start) > idle_timeout:
        ...  # 重试或退出
else:
    # 条件 B：从最后一次 assistant 输出算起
    if (time.time() - last_assistant_ts) > idle_timeout:
        return 0 if saw_any else 3
```

**影响**：
- Claude 正在思考但尚未输出第一个 token 时，走条件 A
- 如果思考时间超过 idle_timeout（默认 300s），任务被误杀
- 合法的长时间 Claude 操作（如大规模代码重构）会被截断

**codes 的做法**：不使用 idle timeout 机制。`RunClaude()` 是同步阻塞调用，
通过 `context.Context` 控制整体超时，Claude CLI 内部自行管理执行节奏。

**ccb 的做法**：
- 使用 **anchor 宽限期**（1.5s）而非 idle timeout
- 一旦检测到 anchor（`CCB_REQ_ID` 出现在日志中），说明 Claude 已接收到请求
- 之后只看整体 deadline（默认 300s），不做 idle 检测
- 如果 anchor 未出现，触发 session rebinding 而非直接失败

---

### 问题 5：错误处理一刀切（🟡 Medium）

**位置**：`registry.py:95-98`
```python
except Exception as e:
    logger.exception(f"Tool registry: '{name}' failed...")
    return f"Error executing {name}: {str(e)}"
```

**影响**：
- 所有异常都转为字符串，Agent Loop 无法区分错误类型
- 不能针对 "超时" vs "认证失败" vs "进程崩溃" 做不同处理
- 无法实现工具级别的重试策略

**codes 的做法**：
- `ClaudeResult` 结构体包含结构化信息：`IsError`, `Error`, `SessionID`, `CostUSD`
- 根据 `IsError` 区分处理：调用 `FailTask()` 或 `CompleteTask()`

**ccb 的做法**：
- `CaskdResult` 包含丰富的诊断指标：
  - `anchor_seen` / `anchor_ms` — 请求是否被 Claude 接收、接收耗时
  - `done_seen` / `done_ms` — 是否正常完成、完成耗时
  - `fallback_scan` — 是否触发了 session rebinding
  - `exit_code` — 区分成功/超时/pane死亡/中断
- 这些指标使得调用方可以做精确的故障诊断和重试决策

---

### 问题 6：Codex 缺少专用工具（🟡 Medium）

**现状**：Codex 没有像 Claude 那样的专用 Tool 实现。
- `skills/codex/SKILL.md` 仅是文档
- 只能通过通用的 `ExecTool`（shell）调用 `codex exec`
- 没有输出解析、超时管理、重试逻辑
- 没有 session 延续能力

**ccb 的做法**：
- `codex_comm.py`（1321 行）：完整的 Codex 日志读取器
- 高效尾部读取：seek 到文件末尾反向读块
- Session ID 过滤 + 工作目录过滤
- Watchdog 集成：可选的文件变更实时监控
- 日志轮转检测：自动 rebind 到新 session

---

## 四、可借鉴的设计模式

### 来自 codes 的模式

#### 模式 C1：纯 `-p` 模式 + JSON 输出

```go
// codes runner.go
cmd := exec.CommandContext(ctx, "claude", "-p", prompt, "--output-format", "json")
// 直接从 stdout 解析，不依赖日志文件
```

**价值**：消除 tmux 冲突和日志路径问题。适用于**不需要可视化**的自动化任务。

#### 模式 C2：进程组隔离 + PID 存活检测

```go
cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}  // 独立进程组
isProcessAlive(pid)  // kill(pid, 0) 检测 + 自动清理
```

#### 模式 C3：文件锁 + CAS 原子操作

```go
fl := NewFileLock(lockPath)
fl.Lock(); defer fl.Unlock()
task = GetTask(...)
err = updateFn(task)   // CAS 回调可拒绝更新
writeJSON(task)
```

#### 模式 C4：结构化调用结果

```go
type ClaudeResult struct {
    Result, Error, SessionID string
    CostUSD, Duration        float64
    IsError                  bool
}
```

---

### 来自 ccb 的模式

#### 模式 B1：协议标记的请求-响应配对

```
发送给 Claude 的 prompt 被包装为：

CCB_REQ_ID: 20260215-143000-123-12345-0
<actual prompt>
IMPORTANT: End your reply with this exact final line:
CCB_DONE: 20260215-143000-123-12345-0
```

**实现**（`ccb_protocol.py`）：
- `make_req_id()` 生成唯一 ID：`YYYYMMDD-HHMMSS-mmm-PID-counter`
- `is_done_text(text, req_id)` 检测完成标记
- `strip_done_text()` 清理输出中的协议标记

**价值**：即使多个请求共享同一个 Claude 实例，也能通过 req_id 精确匹配响应。
这比 aether-bot 的"等待任意 assistant 输出"可靠得多。

#### 模式 B2：Anchor 检测 + Session Rebinding

```python
# laskd_daemon.py _SessionWorker 核心逻辑
anchor_seen = False
grace_period = 1.5  # 秒

while not deadline_reached:
    events = log_reader.wait_for_events(state, timeout_s=0.5)

    for role, text in events:
        if not anchor_seen and REQ_ID_PREFIX in text:
            anchor_seen = True
            anchor_ms = (now - start) * 1000

        if role == "assistant":
            chunks.append(text)
            if is_done_text(combined, req_id):
                done_seen = True
                break

    # 关键：anchor 未出现时自动 rebind
    if not anchor_seen and (now - start) > grace_period:
        reader = ClaudeLogReader(work_dir, use_sessions_index=False)
        state = reader.capture_event_state()
        fallback_scan = True
```

**价值**：
- 不会永远卡在错误的日志文件上（aether-bot 的核心问题）
- 1.5s 宽限期足够判断"请求是否被正确接收"
- 自动 rebind 而非报错退出

#### 模式 B3：结构化诊断指标

```python
@dataclass
class CaskdResult:
    exit_code: int
    reply: str
    anchor_seen: bool     # 请求是否被 Claude 接收
    anchor_ms: float      # 接收耗时
    done_seen: bool       # 是否正常完成
    done_ms: float        # 完成耗时
    fallback_scan: bool   # 是否触发了 session rebinding
```

**价值**：调用方可以根据这些指标做精确诊断：
- `anchor_seen=False` → 请求发送失败（pane 问题或 Claude 未运行）
- `anchor_seen=True, done_seen=False` → Claude 接收了但超时或崩溃
- `fallback_scan=True` → session 发生了切换（值得记录但不一定是错误）

#### 模式 B4：Pane 健康检查

```python
# ccb claude_comm.py
def _check_session_health_impl(probe_terminal=True):
    # 1. pane_id 是否存在
    # 2. 如果是 WezTerm + title marker：重新发现 pane
    # 3. 验证 pane 是否存活（is_alive()）
    # 4. 返回详细错误信息
```

#### 模式 B5：Daemon 自治 + 单实例锁

```python
# ccb askd_server.py
class AskDaemonServer:
    # 文件锁确保每个 work_dir 只有一个 daemon
    # idle timeout 自动关闭（默认 60s 无请求）
    # parent PID 监控（父进程退出则自动停止）
    # token 认证防止未授权访问
```

#### 模式 B6：跨平台终端抽象

```python
# ccb terminal.py
class TmuxBackend:
    def send_text(pane_id, text)   # 短文本用 -l，长文本用 buffer+paste
    def is_alive(pane_id) -> bool
    def ensure_pane_log(pane_id)   # pipe-pane 实时日志

class WeztermBackend:
    def send_text(pane_id, text)   # wezterm cli send-text
    def is_alive(pane_id) -> bool
```

---

## 五、改进方案（更新）

结合 codes 和 ccb 两个项目的经验，建议分两条路线改进：

### 路线一：`-p` 模式优先（适用于自动化批量任务）

借鉴 codes，Claude Tool 默认使用 `claude -p` + `--output-format json`。

**优点**：实现最简单，彻底消除 tmux/日志相关问题。
**缺点**：无法利用 Claude 的交互式能力（如需要用户确认的操作）。

**改动范围**：
- `claude.py`：直接调用 `claude -p`，不再 spawn runner.py（消除一层子进程）
- 新增 `claude_result.py`：结构化返回 `ClaudeResult`
- JSON 解析支持单行和 NDJSON 两种格式

**核心实现**：
```python
@dataclass
class ClaudeResult:
    result: str = ""
    error: str = ""
    session_id: str = ""
    cost_usd: float = 0.0
    duration_secs: float = 0.0
    is_error: bool = False

async def run_claude(prompt, work_dir, model=None, session_id=None,
                     system_prompt=None, timeout=600) -> ClaudeResult:
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if session_id:
        cmd += ["--resume", "--session-id", session_id, "--fork-session"]
    if model:
        cmd += ["--model", model]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=PIPE, stderr=PIPE,
        cwd=work_dir, start_new_session=True  # 进程组隔离
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.terminate()  # SIGTERM 优先
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            os.killpg(proc.pid, signal.SIGKILL)  # 兜底杀进程组
        return ClaudeResult(error="timeout", is_error=True)

    return parse_claude_json(stdout, stderr, proc.returncode)
```

---

### 路线二：TTY 模式重构（适用于需要持久上下文的场景）

借鉴 ccb，重写 TTY 模式，引入协议标记和 session rebinding。

**优点**：保留交互式能力，持久 session 上下文。
**缺点**：实现复杂度高，但 ccb 已验证可行。

**改动范围**：
- 引入 `ccb_protocol` 式的请求-响应标记
- session 文件持久化 + rebinding 逻辑
- pane 健康检查（每 2s 检测 is_alive）
- anchor 宽限期替代 idle timeout

**核心流程**：
```
1. 生成唯一 req_id
2. 包装 prompt：加入 CCB_REQ_ID 和 CCB_DONE 指令
3. 通过 terminal backend 注入 pane
4. 轮询日志：
   a. 检测 anchor（req_id 出现在 user 消息中）→ 确认请求被接收
   b. 如果 1.5s 内无 anchor → rebind 到最新 session
   c. 收集 assistant chunks 直到 CCB_DONE 出现
5. 返回 CaskdResult（包含 anchor_ms, done_ms 等诊断指标）
```

---

### 路线三：Codex 专用 Tool

借鉴 ccb 的 `codex_comm.py`，为 Codex 实现专用 Tool。

**两种选择**：
- **简单版**：`codex exec "<prompt>"` 非交互模式（类似 codes 对 Claude 的做法）
- **完整版**：仿照 ccb 的 Codex 日志读取器，支持 session 追踪

---

## 六、改进优先级排序

| 优先级 | 方案 | 解决的问题 | 复杂度 |
|--------|------|-----------|--------|
| **P0** | 路线一：`-p` 模式优先 | 问题 1,2,3,4 | 低 |
| **P1** | 进程生命周期管理 | 问题 3 | 低 |
| **P2** | 错误处理结构化 | 问题 5 | 低 |
| **P3** | Codex 专用 Tool | 问题 6 | 中 |
| **P4** | 路线二：TTY 模式重构 | 长上下文场景 | 高 |
| **P5** | session 并发保护 | 数据完整性 | 中 |

**建议**：P0 + P1 + P2 合并实施，这三项改动量小但覆盖了绝大部分稳定性问题。
P3 独立实施。P4 作为长期优化。

---

## 七、不建议迁移的特性

| 来源 | 特性 | 原因 |
|------|------|------|
| codes | 3 秒轮询循环 | aether-bot 是 async/await 架构，轮询是倒退 |
| codes | 文件系统做数据库 | aether-bot 已有 session/memory 系统 |
| codes | MCP Server 模式 | aether-bot 是主控方，不需要暴露为 MCP 工具 |
| codes | `dangerously-skip-permissions` | 安全风险过高 |
| ccb | 完整的终端 UI 布局引擎 | aether-bot 不需要可视化 pane 管理 |
| ccb | WezTerm backend | aether-bot 主要运行在服务端，不需要 GUI 终端 |
| ccb | i18n 国际化 | 过度设计 |

---

## 八、附录 A：代码级 Bug 清单

### Bug 1：`claude_tty_bridge.py:199` — 变量丢失
```python
# 当前代码（错误）：
carry + data        # 表达式结果未赋值
lines = buf.split(b"\n")  # buf 未定义

# 应改为：
buf = carry + data
lines = buf.split(b"\n")
```

### Bug 2：`claude_tty_bridge.py:207-208` — 死代码
```python
events.append(("user", user_msg))
continue            # continue 跳过了下面的 assistant 检查
assistant_msg = _extract_message(entry, "assistant")  # 永远不会执行
```

### Bug 3：`claude_tty_bridge.py:107,112` — 方法重复定义
```python
# 第 107 行和第 112 行各定义了一个 _project_dir 方法
# 第二个定义覆盖了第一个
```

### Bug 4：`claude.py:96,106-108` — 重复行
```python
mode_ = mode or self.mode    # 重复赋值
```
```python
cmd = [
    "python3",
    "python3",   # 重复的 "python3"
    "-m",
```

---

## 附录 B：ccb 值得学习的工程实践

### B1. 请求 ID 的唯一性设计
```
格式：YYYYMMDD-HHMMSS-mmm-PID-counter
示例：20260215-143000-123-12345-0
```
- 时间戳到毫秒 → 人类可读，便于调试
- PID → 区分不同进程的请求
- counter → 同一进程内的请求序号

### B2. 三级 Session 查找
```
1. preferred_session（上次成功绑定的 session 路径）
2. sessions-index.json（Claude 官方索引，按 mtime 排序 + projectPath 匹配）
3. 目录扫描 *.jsonl（兜底，按 mtime 取最新）
```
每一级失败都有明确的降级路径，不会静默失败。

### B3. Daemon 自治模式
```python
# 三层防护确保 daemon 不会成为僵尸：
1. idle_timeout（默认 60s 无请求自动退出）
2. parent_pid 监控（父进程死亡则退出）
3. 文件锁单实例（prevent 竞争）
```

### B4. 原子文件写入
```python
def safe_write_session(path, content):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)  # 原子替换
```

### B5. 结构化诊断能力
每次请求返回 `anchor_ms`（请求接收延迟）和 `done_ms`（完成延迟），
可以追踪性能退化趋势，而不仅仅是"成功/失败"二元结果。

---

*报告��成时间：2026-02-15*
*基于 aether-bot develop 分支 (535f2c6)、codes 项目、ccb v5.2.5 源码分析*
