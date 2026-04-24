"""
Tool implementations for the evaluated agent.

The agent's file_read and file_write tools are sandboxed to app_dir.
Any path that resolves outside app_dir is rejected with an error.
shell_exec runs with cwd=app_dir.
corpus_search queries the frozen local corpus using sentence-transformers.
stripe_api provides read-only Stripe API access for the agent to self-verify.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional

import stripe as stripe_lib


# ---------------------------------------------------------------------------
# Path sandbox guard
# ---------------------------------------------------------------------------

def _safe_path(base_dir: str, requested: str) -> Optional[Path]:
    """
    Resolve requested path relative to base_dir.
    Returns None if the resolved path escapes base_dir.
    """
    base = Path(base_dir).resolve()
    target = (base / requested).resolve()
    try:
        target.relative_to(base)
        return target
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# file_read
# ---------------------------------------------------------------------------

def tool_file_read(path: str, app_dir: str) -> dict[str, Any]:
    safe = _safe_path(app_dir, path)
    if safe is None:
        return {"content": "", "error": f"Access denied: path '{path}' is outside the working directory"}
    if not safe.exists():
        return {"content": "", "error": f"File not found: {path}"}
    try:
        return {"content": safe.read_text(encoding="utf-8"), "error": None}
    except Exception as e:
        return {"content": "", "error": str(e)}


# ---------------------------------------------------------------------------
# file_write
# ---------------------------------------------------------------------------

def tool_file_write(path: str, content: str, app_dir: str) -> dict[str, Any]:
    safe = _safe_path(app_dir, path)
    if safe is None:
        return {"success": False, "error": f"Access denied: path '{path}' is outside the working directory"}
    try:
        safe.parent.mkdir(parents=True, exist_ok=True)
        safe.write_text(content, encoding="utf-8")
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# shell_exec
# ---------------------------------------------------------------------------

def tool_shell_exec(command: str, app_dir: str, timeout_seconds: int = 30, env: Optional[dict] = None) -> dict[str, Any]:
    merged_env = {**os.environ, **(env or {})}
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=merged_env,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Command timed out after {timeout_seconds}s", "exit_code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


# ---------------------------------------------------------------------------
# stripe_api (read-only)
# ---------------------------------------------------------------------------

def tool_stripe_api(
    method: str,
    api_key: str,
    session_id: Optional[str] = None,
    limit: int = 10,
) -> dict[str, Any]:
    stripe_lib.api_key = api_key
    try:
        if method == "list_sessions":
            result = stripe_lib.checkout.Session.list(limit=limit)
            return {"result": result.to_dict_recursive(), "error": None}
        elif method == "retrieve_session":
            if not session_id:
                return {"result": {}, "error": "session_id required for retrieve_session"}
            result = stripe_lib.checkout.Session.retrieve(session_id)
            return {"result": result.to_dict_recursive(), "error": None}
        elif method == "retrieve_payment_intent":
            if not session_id:
                return {"result": {}, "error": "session_id (payment_intent_id) required"}
            result = stripe_lib.PaymentIntent.retrieve(session_id)
            return {"result": result.to_dict_recursive(), "error": None}
        else:
            return {"result": {}, "error": f"Unknown method: {method}"}
    except stripe_lib.error.StripeError as e:
        return {"result": {}, "error": str(e)}


# ---------------------------------------------------------------------------
# corpus_search
# ---------------------------------------------------------------------------

_corpus_index: Optional[Any] = None
_corpus_texts: Optional[list[dict]] = None


def _load_corpus(corpus_dir: str) -> tuple[Any, list[dict]]:
    """
    Lazy-load the corpus on first call. Builds a sentence-transformers index
    over all .txt files in corpus_dir.
    Falls back to simple TF-IDF-style keyword search if sentence-transformers
    is unavailable (avoids blocking if model download fails).
    """
    global _corpus_index, _corpus_texts
    if _corpus_texts is not None:
        return _corpus_index, _corpus_texts

    corpus_path = Path(corpus_dir)
    docs = []
    for txt_file in sorted(corpus_path.glob("*.txt")):
        text = txt_file.read_text(encoding="utf-8", errors="replace")
        # Split into ~500-char chunks with overlap
        chunks = _chunk_text(text, chunk_size=500, overlap=50)
        for chunk in chunks:
            docs.append({"passage": chunk, "source": txt_file.stem})

    _corpus_texts = docs

    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        model = SentenceTransformer("all-MiniLM-L6-v2")
        embeddings = model.encode([d["passage"] for d in docs], show_progress_bar=False)
        _corpus_index = (model, np.array(embeddings))
    except Exception:
        # Fallback: no embedding model; use keyword scoring
        _corpus_index = None

    return _corpus_index, _corpus_texts


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    words = text.split()
    chunks = []
    step = chunk_size - overlap
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk:
            chunks.append(chunk)
    return chunks


def _keyword_score(query: str, passage: str) -> float:
    query_words = set(query.lower().split())
    passage_lower = passage.lower()
    matches = sum(1 for w in query_words if w in passage_lower)
    return matches / max(len(query_words), 1)


def tool_corpus_search(query: str, corpus_dir: str, top_k: int = 5) -> dict[str, Any]:
    try:
        index, docs = _load_corpus(corpus_dir)
    except Exception as e:
        return {"results": [], "error": f"Failed to load corpus: {e}"}

    if index is not None:
        model, embeddings = index
        import numpy as np
        q_emb = model.encode([query], show_progress_bar=False)
        # Cosine similarity
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        q_norm = np.linalg.norm(q_emb)
        similarities = (embeddings @ q_emb.T).flatten() / (norms.flatten() * q_norm + 1e-9)
        top_indices = similarities.argsort()[::-1][:top_k]
        results = [
            {
                "passage": docs[i]["passage"],
                "source": docs[i]["source"],
                "score": float(similarities[i]),
            }
            for i in top_indices
        ]
    else:
        # Keyword fallback
        scored = [
            {
                "passage": d["passage"],
                "source": d["source"],
                "score": _keyword_score(query, d["passage"]),
            }
            for d in docs
        ]
        scored.sort(key=lambda x: x["score"], reverse=True)
        results = scored[:top_k]

    return {"results": results, "error": None}
