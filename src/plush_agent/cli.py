from __future__ import annotations

import json

import click
from langgraph.types import Command

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


def _handle_cli_hitl(agent, result, cfg):
    while result.interrupts:
        interrupt = result.interrupts[0]
        action_requests = interrupt.value["action_requests"]
        decisions = []

        for action in action_requests:
            name = action["name"]
            args = action["args"]
            click.echo(f"\n\u26a0 Tool call requires approval:")
            click.echo(f"  Tool: {name}")
            click.echo(f"  Args: {json.dumps(args, ensure_ascii=False, indent=2)}")

            while True:
                choice = click.prompt("  Approve? [y/r(eject)]", default="y").strip().lower()
                if choice in ("y", "yes", ""):
                    decisions.append({"type": "approve"})
                    break
                elif choice in ("r", "reject"):
                    msg = click.prompt("  Rejection reason", default="")
                    decisions.append({"type": "reject", "message": msg})
                    break

        result = agent.invoke(
            Command(resume={"decisions": decisions}),
            cfg,
            version="v2",
        )

    return result


def _print_reply(result) -> None:
    state = result.value if hasattr(result, "value") else result
    messages = state.get("messages", []) if isinstance(state, dict) else []
    for m in reversed(messages):
        if hasattr(m, "content") and m.type == "ai":
            click.echo(f"\nAgent: {m.content}\n")
            break


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
            version="v2",
        )
        result = _handle_cli_hitl(agent, result, cfg)
        _print_reply(result)


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
