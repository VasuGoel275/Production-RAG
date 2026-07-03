import os
import time
from pinecone import Pinecone, ServerlessSpec
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from database import DocumentChunk, Document
from rank_bm25 import BM25Okapi

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "askdocx")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

_pinecone_client: Optional[Pinecone] = None
_re_ranker: Optional[Any] = None

def get_pinecone_client() -> Optional[Pinecone]:
    global _pinecone_client
    if _pinecone_client is not None:
        return _pinecone_client
    
    if PINECONE_API_KEY:
        _pinecone_client = Pinecone(api_key=PINECONE_API_KEY)
        return _pinecone_client
    return None

_re_ranker_failed: bool = False

def get_re_ranker() -> Optional[Any]:
    global _re_ranker, _re_ranker_failed
    if os.getenv("DISABLE_RE_RANKER") == "true":
        return None
        
    if _re_ranker_failed:
        return None
        
    if _re_ranker is None:
        try:
            # Lazy import to prevent loading PyTorch unless re-ranker is enabled
            from sentence_transformers import CrossEncoder
            _re_ranker = CrossEncoder("sentence-transformers/ms-marco-MiniLM-L-6-v2")
        except Exception as e:
            print(f"Disabling re-ranker: Failed to download/load local Cross-Encoder model: {e}")
            _re_ranker_failed = True
            return None
    return _re_ranker

def get_embeddings_model():
    """Initializes Google Generative AI Embeddings."""
    return GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001", 
        google_api_key=GEMINI_API_KEY
    )

_index_initialized: bool = False

def initialize_pinecone_index():
    """Checks if index exists, and creates it if not. Deletes index and recreates if dimension is not 3072."""
    global _index_initialized
    if _index_initialized:
        return
        
    pc = get_pinecone_client()
    if not pc:
        return
        
    try:
        existing_indexes = [index.name for index in pc.list_indexes()]
        if PINECONE_INDEX_NAME in existing_indexes:
            desc = pc.describe_index(PINECONE_INDEX_NAME)
            if desc.dimension != 3072:
                print(f"Deleting index '{PINECONE_INDEX_NAME}' due to dimension mismatch ({desc.dimension} vs 3072)")
                pc.delete_index(PINECONE_INDEX_NAME)
                existing_indexes.remove(PINECONE_INDEX_NAME)
                # Wait for deletion
                time.sleep(5)
                
        if PINECONE_INDEX_NAME not in existing_indexes:
            pc.create_index(
                name=PINECONE_INDEX_NAME,
                dimension=3072,
                metric="cosine",
                spec=ServerlessSpec(
                    cloud="aws",
                    region="us-east-1"
                )
            )
            # Wait for creation
            time.sleep(5)
        _index_initialized = True
    except Exception as e:
        print(f"Error initializing Pinecone index: {e}")

def upsert_chunks_to_vector_store(
    user_id: str, 
    document_id: str, 
    filename: str, 
    chunks: List[Dict[str, Any]]  # Each chunk is {"text": str, "page": int, "chunk_index": int, "section_id": Optional[str], "role_access": Optional[str], "keywords": Optional[List[str]]}
):
    """Generates embeddings and uploads to Pinecone index with user-scoped metadata."""
    pc = get_pinecone_client()
    if not pc:
        print("Pinecone client not configured. Skipping vector store ingestion.")
        return
        
    initialize_pinecone_index()
    index = pc.Index(PINECONE_INDEX_NAME)
    embeddings_model = get_embeddings_model()
    
    # Generate embeddings in batches of 100 for efficiency
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i : i + batch_size]
        texts_to_embed = [c["text"] for c in batch_chunks]
        raw_embeddings = embeddings_model.embed_documents(texts_to_embed)
        
        vectors = []
        for j, (chunk_data, embedding) in enumerate(zip(batch_chunks, raw_embeddings)):
            chunk_idx = i + j
            vector_id = f"{document_id}_{chunk_idx}"
            metadata = {
                "user_id": str(user_id),
                "document_id": str(document_id),
                "filename": filename,
                "text": chunk_data["text"],
                "page": int(chunk_data["page"]),
                "chunk_index": int(chunk_data["chunk_index"]),
                "section_id": str(chunk_data.get("section_id", "default")),
                "role_access": str(chunk_data.get("role_access", "user")),
                "keywords": chunk_data.get("keywords", [])
            }
            vectors.append({
                "id": vector_id,
                "values": embedding,
                "metadata": metadata
            })
            
        index.upsert(vectors=vectors)

