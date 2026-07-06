"""
WS-3 — WCAG Knowledge Base Vector Store

Responsibility:
    1. Load wcag_criteria.json (produced by wcag_loader.py).
    2. Create one ChromaDB collection per disability class:
       visual, auditory, motor, cognitive, at_parsing.
    3. Embed each criterion using BGE-small-en-v1.5 and store it in
       every collection whose disability_class it belongs to.
    4. Expose WCAGVectorStore with:
         retrieve_criteria(disability_class, query, n_results) -> list[dict]
         get_criterion_by_number(number)                       -> dict
    5. Persist ChromaDB to CHROMA_DB_PATH from .env.
    6. rebuild_if_empty() — only re-embeds when the index is absent or stale.

Why BGE-small-en-v1.5:
    - 384-dim embeddings: tiny footprint, runs on CPU comfortably.
    - Outperforms all-MiniLM-L6-v2 on retrieval benchmarks (MTEB).
    - "query:" prefix instruction improves asymmetric retrieval quality —
      we prefix search queries with "query:" and documents with "passage:"
      per the BGE paper recommendation.

Author : Sreekar (WS-3)
Design : Section 2.3, Section 3.1 of WCAG Audit Agent Design Document v1.0
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

# Load .env so CHROMA_DB_PATH is available
load_dotenv()

logger = logging.getLogger("vector_store")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE      = Path(__file__).resolve().parent          # backend/rag/
_REPO_ROOT = _HERE.parent.parent                      # repo root
DATA_DIR   = _REPO_ROOT / "data"

# CHROMA_DB_PATH from .env, fallback to data/chroma
_DEFAULT_CHROMA_PATH = str(DATA_DIR / "chroma")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", _DEFAULT_CHROMA_PATH)

# wcag_criteria.json produced by wcag_loader.py
CRITERIA_JSON = DATA_DIR / "wcag_criteria.json"

# ── Constants ──────────────────────────────────────────────────────────────────
# Exactly the five disability classes defined in the Design Doc Section 2.3.2
DISABILITY_CLASSES: list[str] = [
    "visual",
    "auditory",
    "motor",
    "cognitive",
    "at_parsing",
]

# ChromaDB collection names — one per disability class
# Using a prefix avoids name collisions if the same ChromaDB instance
# is used for multiple projects.
COLLECTION_PREFIX = "wcag_"

# BGE embedding model — runs on CPU, 384-dim output
EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"

# BGE instruction prefixes (per BGE paper — improves retrieval quality)
QUERY_PREFIX    = "Represent this sentence for searching relevant passages: "
PASSAGE_PREFIX  = ""          # BGE-small doesn't need passage prefix unlike BGE-large


# ── BGE Embedding Function (ChromaDB-compatible) ───────────────────────────────

class BGEEmbeddingFunction:
    """
    ChromaDB-compatible embedding function wrapping BGE-small-en-v1.5.

    ChromaDB expects an object with a __call__(input: list[str]) -> list[list[float]]
    signature. We implement that here and handle the query/passage prefix
    distinction that BGE models use for asymmetric retrieval.

    Parameters
    ----------
    model_name : str
        Hugging Face model identifier for the embedding model.
    is_query : bool
        If True, prepends the query instruction prefix to inputs.
        Set True when embedding search queries.
        Set False (default) when embedding documents to index.
    """

    def __init__(self, model_name: str = EMBEDDING_MODEL_NAME, is_query: bool = False):
        logger.info("Loading embedding model: %s", model_name)
        self._model = SentenceTransformer(model_name)
        self._is_query = is_query
        logger.info("Embedding model loaded — output dim: %d", self._model.get_sentence_embedding_dimension())

    def __call__(self, input: list[str]) -> list[list[float]]:
        """
        Embed a list of texts.

        Parameters
        ----------
        input : list[str]
            Texts to embed.

        Returns
        -------
        list[list[float]]
            One embedding vector per input text.
        """
        if self._is_query:
            texts = [QUERY_PREFIX + t for t in input]
        else:
            texts = [PASSAGE_PREFIX + t for t in input]

        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,    # cosine similarity works correctly
            show_progress_bar=False,
            batch_size=32,
        )
        return embeddings.tolist()


# ── WCAGVectorStore ────────────────────────────────────────────────────────────

class WCAGVectorStore:
    """
    Manages ChromaDB collections for WCAG criteria retrieval.

    One collection per disability class allows each audit agent to search
    only criteria relevant to its domain — the visual agent doesn't retrieve
    auditory criteria and vice versa. This scoped retrieval is a core part
    of the Design Doc Section 2.3.2 disability-stratified architecture.

    Usage
    -----
        store = WCAGVectorStore()
        store.rebuild_if_empty()   # call once at startup

        # Retrieve top 5 visual criteria for a contrast-related DOM chunk
        results = store.retrieve_criteria(
            disability_class="visual",
            query="text has insufficient contrast ratio against background",
            n_results=5,
        )

        # Look up a specific criterion by number
        criterion = store.get_criterion_by_number("1.4.3")

    Parameters
    ----------
    chroma_path : str
        Path where ChromaDB persists its data. Defaults to CHROMA_DB_PATH
        from environment (or data/chroma/ as fallback).
    criteria_json_path : Path
        Path to wcag_criteria.json. Defaults to data/wcag_criteria.json.
    """

    def __init__(
        self,
        chroma_path: str = CHROMA_DB_PATH,
        criteria_json_path: Path = CRITERIA_JSON,
    ) -> None:
        self._chroma_path      = chroma_path
        self._criteria_json    = criteria_json_path

        # Persistent ChromaDB client — data survives between restarts
        self._client = chromadb.PersistentClient(
            path=chroma_path,
            settings=Settings(anonymized_telemetry=False),
        )

        # Embedding functions — separate instances for doc vs. query
        # so we apply the correct prefix in each case
        self._doc_embedder   = BGEEmbeddingFunction(is_query=False)
        self._query_embedder = BGEEmbeddingFunction(is_query=True)

        # Cache of collection objects keyed by disability class name
        self._collections: dict[str, chromadb.Collection] = {}

        # Flat lookup by criterion number — populated from JSON on first use
        self._criteria_by_number: dict[str, dict] = {}

        logger.info("WCAGVectorStore initialised — chroma path: %s", chroma_path)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load_criteria_json(self) -> list[dict]:
        """
        Load wcag_criteria.json from disk.

        Returns
        -------
        list[dict]
            Raw list of criterion dicts from the JSON file.

        Raises
        ------
        FileNotFoundError
            If wcag_criteria.json has not been built yet.
        """
        if not self._criteria_json.exists():
            raise FileNotFoundError(
                f"wcag_criteria.json not found at {self._criteria_json}.\n"
                "Run: python -m backend.rag.wcag_loader\n"
                "to build the criteria database first."
            )

        with self._criteria_json.open(encoding="utf-8") as f:
            criteria = json.load(f)

        logger.info("Loaded %d criteria from JSON", len(criteria))
        return criteria

    def _get_or_create_collection(self, disability_class: str) -> chromadb.Collection:
        """
        Get an existing ChromaDB collection or create a new one.

        Collections use cosine similarity (best for normalised BGE embeddings).
        We use the document embedder here — the collection's embedded function
        is only used when adding documents, not querying.

        Parameters
        ----------
        disability_class : str
            One of the five DISABILITY_CLASSES values.

        Returns
        -------
        chromadb.Collection
        """
        if disability_class not in self._collections:
            name = f"{COLLECTION_PREFIX}{disability_class}"
            collection = self._client.get_or_create_collection(
                name=name,
                embedding_function=self._doc_embedder,
                metadata={"hnsw:space": "cosine"},
            )
            self._collections[disability_class] = collection
            logger.debug("Collection ready: %s (count: %d)", name, collection.count())

        return self._collections[disability_class]

    def _build_document_text(self, criterion: dict) -> str:
        """
        Build the text that gets embedded for a criterion document.

        We combine title + criterion_text + disability_classes for richer
        semantic representation. This means a query about "screen reader"
        retrieves AT-parsing criteria even if "screen reader" isn't in the
        criterion text verbatim.

        Parameters
        ----------
        criterion : dict
            A single criterion dict from wcag_criteria.json.

        Returns
        -------
        str
            Combined text for embedding.
        """
        parts = [
            f"WCAG {criterion.get('criterion_number', '')} "
            f"{criterion.get('criterion_title', '')}",
            criterion.get("criterion_text", ""),
            f"Disability classes: {', '.join(criterion.get('disability_classes', []))}",
            f"Pillar: {criterion.get('pillar', '')}",
        ]
        return " | ".join(p for p in parts if p.strip())

    def _build_metadata(self, criterion: dict) -> dict:
        """
        Build ChromaDB metadata for a criterion.

        ChromaDB metadata values must be str, int, float, or bool — not lists.
        We serialise lists as JSON strings and deserialise on retrieval.

        Parameters
        ----------
        criterion : dict
            A single criterion dict.

        Returns
        -------
        dict
            Flat metadata dict safe for ChromaDB storage.
        """
        return {
            "criterion_number":  criterion.get("criterion_number", ""),
            "criterion_title":   criterion.get("criterion_title", ""),
            "conformance_level": criterion.get("conformance_level", "A"),
            "criterion_text":    criterion.get("criterion_text", ""),
            "pillar":            criterion.get("pillar", ""),
            "wcag_version":      criterion.get("wcag_version", "2.2"),
            "understanding_url": criterion.get("understanding_url") or "",
            # Serialise lists as JSON strings — ChromaDB can't store lists
            "disability_classes":  json.dumps(criterion.get("disability_classes", [])),
            "legal_regulations":   json.dumps(criterion.get("legal_regulations", [])),
        }

    def _deserialise_metadata(self, metadata: dict) -> dict:
        """
        Reverse _build_metadata — parse JSON string fields back into lists.

        Parameters
        ----------
        metadata : dict
            Raw ChromaDB metadata dict.

        Returns
        -------
        dict
            Criterion dict with disability_classes and legal_regulations
            restored to Python lists.
        """
        result = dict(metadata)
        for list_field in ("disability_classes", "legal_regulations"):
            raw = result.get(list_field, "[]")
            try:
                result[list_field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result[list_field] = []
        return result

    # ── Public API ─────────────────────────────────────────────────────────────

    def rebuild_if_empty(self) -> None:
        """
        Build (or skip) the ChromaDB index.

        Logic:
            - Check if all five collections exist AND each has at least
              one document.
            - If all are populated: log and return immediately (fast path).
            - If any is empty or missing: rebuild ALL collections from scratch.
              This avoids partial index states where some collections are
              stale and others are current.

        This method is safe to call on every application startup — it is
        a no-op when the index is already up to date.

        Raises
        ------
        FileNotFoundError
            If wcag_criteria.json doesn't exist (wcag_loader hasn't been run).
        """
        logger.info("Checking index status across %d disability collections...", len(DISABILITY_CLASSES))

        all_populated = all(
            self._client.get_or_create_collection(
                f"{COLLECTION_PREFIX}{dc}",
                embedding_function=self._doc_embedder,
                metadata={"hnsw:space": "cosine"},
            ).count() > 0
            for dc in DISABILITY_CLASSES
        )

        if all_populated:
            logger.info("All collections populated — skipping rebuild (fast path)")
            # Still need to hydrate _criteria_by_number cache
            self._hydrate_number_cache()
            return

        logger.info("One or more collections empty — rebuilding index...")
        self._build_index()

    def _hydrate_number_cache(self) -> None:
        """
        Populate the in-memory criterion number lookup cache from JSON.
        Called after a successful build or when skipping rebuild.
        """
        if self._criteria_by_number:
            return   # already loaded
        criteria = self._load_criteria_json()
        for c in criteria:
            self._criteria_by_number[c["criterion_number"]] = c
        logger.debug("Number cache hydrated: %d entries", len(self._criteria_by_number))

    def _build_index(self) -> None:
        """
        Full index build: embed all criteria into their disability collections.

        Each criterion is added to every collection whose disability_class
        it belongs to. A criterion with disability_classes=["visual","motor"]
        is added to BOTH the visual and motor collections.

        We use batch upsert (not add) so re-running is idempotent.
        """
        criteria = self._load_criteria_json()
        if not criteria:
            raise RuntimeError("wcag_criteria.json is empty — run wcag_loader.py first.")

        # Populate number cache
        for c in criteria:
            self._criteria_by_number[c["criterion_number"]] = c

        # Group criteria by disability class
        class_to_criteria: dict[str, list[dict]] = {dc: [] for dc in DISABILITY_CLASSES}
        unmatched: list[str] = []

        for criterion in criteria:
            dc_list: list[str] = criterion.get("disability_classes", [])
            matched = False
            for dc in dc_list:
                dc = dc.strip()
                if dc in class_to_criteria:
                    class_to_criteria[dc].append(criterion)
                    matched = True
            if not matched:
                unmatched.append(criterion.get("criterion_number", "?"))

        if unmatched:
            logger.warning(
                "%d criteria have no known disability_class — they will not be "
                "indexed: %s", len(unmatched), unmatched
            )

        # Embed and upsert each collection
        total_docs = 0
        for disability_class, dc_criteria in class_to_criteria.items():
            if not dc_criteria:
                logger.warning("No criteria for disability class: %s", disability_class)
                continue

            collection = self._get_or_create_collection(disability_class)

            documents   : list[str]  = []
            metadatas   : list[dict] = []
            ids         : list[str]  = []

            for criterion in dc_criteria:
                num = criterion.get("criterion_number", "unknown")
                # Unique ID per criterion per collection
                doc_id = f"{disability_class}_{num.replace('.', '_')}"
                documents.append(self._build_document_text(criterion))
                metadatas.append(self._build_metadata(criterion))
                ids.append(doc_id)

            # Upsert in a single batch — idempotent on re-run
            collection.upsert(
                documents=documents,
                metadatas=metadatas,
                ids=ids,
            )
            total_docs += len(ids)
            logger.info(
                "  %-12s → %3d criteria indexed (collection: %s%s)",
                disability_class, len(ids), COLLECTION_PREFIX, disability_class,
            )

        logger.info("Index build complete — %d total document-collection pairs", total_docs)

    def retrieve_criteria(
        self,
        disability_class: str,
        query: str,
        n_results: int = 5,
    ) -> list[dict]:
        """
        Retrieve the top-N most relevant WCAG criteria for a given query.

        This is called by each disability agent before building its system
        prompt. The agent searches with a natural-language description of
        what it's checking (e.g. "image has no alt text") and receives the
        most semantically relevant criteria for its disability domain.

        Parameters
        ----------
        disability_class : str
            Must be one of: visual, auditory, motor, cognitive, at_parsing.
        query : str
            Natural-language description of the accessibility concern.
            No need for exact WCAG terminology — semantic search handles it.
        n_results : int
            Number of criteria to retrieve. Default 5. Max meaningful value
            is ~10 for a single DOM chunk; more adds noise not signal.

        Returns
        -------
        list[dict]
            List of criterion dicts, each containing:
            criterion_number, criterion_title, conformance_level,
            criterion_text, disability_classes (list), legal_regulations (list),
            pillar, wcag_version, understanding_url.

        Raises
        ------
        ValueError
            If disability_class is not one of the five known values.
        RuntimeError
            If the collection is empty (rebuild_if_empty() was not called).
        """
        if disability_class not in DISABILITY_CLASSES:
            raise ValueError(
                f"Unknown disability_class: '{disability_class}'. "
                f"Must be one of: {DISABILITY_CLASSES}"
            )

        collection = self._get_or_create_collection(disability_class)

        if collection.count() == 0:
            raise RuntimeError(
                f"Collection '{COLLECTION_PREFIX}{disability_class}' is empty. "
                "Call rebuild_if_empty() before querying."
            )

        # Cap n_results to the collection size to avoid ChromaDB errors
        n_results = min(n_results, collection.count())

        # Apply query prefix for BGE asymmetric retrieval
        query_with_prefix = QUERY_PREFIX + query

        results = collection.query(
            query_texts=[query_with_prefix],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

        # results["metadatas"] is list[list[dict]] — first element is our batch
        raw_metadatas = results.get("metadatas", [[]])[0]
        distances     = results.get("distances",  [[]])[0]

        criteria_out: list[dict] = []
        for meta, dist in zip(raw_metadatas, distances):
            criterion = self._deserialise_metadata(meta)
            # Add similarity score (1 - cosine distance) for agent transparency
            criterion["similarity_score"] = round(1.0 - dist, 4)
            criteria_out.append(criterion)

        logger.debug(
            "retrieve_criteria('%s', query='%s...') → %d results",
            disability_class, query[:50], len(criteria_out),
        )
        return criteria_out

    def get_criterion_by_number(self, number: str) -> dict:
        """
        Direct lookup of a WCAG criterion by its number (e.g. "1.4.3").

        Used by the Critique Sub-Agent to independently re-fetch the criterion
        text rather than trusting the text passed in the finding. This is the
        mechanism that enforces citation accuracy — the critique agent always
        verifies against the source, not the audit agent's copy.

        Parameters
        ----------
        number : str
            Criterion number in dot notation, e.g. "1.4.3".

        Returns
        -------
        dict
            Full criterion dict.

        Raises
        ------
        KeyError
            If the criterion number is not in the loaded criteria.
            This should never happen for valid WCAG numbers — if it does,
            the criterion was not in wcag_criteria.json (check wcag_loader).
        """
        # Hydrate cache if needed
        if not self._criteria_by_number:
            self._hydrate_number_cache()

        if number not in self._criteria_by_number:
            raise KeyError(
                f"Criterion '{number}' not found in WCAG knowledge base. "
                f"Known range: {sorted(self._criteria_by_number.keys())[:5]}..."
            )

        return self._criteria_by_number[number]

    def collection_stats(self) -> dict[str, int]:
        """
        Return document counts per disability class collection.
        Useful for health checks and debugging.

        Returns
        -------
        dict[str, int]
            Mapping of disability_class → document count in collection.
        """
        stats: dict[str, int] = {}
        for dc in DISABILITY_CLASSES:
            try:
                col = self._client.get_collection(
                    f"{COLLECTION_PREFIX}{dc}",
                    embedding_function=self._doc_embedder,
                )
                stats[dc] = col.count()
            except Exception:
                stats[dc] = 0
        return stats


# ── CLI / __main__ ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Build the ChromaDB index when run directly.

    From repo root:
        python -m backend.rag.vector_store

    This will:
        1. Load wcag_criteria.json
        2. Embed each criterion with BGE-small-en-v1.5
        3. Upsert into 5 ChromaDB collections
        4. Print a summary and run a quick smoke test

    Expected first-run time: ~30-60 seconds on CPU (embedding 87 criteria).
    Subsequent runs: < 1 second (rebuild_if_empty skips if populated).
    """
    import sys

    store = WCAGVectorStore()

    print("\n📦 Building WCAG vector store index...")
    store.rebuild_if_empty()

    print("\n📊 Collection stats:")
    stats = store.collection_stats()
    for dc, count in stats.items():
        bar = "█" * count
        print(f"  {dc:<12} {count:>3} criteria  {bar}")

    print("\n🔍 Smoke test — retrieve_criteria('visual', 'image missing alt text'):")
    results = store.retrieve_criteria("visual", "image missing alt text", n_results=3)
    for r in results:
        print(f"  [{r['criterion_number']}] {r['criterion_title']} "
              f"(Level {r['conformance_level']}) — similarity: {r['similarity_score']}")

    print("\n🔍 Smoke test — get_criterion_by_number('1.4.3'):")
    c = store.get_criterion_by_number("1.4.3")
    print(f"  {c['criterion_number']} {c['criterion_title']} | Level {c['conformance_level']}")
    print(f"  Legal: {c['legal_regulations']}")

    print("\n✅ Vector store ready\n")
    sys.exit(0)