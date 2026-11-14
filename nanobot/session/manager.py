"""会话管理，用于对话历史的持久化

本模块提供会话（Session）的管理功能，包括:
- 创建和加载会话
- 存储对话历史（JSONL格式）
- 会话切换和索引管理
- 多渠道会话隔离

会话的作用:
- 保存用户与代理的完整对话历史
- 支持上下文连贯的多轮对话
- 允许用户在不同时间继续同一会话

存储格式:
- 使用JSONL（JSON Lines）格式存储
- 每行一个JSON对象（元数据行或消息行）
- 易于追加、易于解析、易于调试

会话键格式:
- 基础键: "channel:chat_id"（如"telegram:123456"）
- 完整键: "channel:chat_id#timestamp"（如"telegram:123456#20240101120000"）
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, get_sessions_path, safe_filename


@dataclass
class Session:
    """会话对象，存储单个对话的历史

    会话以JSONL格式持久化到磁盘，便于读取和追加。

    会话键（key）的两种格式:
    1. 基础键: "channel:chat_id"（如"telegram:123456"）
    2. 完整键: "channel:chat_id#timestamp"（如"telegram:123456#20240101120000"）

    完整键用于支持多会话切换，同一个基础键可以有多个会话。

    Attributes:
        key: 会话键（基础键或完整键）
        messages: 消息列表（每条消息包含role、content、timestamp等）
        created_at: 创建时间
        updated_at: 最后更新时间
        metadata: 会话元数据（如LLM会话状态、摘要信息等）
    """

    key: str  # 会话键: channel:chat_id 或 channel:chat_id#timestamp
    messages: list[dict[str, Any]] = field(default_factory=list)  # 消息历史
    created_at: datetime = field(default_factory=datetime.now)  # 创建时间
    updated_at: datetime = field(default_factory=datetime.now)  # 最后更新时间
    metadata: dict[str, Any] = field(default_factory=dict)  # 元数据

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """向会话添加一条消息

        Args:
            role: 角色（"user"、"assistant"、"system"、"tool"）
            content: 消息内容
            **kwargs: 额外字段（如media、tool_call_id等）
        """
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
        """获取用于LLM上下文的消息历史

        只返回最近的max_messages条消息，并转换为LLM格式
        （仅保留role和content字段）。

        Args:
            max_messages: 返回的最大消息数

        Returns:
            list[dict]: LLM格式的消息列表

        Example:
            ```python
            history = session.get_history(max_messages=10)
            # [
            #     {"role": "user", "content": "Hello"},
            #     {"role": "assistant", "content": "Hi!"}
            # ]
            ```
        """
        # 获取最近的消息
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages

        # 转换为LLM格式（仅保留role和content）
        return [{"role": m["role"], "content": m["content"]} for m in recent]

    def clear(self) -> None:
        """清空会话中的所有消息"""
        self.messages = []
        self.updated_at = datetime.now()


class SessionManager:
    """会话管理器

    负责管理所有会话的创建、加载、保存和删除。

    核心功能:
    1. **会话持久化**: 以JSONL格式存储到~/.aether-bot/sessions/
    2. **活动会话索引**: 使用active.json映射基础键到完整键
    3. **会话切换**: 支持同一渠道有多个会话，通过/new命令创建新会话
    4. **缓存管理**: 在内存中缓存活动会话，提高性能

    目录结构:
    ```
    ~/.aether-bot/sessions/
    ├── active.json                      # 活动会话索引
    ├── telegram_123456.jsonl            # 基础会话文件（兼容旧版）
    └── telegram_123456#20240101120000.jsonl  # 带时间戳的会话文件
    ```

    active.json格式:
    ```json
    {
        "telegram:123456": "telegram:123456#20240101120000",
        "slack:789": "slack:789#20240102150000"
    }
    ```

    工作流程:
    1. 用户发送消息
    2. 使用基础键（telegram:123456）查找活动会话
    3. 从active.json获取完整键
    4. 加载或创建对应的会话
    5. 处理消息并保存

    Attributes:
        workspace: 工作空间路径
        sessions_dir: 会话文件存储目录
        _active_index_path: 活动会话索引文件路径
        _active_sessions: 活动会话映射（基础键 → 完整键）
        _cache: 会话缓存（完整键 → Session对象）
    """

    def __init__(self, workspace: Path):
        """初始化会话管理器

        Args:
            workspace: 工作空间路径（当前未使用，保留用于未来扩展）
        """
        self.workspace = workspace
        self.sessions_dir = ensure_dir(Path.home() / ".aether-bot" / "sessions")
        self._active_index_path = get_sessions_path() / "active.json"
        self._active_sessions: dict[str, str] = {}  # 基础键 → 完整键
        self._load_active_index()
        self._cache: dict[str, Session] = {}  # 完整键 → Session对象

    def _load_active_index(self) -> None:
        """从磁盘加载活动会话索引

        读取active.json文件，构建基础键到完整键的映射。
        """
        if not self._active_index_path.exists():
            return
        try:
            with open(self._active_index_path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                old = dict(self._active_sessions)
                self._active_sessions = {str(k): str(v) for k, v in data.items()}
                # 仅记录实际的运行时变更（跳过首次从None加载）
                for k in set(list(old.keys()) + list(self._active_sessions.keys())):
                    ov = old.get(k)
                    nv = self._active_sessions.get(k)
                    if ov != nv and ov is not None:
                        logger.debug(f"active_index changed: {k}: {ov} -> {nv}")
        except Exception as e:
            logger.warning(f"Failed to load session index: {e}")

    def _save_active_index(self) -> None:
        """保存活动会话索引到磁盘"""
        try:
            with open(self._active_index_path, "w") as f:
                json.dump(self._active_sessions, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save session index: {e}")

    def _make_session_key(self, base_key: str) -> str:
        """生成带时间戳的完整会话键

        Args:
            base_key: 基础键（如"telegram:123456"）

        Returns:
            str: 完整键（如"telegram:123456#20240101120000"）
        """
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{base_key}#{stamp}"

    def _get_active_key(self, base_key: str) -> str | None:
        """获取基础键对应的活动会话完整键

        Args:
            base_key: 基础键

        Returns:
            str | None: 完整键，如果不存在则返回None
        """
        return self._active_sessions.get(base_key)

    def _set_active_key(self, base_key: str, session_key: str) -> None:
        """设置基础键的活动会话

        Args:
            base_key: 基础键
            session_key: 完整会话键
        """
        old = self._active_sessions.get(base_key)
        self._active_sessions[base_key] = session_key
        if old != session_key:
            logger.debug(f"_set_active_key: {base_key}: {old} -> {session_key}")
        self._save_active_index()

    def _get_session_path(self, key: str) -> Path:
        """获取会话文件的路径

        将会话键转换为安全的文件名，冒号替换为下划线。

        Args:
            key: 会话键

        Returns:
            Path: 会话文件路径
        """
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """获取或创建会话

        每次调用都会重新加载active.json，以便捕获外部变更
        （如Web渠道切换会话）。

        键的两种处理方式:
        1. **完整键**（包含#）: 直接解析，不依赖活动索引
        2. **基础键**: 从active.json查找对应的完整键

        Args:
            key: 会话键（基础键或完整键）

        Returns:
            Session: 会话对象

        Example:
            ```python
            # 使用基础键
            session = manager.get_or_create("telegram:123456")

            # 使用完整键
            session = manager.get_or_create("telegram:123456#20240101120000")
            ```
        """
        # 完整会话键（包含#timestamp）- 直接解析，不使用活动索引
        if "#" in key:
            if key in self._cache:
                logger.debug(f"get_or_create direct hit cache: {key}")
                return self._cache[key]
            session = self._load(key)
            if session is not None:
                self._cache[key] = session
                logger.debug(f"get_or_create direct loaded: {key} msgs={len(session.messages)}")
                return session
            # 文件不存在 - 创建新会话
            session = Session(key=key)
            self._cache[key] = session
            logger.debug(f"get_or_create direct created: {key}")
            return session

        # 基础键 - 重新加载活动索引以捕获外部变更
        self._load_active_index()

        # 检查缓存中的活动会话
        active_key = self._get_active_key(key)
        logger.debug(
            f"get_or_create key={key} active_key={active_key} "
            f"cached={active_key in self._cache if active_key else 'n/a'}"
        )
        if active_key and active_key in self._cache:
            return self._cache[active_key]

        # 活动键已变更或未缓存 - 从磁盘加载
        if active_key:
            session = self._load(active_key)
            if session is None:
                session = Session(key=active_key)
            self._cache[active_key] = session
            logger.debug(f"get_or_create loaded from disk: {active_key} msgs={len(session.messages)}")
            return session

        # 向后兼容: 如果基础会话文件存在，使用它
        legacy = self._load(key)
        if legacy is not None:
            self._set_active_key(key, key)
            self._cache[key] = legacy
            return legacy

        # 为此基础键创建新会话
        new_key = self._make_session_key(key)
        session = Session(key=new_key)
        self._set_active_key(key, new_key)
        self._cache[new_key] = session
        return session

    def start_new(self, base_key: str) -> Session:
        """为基础键创建并激活新会话

        当用户发送/new命令时调用，创建一个新会话并设为活动。

        Args:
            base_key: 基础键（如"telegram:123456"）

        Returns:
            Session: 新创建的会话
        """
        new_key = self._make_session_key(base_key)
        session = Session(key=new_key)
        self._set_active_key(base_key, new_key)
        self._cache[new_key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """从磁盘加载会话

        读取JSONL文件，解析元数据行和消息行。

        JSONL格式:
        ```
        {"_type": "metadata", "created_at": "...", "metadata": {...}}
        {"role": "user", "content": "...", "timestamp": "..."}
        {"role": "assistant", "content": "...", "timestamp": "..."}
        ```

        Args:
            key: 会话键

        Returns:
            Session | None: 会话对象，如果文件不存在或加载失败则返回None
        """
        path = self._get_session_path(key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None

            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        # 元数据行
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                    else:
                        # 消息行
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata
            )
        except Exception as e:
            logger.warning(f"Failed to load session {key}: {e}")
            return None

    def save(self, session: Session) -> None:
        """保存会话到磁盘

        以JSONL格式写入文件:
        1. 第一行是元数据
        2. 后续每行是一条消息

        Args:
            session: 要保存的会话对象
        """
        path = self._get_session_path(session.key)

        with open(path, "w") as f:
            # 首先写入元数据行
            metadata_line = {
                "_type": "metadata",
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata
            }
            f.write(json.dumps(metadata_line) + "\n")

            # 写入消息行
            for msg in session.messages:
                f.write(json.dumps(msg) + "\n")

        # 更新缓存
        self._cache[session.key] = session

    def delete(self, key: str) -> bool:
        """删除会话

        同时删除:
        1. 缓存中的会话对象
        2. 活动索引中的条目
        3. 磁盘上的JSONL文件

        Args:
            key: 会话键

        Returns:
            bool: True表示删除成功，False表示会话不存在
        """
        # 从缓存中移除
        self._cache.pop(key, None)
        # 如果需要，移除活动指针
        base_to_remove = None
        for base_key, active_key in self._active_sessions.items():
            if active_key == key:
                base_to_remove = base_key
                break
        if base_to_remove:
            self._active_sessions.pop(base_to_remove, None)
            self._save_active_index()

        # 删除文件
        path = self._get_session_path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有会话

        扫描sessions目录中的所有.jsonl文件，读取元数据。

        Returns:
            list[dict]: 会话信息列表，按更新时间降序排列

        Example:
            ```python
            sessions = manager.list_sessions()
            # [
            #     {
            #         "key": "telegram:123456#20240101120000",
            #         "created_at": "2024-01-01T12:00:00",
            #         "updated_at": "2024-01-01T13:30:00",
            #         "path": "/path/to/sessions/telegram_123456#20240101120000.jsonl"
            #     },
            #     ...
            # ]
            ```
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # 仅读取元数据行
                with open(path) as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            sessions.append({
                                "key": path.stem.replace("_", ":"),
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
