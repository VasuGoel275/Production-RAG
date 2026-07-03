import os
from supabase import create_client, Client
from typing import Optional

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME", "documents")

_supabase_client: Optional[Client] = None

def get_supabase_client() -> Optional[Client]:
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    
    if SUPABASE_URL and SUPABASE_KEY:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return _supabase_client
    return None

def initialize_bucket():
    """Create the storage bucket if it doesn't exist."""
    client = get_supabase_client()
    if not client:
        return
    
    try:
        # Check if bucket exists
        buckets = client.storage.list_buckets()
        exists = any(b.name == BUCKET_NAME for b in buckets)
        if not exists:
            client.storage.create_bucket(BUCKET_NAME, options={"public": True})
    except Exception as e:
        print(f"Error checking/creating Supabase bucket: {e}")

def upload_pdf_to_storage(user_id: str, doc_id: str, filename: str, file_data: bytes) -> Optional[str]:
    """Uploads file bytes to Supabase Storage and returns the public URL."""
    client = get_supabase_client()
    if not client:
        # Fallback for local testing: return dummy URL
        return f"local://{user_id}/{doc_id}/{filename}"
        
    try:
        initialize_bucket()
        
        # Path inside bucket: user_id/doc_id_filename.pdf
        storage_path = f"{user_id}/{doc_id}_{filename}"
        
        # Upload file bytes
        client.storage.from_(BUCKET_NAME).upload(
            path=storage_path,
            file=file_data,
            file_options={"content-type": "application/pdf", "x-upsert": "true"}
        )
        
        # Get public url
        url_resp = client.storage.from_(BUCKET_NAME).get_public_url(storage_path)
        return url_resp
    except Exception as e:
        print(f"Failed uploading file to Supabase storage: {e}")
        raise e

def download_pdf_from_storage(storage_url: str) -> bytes:
    """Downloads a file using its storage path/url."""
    # If it is local dummy URL
    if storage_url.startswith("local://"):
        # Not a real cloud setup, but should never happen in production
        raise ValueError("Cannot download local file placeholder from Supabase storage")
        
    client = get_supabase_client()
    if not client:
        raise ValueError("Supabase client is not configured")
        
    try:
        # Extract storage path from url
        # The URL looks like: https://xxxx.supabase.co/storage/v1/object/public/documents/user_id/doc_id_filename.pdf
        # We need the path starting after the bucket name: user_id/doc_id_filename.pdf
        bucket_prefix = f"/storage/v1/object/public/{BUCKET_NAME}/"
        if bucket_prefix in storage_url:
            storage_path = storage_url.split(bucket_prefix)[-1]
        else:
            # Fallback if different format, extract from last part
            parts = storage_url.split(f"/{BUCKET_NAME}/")
            if len(parts) > 1:
                storage_path = parts[-1]
            else:
                raise ValueError("Could not parse storage path from storage URL")
                
        data = client.storage.from_(BUCKET_NAME).download(storage_path)
        return data
    except Exception as e:
        print(f"Failed downloading file from Supabase storage: {e}")
        raise e
