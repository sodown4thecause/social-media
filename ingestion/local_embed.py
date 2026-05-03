from __future__ import annotations
import math
import re
from typing import Dict, List, Iterable, Tuple

WS_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    'the','a','an','and','or','but','if','then','else','of','for','on','in','to','with','without','is','are','be','was','were','it','this','that','as','at','by','from','into','about','over','under','up','down','out','not','no','can','could','should','would','you','your','yours','we','our','ours','they','them','their','theirs','i','me','my','mine','he','she','his','her','hers','its','too','very','just','more','most','less','least','also'
}


def tokenize(text: str) -> List[str]:
    text = text.lower()
    # strip urls crudely
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    toks = TOKEN_RE.findall(text)
    return [t for t in toks if t not in STOPWORDS and len(t) > 2]


class LocalEmbedder:
    """
    Lightweight TF-IDF embedder with cosine similarity.
    Vocab is built from seed exemplars and optional extra_docs (e.g., sampled posts).
    """
    def __init__(self, seed_exemplars: Dict[str, List[str]], extra_docs: Iterable[str] | None = None):
        self.seed_exemplars = seed_exemplars
        self.docs_tokens: List[List[str]] = []
        for _cluster, seeds in seed_exemplars.items():
            for s in seeds:
                self.docs_tokens.append(tokenize(s))
        if extra_docs:
            for d in extra_docs:
                self.docs_tokens.append(tokenize(d))
        # build vocab and idf
        self.vocab: Dict[str, int] = {}
        df: Dict[str, int] = {}
        for toks in self.docs_tokens:
            seen = set(toks)
            for t in seen:
                df[t] = df.get(t, 0) + 1
        # sort tokens for deterministic indices
        all_terms = sorted(df.keys())
        for idx, t in enumerate(all_terms):
            self.vocab[t] = idx
        N = max(1, len(self.docs_tokens))
        self.idf: List[float] = [0.0] * len(self.vocab)
        for t, idx in self.vocab.items():
            dfi = df.get(t, 0)
            self.idf[idx] = math.log((1.0 + N) / (1.0 + dfi)) + 1.0
        self.dim = len(self.vocab)

    def _vec(self, toks: List[str]) -> List[float]:
        if self.dim == 0:
            return []
        tf: Dict[int, float] = {}
        for t in toks:
            idx = self.vocab.get(t)
            if idx is not None:
                tf[idx] = tf.get(idx, 0.0) + 1.0
        if not tf:
            return [0.0] * self.dim
        # l2-normalized tf-idf
        vec = [0.0] * self.dim
        max_tf = max(tf.values())
        for idx, cnt in tf.items():
            tf_norm = cnt / max_tf
            vec[idx] = tf_norm * self.idf[idx]
        # l2 normalize
        s = math.sqrt(sum(x * x for x in vec))
        if s > 0:
            vec = [x / s for x in vec]
        return vec

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(tokenize(t)) for t in texts]

    def seed_vectors(self) -> Dict[str, List[List[float]]]:
        out: Dict[str, List[List[float]]] = {}
        for cluster, seeds in self.seed_exemplars.items():
            out[cluster] = self.embed_texts(seeds)
        return out
