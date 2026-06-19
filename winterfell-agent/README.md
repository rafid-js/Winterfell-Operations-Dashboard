# Winterfell Agent

AI agent that receives product photos via Telegram, analyzes them with Claude
Vision, generates WooCommerce product content, stages a draft for approval,
and notifies Rafid via Telegram before anything goes live.

This is the foundation of a larger multi-agent system (`agents/`). Today only
`product_agent` is built — `ads_agent`, `inventory_agent`, `finance_agent`,
and `orders_agent` are empty placeholders for future work.

## Project structure

```
winterfell-agent/
├── main.py              # FastAPI app, Telegram webhook receiver
├── orchestrator.py       # Routes incoming messages to the right agent
├── agents/
│   ├── product_agent.py  # Built — photo → content → draft → publish pipeline
│   ├── ads_agent.py       # placeholder
│   ├── inventory_agent.py # placeholder
│   ├── finance_agent.py   # placeholder
│   └── orders_agent.py    # placeholder
├── tools/
│   ├── vision.py          # Claude Vision product analysis
│   ├── content.py         # Claude product copywriting
│   ├── woocommerce.py     # WooCommerce REST API
│   ├── brain.py           # PostgreSQL persistence
│   └── telegram.py        # Telegram Bot API
├── memory/
│   └── memory.py          # Shared learnings store, used by all agents
├── config.py
├── requirements.txt
└── Procfile
```

## Approval gate

`create_woocommerce_draft`, `publish_product`, and `delete_product` never run
automatically. The agent stages them in the `pending_actions` table and asks
Rafid to reply `yes` on Telegram before executing. Replying `yes` with extra
text (e.g. "yes, price should be 1100") also saves a correction to
`agent_memory`, which gets injected into future prompts.

## Railway deployment

1. Push code to GitHub.
2. New Railway project → Deploy from GitHub repo, root directory `winterfell-agent/`.
3. Add all environment variables (see below) in the Railway dashboard.
4. Get the Railway public URL for this service.
5. Set the Telegram webhook:
   ```
   https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url=https://{railway-url}/webhook/telegram
   ```
6. Send a test product photo to the bot. You should get a review message
   within ~30 seconds.

## Environment variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Rafid's personal Telegram chat ID — only this ID is served |
| `WOOCOMMERCE_URL` | e.g. `https://winterfell.com.bd` |
| `WOOCOMMERCE_KEY` | WooCommerce REST API consumer key |
| `WOOCOMMERCE_SECRET` | WooCommerce REST API consumer secret |
| `BRAIN_DATABASE_URL` | PostgreSQL connection string (Winterfell Brain on Railway) |
| `CAT_CARGO_PANTS` | WooCommerce category ID |
| `CAT_DROP_SHOULDER_TEE` | WooCommerce category ID |
| `CAT_DENIM` | WooCommerce category ID |
| `CAT_KNIT_POLO` | WooCommerce category ID |
| `CAT_JOGGER` | WooCommerce category ID |
| `CAT_JACKET` | WooCommerce category ID |
| `CAT_OTHER` | WooCommerce category ID |

## Security

- Only messages from `TELEGRAM_CHAT_ID` are processed — everything else is ignored silently.
- Images over 20MB are rejected before processing.
- All database writes use parameterized queries.
- API keys and secrets are never logged.
