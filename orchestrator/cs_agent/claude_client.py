"""Shared Anthropic client for the CS agent — uses CS_ANTHROPIC_API_KEY.

Kept separate from orchestrator/agent_tools so the CS workload never touches the
product agent's key. If the key is missing we still construct the client (the
SDK reads ANTHROPIC_API_KEY as a last resort) but log loudly, because the whole
point is an independent key.
"""
from anthropic import Anthropic

from . import config

if not config.CS_ANTHROPIC_API_KEY:
    print('  ⚠ CS_ANTHROPIC_API_KEY not set — CS agent Claude calls will fail. '
          'Set a dedicated key (separate from the product agent).', flush=True)

client = Anthropic(api_key=config.CS_ANTHROPIC_API_KEY)
