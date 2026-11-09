"""Errand Guard for Hermes — registration.

Registers the errand tools, the guard hooks, the /errands command and the skill.
Nothing touches the network or the database here; both open on first use.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import commands, hooks, schemas, settings, shop, tools

logger = logging.getLogger(__name__)

TOOLSET = "errand_guard"

_HANDLERS = {
    "errand_create": tools.errand_create,
    "errand_confirm": tools.errand_confirm,
    "errand_list": tools.errand_list,
    "errand_cancel": tools.errand_cancel,
    "errand_search_products": shop.errand_search_products,
    "errand_quote": shop.errand_quote,
    "errand_buy": shop.errand_buy,
    "errand_status": shop.errand_status,
    "errand_receipt": shop.errand_receipt,
}


def register(ctx) -> None:
    settings.bind_context(ctx)
    for schema in schemas.ALL:
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema,
                          handler=_HANDLERS[schema["name"]], emoji="🛡️")

    ctx.register_hook("pre_tool_call", hooks.on_pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.on_post_tool_call)
    ctx.register_hook("post_approval_response", hooks.on_post_approval_response)

    ctx.register_command("errands", handler=commands.handle_errands,
                         description="Errand Guard: errands, receipts and blocked payments",
                         args_hint="[show <id> | receipts | blocked | code <id> | help]")

    skill_md = Path(__file__).parent / "skills" / "errand-guard" / "SKILL.md"
    if skill_md.exists():
        try:
            ctx.register_skill("errand-guard", skill_md)
        except Exception as exc:  # a skill problem must not disable the guard
            logger.warning("errand-guard: could not register skill: %s", exc)