def query_vector_store(
    user_id: str, 
    query_text: str, 
    db: Session,
    top_k: int = 5,
    document_ids: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Hybrid Search (Dense Pinecone + Sparse BM25 via PostgreSQL Chunks) with RRF & local Re-ranking."""
    
    # 1. Dense Semantic Search (Pinecone)
    pc = get_pinecone_client()
    dense_candidates = []
    if pc:
        try:
            index = pc.Index(PINECONE_INDEX_NAME)
            embeddings_model = get_embeddings_model()
            query_embedding = embeddings_model.embed_query(query_text)
            
            # Build filter
            filter_dict = {"user_id": str(user_id)}
            if document_ids and len(document_ids) > 0:
                if len(document_ids) == 1:
                    filter_dict["document_id"] = str(document_ids[0])
                else:
                    filter_dict["document_id"] = {"$in": [str(d) for d in document_ids]}
                    
            res = index.query(
                vector=query_embedding,
                top_k=25,  # Fetch wider list of candidate vectors for RRF
                include_metadata=True,
                filter=filter_dict
            )
            
            if "matches" in res:
                for match in res["matches"]:
                    dense_candidates.append({
                        "text": match["metadata"]["text"],
                        "filename": match["metadata"].get("filename", "Unknown"),
                        "page": int(match["metadata"].get("page", 1)),
                        "chunk_index": int(match["metadata"].get("chunk_index", 0)),
                        "document_id": match["metadata"].get("document_id")
                    })
        except Exception as e:
            print(f"Dense vector query failed: {e}")

    # 2. Sparse Keyword Search (Local BM25 on PostgreSQL chunks)
    sparse_candidates = []
    try:
        # Fetch matching chunks from PostgreSQL
        chunk_query = db.query(DocumentChunk).join(Document)
        if document_ids and len(document_ids) > 0:
            chunk_query = chunk_query.filter(DocumentChunk.document_id.in_(document_ids))
        else:
            chunk_query = chunk_query.filter(Document.user_id == user_id)
            
        all_db_chunks = chunk_query.all()
        
        if all_db_chunks:
            # Tokenize chunks for BM25
            corpus = [doc.content.lower().split(" ") for doc in all_db_chunks]
            bm25 = BM25Okapi(corpus)
            
            tokenized_query = query_text.lower().split(" ")
            # Get BM25 scores
            scores = bm25.get_scores(tokenized_query)
            
            # Pack chunks with their BM25 scores
            chunk_scores = []
            for doc_chunk, score in zip(all_db_chunks, scores):
                if score > 0:  # Only keep match with overlapping terms
                    chunk_scores.append((doc_chunk, score))
                    
            # Sort by score descending and take top 25
            chunk_scores.sort(key=lambda x: x[1], reverse=True)
            top_sparse = chunk_scores[:25]
            
            for doc_chunk, score in top_sparse:
                sparse_candidates.append({
                    "text": doc_chunk.content,
                    "filename": doc_chunk.document.filename,
                    "page": doc_chunk.page_number,
                    "chunk_index": doc_chunk.chunk_index,
                    "document_id": str(doc_chunk.document_id)
                })
    except Exception as e:
        print(f"Sparse BM25 query failed: {e}")

    # 3. Reciprocal Rank Fusion (RRF) to merge ranks
    # Constant k for RRF (standard is 60)
    RRF_K = 60
    rrf_scores = {}
    
    # helper unique key for chunk matching
    def get_chunk_key(c):
        return f"{c['document_id']}_{c['chunk_index']}"

    # Process Dense ranks
    for rank, candidate in enumerate(dense_candidates):
        key = get_chunk_key(candidate)
        if key not in rrf_scores:
            rrf_scores[key] = {"candidate": candidate, "score": 0.0}
        rrf_scores[key]["score"] += 1.0 / (RRF_K + rank + 1)

    # Process Sparse ranks
    for rank, candidate in enumerate(sparse_candidates):
        key = get_chunk_key(candidate)
        if key not in rrf_scores:
            rrf_scores[key] = {"candidate": candidate, "score": 0.0}
        rrf_scores[key]["score"] += 1.0 / (RRF_K + rank + 1)

    # Sort merged results by RRF score descending
    merged_candidates = list(rrf_scores.values())
    merged_candidates.sort(key=lambda x: x["score"], reverse=True)
    
    # Take top 15 candidates for re-ranking
    top_candidates = [m["candidate"] for m in merged_candidates[:15]]

    if not top_candidates:
        return []

    # 4. Cross-Encoder Re-ranking (Local ms-marco Cross-Encoder model)
    try:
        re_ranker = get_re_ranker()
        # Predict relevancy for each candidate chunk
        pairs = [[query_text, c["text"]] for c in top_candidates]
        ce_scores = re_ranker.predict(pairs)
        
        # Attach CE scores
        for candidate, ce_score in zip(top_candidates, ce_scores):
            candidate["re_rank_score"] = float(ce_score)
            
        # Sort by cross-encoder score descending
        top_candidates.sort(key=lambda x: x["re_rank_score"], reverse=True)
    except Exception as e:
        print(f"Local Cross-Encoder re-ranking failed: {e}. Falling back to RRF rank order.")

    # Return top_k results
    return top_candidates[:top_k]
