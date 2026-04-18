from __future__ import annotations

import click

from plush_agent.config import load_config


def _sanitize_surrogates(text: str) -> str:
    """Remove or replace lone surrogate codepoints that can leak in from WSL2/Windows terminal IME input."""
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


@click.group()
@click.option("--config", "config_path", default="config.yaml", help="配置文件路径")
@click.pass_context
def cli(ctx, config_path: str) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config_path)


@cli.command()
@click.pass_context
def chat(ctx) -> None:
    """启动交互式 CLI 对话"""
    config = ctx.obj["config"]
    from plush_agent.agent import build_agent

    agent, store, checkpointer = build_agent(config)
    thread_id = "cli-session"
    cfg = {"configurable": {"thread_id": thread_id}}

    click.echo("Plush Agent CLI（输入 /quit 退出）")
    while True:
        user_input = click.prompt("", default="", show_default=False)
        if not user_input or user_input.strip() == "/quit":
            break
        user_input = _sanitize_surrogates(user_input)
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            cfg,
        )
        for msg in reversed(result.get("messages", [])):
            if hasattr(msg, "content") and msg.type == "ai":
                click.echo(f"\nAgent: {msg.content}\n")
                break


@cli.command()
@click.option("--host", default=None, help="监听地址")
@click.option("--port", default=None, type=int, help="监听端口")
@click.pass_context
def serve(ctx, host: str | None, port: int | None) -> None:
    """启动 HTTP 服务"""
    config = ctx.obj["config"]
    if host:
        config.server.host = host
    if port:
        config.server.port = port
    from plush_agent.server.main import run_server
    run_server(config)


if __name__ == "__main__":
    cli()
