import os
from dotenv import load_dotenv
load_dotenv(override=True)

import io
from celery import Celery
import fitz # PyMuPDF
from langchain_text_splitters import RecursiveCharacterTextSplitter
from database import SessionLocal, Document, DocumentChunk
from storage import download_pdf_from_storage
from pinecone_service import upsert_chunks_to_vector_store

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "tasks",
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Securely handle Upstash Redis over SSL
if REDIS_URL.startswith("rediss://"):
    celery_app.conf.update(
        broker_use_ssl={'ssl_cert_reqs': 'none'},
        redis_backend_use_ssl={'ssl_cert_reqs': 'none'}
    )

# Celery task configuration
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

@celery_app.task(name="process_pdf")
def process_pdf_task(user_id: str, document_id: str, filename: str, storage_url: str):
    """Asynchronous task to download PDF, chunk it page-by-page, embed, store in Pinecone and save to SQL."""
    db = SessionLocal()
    try:
        # 1. Download file bytes from Supabase storage
        file_bytes = download_pdf_from_storage(storage_url)
        
        # 2. Extract text from PDF page-by-page
        pdf_document = fitz.open(stream=file_bytes, filetype="pdf")
        
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
        all_chunks = []
        import re
        
        for page_idx, page in enumerate(pdf_document):
            page_text = page.get_text()
            if not page_text or not page_text.strip():
                continue
            
            # --- Structure-Aware Markdown & Hierarchy Parsing ---
            parsed_lines = []
            current_section = "default"
            table_mode = False
            
            lines = page_text.split("\n")
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                
                # Header detection (e.g. "1. Executive Summary" or ALL CAPS short lines)
                header_match = re.match(r"^(\d+\.?\d*)\s+(.*)$", stripped)
                if header_match or (stripped.isupper() and len(stripped) < 60):
                    if header_match:
                        section_num = header_match.group(1)
                        section_title = header_match.group(2)
                        current_section = f"{section_num}_{section_title.replace(' ', '_')}"
                        parsed_lines.append(f"\n# {stripped}")
                    else:
                        current_section = stripped.replace(" ", "_")
                        parsed_lines.append(f"\n# {stripped}")
                    table_mode = False
                # Table row detection (multiple columns separated by 2+ spaces or tabs)
                elif "  " in line or "\t" in line:
                    parts = [p.strip() for p in re.split(r"\s{2,}|\t", line) if p.strip()]
                    if len(parts) > 1:
                        if not table_mode:
                            headers = [f"Col {idx+1}" for idx in range(len(parts))]
                            parsed_lines.append("\n| " + " | ".join(headers) + " |")
                            parsed_lines.append("| " + " | ".join(["---"] * len(parts)) + " |")
                            table_mode = True
                        parsed_lines.append("| " + " | ".join(parts) + " |")
                    else:
                        parsed_lines.append(stripped)
                        table_mode = False
                else:
                    parsed_lines.append(stripped)
                    table_mode = False
            
            markdown_text = "\n".join(parsed_lines)
            
            # Extract keywords from the section and text
            raw_keywords = re.findall(r"\b[a-zA-Z]{5,}\b", current_section + " " + markdown_text)
            keywords = list(set([k.lower() for k in raw_keywords]))[:10]
            
            # Chunk the parsed markdown text
            page_chunks = text_splitter.split_text(markdown_text)
            for chunk_text in page_chunks:
                all_chunks.append({
                    "text": chunk_text,
                    "page": page_idx + 1,
                    "chunk_index": len(all_chunks),
                    "section_id": current_section,
                    "role_access": "user",
                    "keywords": keywords
                })
                
        if not all_chunks:
            raise ValueError("No extractable text found in PDF file.")
            
        # 3. Generate embeddings and upsert to Pinecone
        upsert_chunks_to_vector_store(user_id, document_id, filename, all_chunks)
        
        # Save chunks to PostgreSQL database in batch
        db_chunks = [
            DocumentChunk(
                document_id=document_id,
                page_number=c["page"],
                chunk_index=c["chunk_index"],
                content=c["text"]
            ) for c in all_chunks
        ]
        db.add_all(db_chunks)
        db.commit()
        
        # 4. Update database status to completed
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = "completed"
            db.commit()
            
        return {"status": "success", "chunks_processed": len(all_chunks)}
        
    except Exception as e:
        print(f"Error processing document {document_id}: {e}")
        # Update database status to failed
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = "failed"
            db.commit()
        return {"status": "failed", "error": str(e)}
    finally:
        db.close()
