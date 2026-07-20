from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./()-]*|\d+(?:\.\d+)*|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """Return stable English tokens plus Chinese unigram/bigram tokens for FTS5."""
    base = [token.lower() for token in TOKEN_RE.findall(text)]
    chinese = [token for token in base if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    bigrams = [chinese[index] + chinese[index + 1] for index in range(len(chinese) - 1)]
    return list(dict.fromkeys([*base, *bigrams]))


def token_document(text: str) -> str:
    return " ".join(tokenize(text))


def fts_query(text: str) -> str:
    tokens = tokenize(text)
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
