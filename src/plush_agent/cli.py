from __future__ import annotations

import asyncio
import json
import re

import click
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.types import Command

from plush_agent.config import Config, load_config


def _sanitize_surrogates(text: str) -> str:
    """Remove or replace lone surrogate codepoints that can leak in from WSL2/Windows terminal IME input."""
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


def _should_auto_approve(config: Config, tool_name: str, tool_args: dict) -> bool:
    for rule in config.hitl.auto_approve:
        if rule.tool != tool_name:
            continue
        args_str = str(tool_args)
        for pattern in rule.args_patterns:
            if re.search(pattern, args_str):
                return True
    return False


@click.group()
@click.option("--config", "config_path", default="config.yaml", help="配置文件路径")
@click.pass_context
def cli(ctx, config_path: str) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config_path)


def _handle_cli_hitl(agent, result, cfg, config: Config):
    while result.interrupts:
        interrupt = result.interrupts[0]
        action_requests = interrupt.value["action_requests"]
        decisions = []

        for action in action_requests:
            name = action["name"]
            args = action["args"]

            if _should_auto_approve(config, name, args):
                decisions.append({"type": "approve"})
                continue

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


async def _stream_cli(agent, input_data, cfg, config: Config):
    """Stream agent response to CLI with HITL support.

    Uses a while loop: stream tokens until an interrupt or end.
    On interrupt: auto-approve matching tools, prompt for the rest.
    If all auto-approved, resume streaming immediately.
    If human input needed, fall back to sync invoke for the remainder.
    """
    tool_call_acc: dict[str, dict] = {}
    stream_input = input_data

    while True:
        interrupt_info = None

        async for chunk in agent.astream(
            stream_input,
            config=cfg,
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            chunk_type = chunk.get("type")

            if chunk_type == "messages":
                token, metadata = chunk["data"]
                if isinstance(token, AIMessageChunk):
                    if token.content:
                        click.echo(token.content, nl=False)
                    for tc in token.tool_call_chunks:
                        tc_id = tc.get("id")
                        if not tc_id:
                            continue
                        entry = tool_call_acc.setdefault(tc_id, {"name": "", "args_str": ""})
                        if tc.get("name"):
                            click.echo(f"\n  [Calling tool: {tc['name']}]", nl=False)
                        if tc.get("args"):
                            entry["args_str"] += tc["args"]

            elif chunk_type == "updates":
                update_data = chunk.get("data", {})
                if not isinstance(update_data, dict):
                    continue

                # LangGraph v2: __interrupt__ is a top-level key in update_data
                if "__interrupt__" in update_data:
                    interrupt_info = update_data["__interrupt__"]
                    break

                for node_name, node_output in update_data.items():
                    if not isinstance(node_output, dict):
                        continue
                    if node_name == "tools":
                        msgs = node_output.get("messages", [])
                        for m in msgs:
                            if isinstance(m, ToolMessage):
                                click.echo(f"\n  [Tool result: {m.content[:200]}]", nl=False)

        # No interrupt — stream ended normally
        if interrupt_info is None:
            click.echo("\n")
            return None

        # Process interrupt
        interrupt = interrupt_info[0] if isinstance(interrupt_info, (tuple, list)) else interrupt_info
        action_requests = interrupt.value.get("action_requests", [])

        decisions = []
        needs_human = False
        for action in action_requests:
            if _should_auto_approve(config, action["name"], action["args"]):
                decisions.append({"type": "approve"})
            else:
                needs_human = True
                click.echo(f"\n\u26a0 Tool call requires approval:")
                click.echo(f"  Tool: {action['name']}")
                click.echo(f"  Args: {json.dumps(action['args'], ensure_ascii=False, indent=2)}")
                while True:
                    choice = click.prompt("  Approve? [y/r(eject)]", default="y").strip().lower()
                    if choice in ("y", "yes", ""):
                        decisions.append({"type": "approve"})
                        break
                    elif choice in ("r", "reject"):
                        msg = click.prompt("  Rejection reason", default="")
                        decisions.append({"type": "reject", "message": msg})
                        break

        if needs_human:
            # Human was involved — fall back to sync for the remainder
            click.echo()
            result = await agent.ainvoke(
                Command(resume={"decisions": decisions}),
                cfg,
                version="v2",
            )
            result = _handle_cli_hitl(agent, result, cfg, config)
            _print_reply(result)
            return result

        # All auto-approved — resume streaming loop
        stream_input = Command(resume={"decisions": decisions})


@cli.command()
@click.option("--stream", is_flag=True, default=False, help="启用流式输出")
@click.pass_context
def chat(ctx, stream: bool) -> None:
    """启动交互式 CLI 对话"""
    config = ctx.obj["config"]
    from plush_agent.agent import build_agent
    from plush_agent.tools.memory import load_session_summary, save_session_summary

    agent, store, checkpointer = build_agent(config)
    thread_id = "cli-session"
    cfg = {"configurable": {"thread_id": thread_id}}

    prev_summary = load_session_summary(store)

    click.echo("Plush Agent CLI（输入 /quit 退出）")

    all_messages: list = []
    try:
        while True:
            user_input = click.prompt("", default="", show_default=False)
            if not user_input or user_input.strip() == "/quit":
                break
            user_input = _sanitize_surrogates(user_input)

            if prev_summary and not all_messages:
                enriched = (
                    f"[上一次对话摘要]\n{prev_summary}\n"
                    f"[当前消息]\n{user_input}"
                )
            else:
                enriched = user_input

            if stream:
                result = asyncio.get_event_loop().run_until_complete(
                    _stream_cli(
                        agent,
                        {"messages": [{"role": "user", "content": enriched}]},
                        cfg,
                        config,
                    )
                )
            else:
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": enriched}]},
                    cfg,
                    version="v2",
                )
                result = _handle_cli_hitl(agent, result, cfg, config)
                _print_reply(result)

            if result is not None:
                state = result.value if hasattr(result, "value") else result
                if isinstance(state, dict):
                    all_messages = state.get("messages", all_messages)
    except (KeyboardInterrupt, EOFError):
        click.echo()
    finally:
        if all_messages:
            click.echo("正在保存对话记忆...")
            try:
                save_session_summary(store, all_messages, config)
                click.echo("对话记忆已保存。")
            except Exception:
                click.echo("保存对话记忆失败。")


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
