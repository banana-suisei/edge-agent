from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus

import yaml


@dataclass
class ModelConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model_name: str = "gpt-4.1"
    temperature: float = 0.7
    max_tokens: int = 4096

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, self.api_key_env)


@dataclass
class AgentConfig:
    system_prompt: str = "你是一个智能助手。"
    skills_dir: str = ".skill"
    max_iterations: int = 10


@dataclass
class McpConfig:
    config_file: str = "mcp_servers.json"


@dataclass
class PostgresConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = "postgres"
    database: str = "plush_agent"
    sslmode: str = "prefer"

    @property
    def connection_string(self) -> str:
        user = quote_plus(self.user)
        password = quote_plus(self.password)
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{self.database}?sslmode={self.sslmode}"


@dataclass
class MemoryConfig:
    type: str = "postgres"
    postgres: PostgresConfig = field(default_factory=PostgresConfig)


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class AutoApproveRule:
    tool: str
    args_patterns: list[str] = field(default_factory=list)


@dataclass
class HitlConfig:
    auto_approve: list[AutoApproveRule] = field(default_factory=list)


@dataclass
class FormConfig:
    default_timeout: int = 300


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    hitl: HitlConfig = field(default_factory=HitlConfig)
    form: FormConfig = field(default_factory=FormConfig)


def load_config(path: str | Path) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    cfg = Config()

    if "model" in raw:
        m = raw["model"]
        cfg.model = ModelConfig(
            base_url=m.get("base_url", cfg.model.base_url),
            api_key_env=m.get("api_key_env", cfg.model.api_key_env),
            model_name=m.get("model_name", cfg.model.model_name),
            temperature=m.get("temperature", cfg.model.temperature),
            max_tokens=m.get("max_tokens", cfg.model.max_tokens),
        )

    if "agent" in raw:
        a = raw["agent"]
        cfg.agent = AgentConfig(
            system_prompt=a.get("system_prompt", cfg.agent.system_prompt),
            skills_dir=a.get("skills_dir", cfg.agent.skills_dir),
            max_iterations=a.get("max_iterations", cfg.agent.max_iterations),
        )

    if "mcp" in raw:
        cfg.mcp = McpConfig(config_file=raw["mcp"].get("config_file", cfg.mcp.config_file))

    if "memory" in raw:
        mem = raw["memory"]
        cfg.memory = MemoryConfig(type=mem.get("type", cfg.memory.type))
        if "postgres" in mem:
            p = mem["postgres"]
            cfg.memory.postgres = PostgresConfig(
                host=p.get("host", "localhost"),
                port=p.get("port", 5432),
                user=p.get("user", "postgres"),
                password=p.get("password", "postgres"),
                database=p.get("database", "plush_agent"),
                sslmode=p.get("sslmode", "prefer"),
            )

    if "server" in raw:
        s = raw["server"]
        cfg.server = ServerConfig(
            host=s.get("host", cfg.server.host),
            port=s.get("port", cfg.server.port),
        )

    if "hitl" in raw:
        rules = []
        for r in raw["hitl"].get("auto_approve", []):
            rules.append(AutoApproveRule(
                tool=r["tool"],
                args_patterns=r.get("args_patterns", []),
            ))
        cfg.hitl = HitlConfig(auto_approve=rules)

    if "form" in raw:
        cfg.form = FormConfig(
            default_timeout=raw["form"].get("default_timeout", cfg.form.default_timeout),
        )

    return cfg
