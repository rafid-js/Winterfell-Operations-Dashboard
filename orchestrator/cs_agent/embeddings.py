"""OpenAI embeddings for product image-matching.

Anthropic has no embeddings endpoint, so the pgvector index is populated with
OpenAI text-embedding-3-small (1536-dim — matches Brain's existing vector(1536)
columns). This is the only non-Anthropic model call in the CS agent.
"""
from . import config

_client = None


def _get_client():
    global _client
    if _client is None:
        import openai
        _client = openai.OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def embed(text_input: str):
    """Return a 1536-dim embedding for the given text, or None on failure."""
    if not config.OPENAI_API_KEY:
        print('  ⚠ OPENAI_API_KEY not set — embedding skipped.', flush=True)
        return None
    try:
        resp = _get_client().embeddings.create(
            model=config.EMBEDDING_MODEL,
            input=text_input,
        )
        return resp.data[0].embedding
    except Exception as e:  # noqa: BLE001
        print(f'  ⚠ Embedding failed: {e}', flush=True)
        return None


def to_pgvector(vec) -> str:
    """Format a float list as a pgvector literal, e.g. '[0.1,0.2,...]'."""
    return '[' + ','.join(repr(float(x)) for x in vec) + ']'
