import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Header, BackgroundTasks
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List, Optional, Any
from pydantic import BaseModel, EmailStr
import uuid
import json

# Import custom modules
from database import get_db, User, Document, ChatSession, ChatMessage
from auth import hash_password, verify_password, create_access_token, decode_access_token
from storage import upload_pdf_to_storage
from pinecone_service import query_vector_store, get_embeddings_model
from cache_manager import get_cached_response, set_cached_response, get_semantic_cache, set_semantic_cache
from worker import process_pdf_task

# Langchain Core / LCEL / Message imports
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document as LCDocument
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI

# Custom Retriever Wrapper for clean integration into history-aware retriever
class AskDocXRetriever(BaseRetriever):
    user_id: str
    db_session: Any
    document_ids: Optional[List[str]] = None

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[LCDocument]:
        # This calls our custom vector store hybrid query
        contexts = query_vector_store(
            user_id=self.user_id,
            query_text=query,
            db=self.db_session,
            top_k=5,
            document_ids=self.document_ids
        )
        return [
            LCDocument(
                page_content=c["text"],
                metadata={"source": c["filename"], "page": c["page"]}
            ) for c in contexts
        ]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
print(f"--- Loaded GEMINI_API_KEY in main.py: {GEMINI_API_KEY[:10] if GEMINI_API_KEY else 'NONE'}...{GEMINI_API_KEY[-5:] if GEMINI_API_KEY else 'NONE'} ---", flush=True)

app = FastAPI(title="AskDocX RAG Backend", version="1.0.0")

# Enable CORS for Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Schemas ---
class UserSignUp(BaseModel):
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class SessionCreate(BaseModel):
    title: str

class SessionResponse(BaseModel):
    id: str
    title: str
    created_at: str

    class Config:
        orm_mode = True

class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    created_at: str

    class Config:
        orm_mode = True

class QueryRequest(BaseModel):
    session_id: str
    question: str
    document_ids: Optional[List[str]] = None

class QueryResponse(BaseModel):
    answer: str
    cached: bool
    contexts: Optional[List[dict]] = None

class EvalSample(BaseModel):
    question: str
    contexts: List[str]
    answer: str
    ground_truth: Optional[str] = None

class EvalRequest(BaseModel):
    samples: List[EvalSample]

# --- JWT Authentication Dependency ---
def get_current_user(token: str = Depends(lambda x: None), db: Session = Depends(get_db)) -> User:
    # We will accept token via header or query param
    # FastAPI Depends on security header is cleaner, but let's implement standard header check
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    security = HTTPBearer(auto_error=False)
    
    # We will extract manually or via security scheme
    raise_credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    return raise_credentials_exception

# Overwrite header verification explicitly to keep it simple and robust
async def get_current_user_custom(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired",
        )
    email: str = payload.get("sub")
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user

# --- Routes ---

@app.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(user_data: UserSignUp, db: Session = Depends(get_db)):
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email is already registered")
    
    hashed_pwd = hash_password(user_data.password)
    new_user = User(email=user_data.email, password_hash=hashed_pwd)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User registered successfully"}

@app.post("/login", response_model=Token)
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/sessions")
def create_session(session_data: SessionCreate, current_user: User = Depends(get_current_user_custom), db: Session = Depends(get_db)):
    new_session = ChatSession(user_id=current_user.id, title=session_data.title)
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return {
        "id": str(new_session.id),
        "title": new_session.title,
        "created_at": new_session.created_at.isoformat()
    }

@app.get("/sessions")
def list_sessions(current_user: User = Depends(get_current_user_custom), db: Session = Depends(get_db)):
    sessions = db.query(ChatSession).filter(ChatSession.user_id == current_user.id).order_by(ChatSession.created_at.desc()).all()
    return [
        {
            "id": str(s.id),
            "title": s.title,
            "created_at": s.created_at.isoformat()
        } for s in sessions
    ]

@app.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str, current_user: User = Depends(get_current_user_custom), db: Session = Depends(get_db)):
    # Verify session belongs to user
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc()).all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat()
        } for m in messages
    ]

@app.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user_custom),
    db: Session = Depends(get_db)
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
        
    doc_id = uuid.uuid4()
    file_bytes = await file.read()
    
    try:
        # Determine page count using PyMuPDF (fitz)
        import fitz
        pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_count = len(pdf_doc)
        pdf_doc.close()
        
        # 1. Upload to Supabase Storage
        storage_url = upload_pdf_to_storage(str(current_user.id), str(doc_id), file.filename, file_bytes)
        
        # 2. Save document record in Postgres
        new_doc = Document(
            id=doc_id,
            user_id=current_user.id,
            filename=file.filename,
            storage_url=storage_url,
            status="processing"
        )
        db.add(new_doc)
        db.commit()
        db.refresh(new_doc)
        
        # 3. Trigger background processing task using FastAPI native BackgroundTasks thread pool
        background_tasks.add_task(
            process_pdf_task,
            str(current_user.id),
            str(doc_id),
            file.filename,
            storage_url
        )
        
        return {
            "id": str(new_doc.id),
            "filename": new_doc.filename,
            "status": new_doc.status,
            "storage_url": new_doc.storage_url
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

@app.get("/documents")
def list_documents(current_user: User = Depends(get_current_user_custom), db: Session = Depends(get_db)):
    docs = db.query(Document).filter(Document.user_id == current_user.id).order_by(Document.created_at.desc()).all()
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "status": d.status,
            "storage_url": d.storage_url,
            "created_at": d.created_at.isoformat()
        } for d in docs
    ]

@app.post("/chat/query", response_model=QueryResponse)
def query_pdf_chat(req: QueryRequest, current_user: User = Depends(get_current_user_custom), db: Session = Depends(get_db)):
    # 1. Instant Cache Check (using the raw question key)
    cached_answer = get_cached_response(req.session_id, req.question)
    if cached_answer:
        # Save query and response to SQL DB
        user_msg = ChatMessage(session_id=req.session_id, role="user", content=req.question)
        assistant_msg = ChatMessage(session_id=req.session_id, role="assistant", content=cached_answer)
        db.add(user_msg)
        db.add(assistant_msg)
        db.commit()
        return QueryResponse(answer=cached_answer, cached=True)

    # Fetch last 3 message exchanges (6 messages total) from SQL database to reconstruct history
    history_msgs = db.query(ChatMessage).filter(ChatMessage.session_id == req.session_id).order_by(ChatMessage.created_at.desc()).limit(6).all()
    history_msgs.reverse()
    
    chat_history = []
    for m in history_msgs:
        if m.role == "user":
            chat_history.append(HumanMessage(content=m.content))
        else:
            chat_history.append(AIMessage(content=m.content))

    # Initialize model
    model = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0, google_api_key=GEMINI_API_KEY)

    # 1. Condense Question Chain (LCEL)
    condense_prompt = ChatPromptTemplate.from_messages([
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{question}"),
        ("user", "Given the above conversation history and the latest user question, rephrase the latest question to be a standalone, context-complete question that can be understood without the chat history. Do NOT answer the question, just reformulate it or return it as-is if no rephrasing is needed.")
    ])
    rephrase_chain = condense_prompt | model | StrOutputParser()

    try:
        if chat_history:
            standalone_query = rephrase_chain.invoke({
                "chat_history": chat_history,
                "question": req.question
            })
        else:
            standalone_query = req.question
    except Exception as e:
        standalone_query = req.question
        print(f"Query condensation failed: {e}")

    # 2. Semantic Cache Check (using standalone query embedding)
    query_emb = None
    try:
        embeddings_model = get_embeddings_model()
        query_emb = embeddings_model.embed_query(standalone_query)
        sem_cached_answer = get_semantic_cache(req.session_id, query_emb)
        if sem_cached_answer:
            # Save query and response to SQL DB
            user_msg = ChatMessage(session_id=req.session_id, role="user", content=req.question)
            assistant_msg = ChatMessage(session_id=req.session_id, role="assistant", content=sem_cached_answer)
            db.add(user_msg)
            db.add(assistant_msg)
            db.commit()
            return QueryResponse(answer=sem_cached_answer, cached=True)
    except Exception as e:
        print(f"Semantic cache lookup failed: {e}")

    # 3. Retrieve Documents using the standard AskDocXRetriever class wrapper
    retriever = AskDocXRetriever(
        user_id=str(current_user.id),
        db_session=db,
        document_ids=req.document_ids
    )

    try:
        lc_docs = retriever.invoke(standalone_query)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document retrieval failed: {str(e)}"
        )

    # Format contexts to match the API response schema
    contexts = [
        {
            "text": doc.page_content,
            "filename": doc.metadata.get("source", "Unknown"),
            "page": doc.metadata.get("page", 1)
        } for doc in lc_docs
    ]

    if not contexts:
        answer = "I cannot answer your question because no matching document context was found. Please ensure you have uploaded and selected relevant documents."
        # Save to DB
        user_msg = ChatMessage(session_id=req.session_id, role="user", content=req.question)
        assistant_msg = ChatMessage(session_id=req.session_id, role="assistant", content=answer)
        db.add(user_msg)
        db.add(assistant_msg)
        db.commit()
        return QueryResponse(answer=answer, cached=False)

    # Assemble retrieved context blocks
    context_str = "\n\n".join([f"[Source: {d.metadata.get('source', 'Unknown')} Page {d.metadata.get('page', 1)}]: {d.page_content}" for d in lc_docs])

    # 4. QA Chain (LCEL) - enforces strict context adherence and conciseness
    qa_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a precise AI assistant. Answer the user's question using ONLY the provided retrieved context. "
                   "Be concise, direct, and to the point. Do not extrapolate, add external information, or guess. "
                   "If the answer cannot be found in the context, state clearly: 'I cannot answer based on the provided documents.'\n\n"
                   "Context:\n{context}"),
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{question}")
    ])
    
    qa_chain = qa_prompt | model | StrOutputParser()

    try:
        answer = qa_chain.invoke({
            "context": context_str,
            "chat_history": chat_history,
            "question": standalone_query
        })
    except Exception as e:
        if "quota" in str(e).lower() or "429" in str(e):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Gemini API rate limit exceeded. Please wait a moment and try again."
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gemini API Error: {str(e)}"
        )

    # 5. Save to SQL DB
    user_msg = ChatMessage(session_id=req.session_id, role="user", content=req.question)
    assistant_msg = ChatMessage(session_id=req.session_id, role="assistant", content=answer)
    db.add(user_msg)
    db.add(assistant_msg)
    db.commit()

    # 6. Write to Redis Cache (Exact + Semantic)
    set_cached_response(req.session_id, req.question, answer)
    if query_emb:
        set_semantic_cache(req.session_id, standalone_query, query_emb, answer)

    return QueryResponse(answer=answer, cached=False, contexts=contexts)

@app.post("/chat/query/stream")
def query_pdf_chat_stream(req: QueryRequest, current_user: User = Depends(get_current_user_custom), db: Session = Depends(get_db)):
    # 1. Instant Cache Check (using the raw question key)
    cached_answer = get_cached_response(req.session_id, req.question)
    if cached_answer:
        print(f"--- Exact Cache Hit! key: cache:{req.session_id}:{req.question.strip().lower()} ---", flush=True)
        # Save query and response to SQL DB
        user_msg = ChatMessage(session_id=req.session_id, role="user", content=req.question)
        assistant_msg = ChatMessage(session_id=req.session_id, role="assistant", content=cached_answer)
        db.add(user_msg)
        db.add(assistant_msg)
        db.commit()
        
        def cached_generator():
            yield json.dumps({"answer": cached_answer, "cached": True, "done": True}) + "\n"
        return StreamingResponse(cached_generator(), media_type="application/x-ndjson")

    # Fetch last 3 message exchanges (6 messages total) from SQL database to reconstruct history
    history_msgs = db.query(ChatMessage).filter(ChatMessage.session_id == req.session_id).order_by(ChatMessage.created_at.desc()).limit(6).all()
    history_msgs.reverse()
    
    chat_history = []
    for m in history_msgs:
        if m.role == "user":
            chat_history.append(HumanMessage(content=m.content))
        else:
            chat_history.append(AIMessage(content=m.content))

    # Initialize model
    model = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0, google_api_key=GEMINI_API_KEY)

    # 1. Condense Question Chain (LCEL)
    condense_prompt = ChatPromptTemplate.from_messages([
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{question}"),
        ("user", "Given the above conversation history and the latest user question, rephrase the latest question to be a standalone, context-complete question that can be understood without the chat history. Do NOT answer the question, just reformulate it or return it as-is if no rephrasing is needed.")
    ])
    rephrase_chain = condense_prompt | model | StrOutputParser()

    try:
        if chat_history:
            standalone_query = rephrase_chain.invoke({
                "chat_history": chat_history,
                "question": req.question
            })
        else:
            standalone_query = req.question
    except Exception as e:
        standalone_query = req.question
        print(f"Query condensation failed: {e}")

    # 2. Semantic Cache Check (using standalone query embedding)
    query_emb = None
    try:
        embeddings_model = get_embeddings_model()
        query_emb = embeddings_model.embed_query(standalone_query)
        sem_cached_answer = get_semantic_cache(req.session_id, query_emb)
        if sem_cached_answer:
            # Save query and response to SQL DB
            user_msg = ChatMessage(session_id=req.session_id, role="user", content=req.question)
            assistant_msg = ChatMessage(session_id=req.session_id, role="assistant", content=sem_cached_answer)
            db.add(user_msg)
            db.add(assistant_msg)
            db.commit()
            
            def cached_generator():
                yield json.dumps({"answer": sem_cached_answer, "cached": True, "done": True}) + "\n"
            return StreamingResponse(cached_generator(), media_type="application/x-ndjson")
    except Exception as e:
        print(f"Semantic cache lookup failed: {e}")

    # 3. Retrieve Documents using the standard AskDocXRetriever class wrapper
    retriever = AskDocXRetriever(
        user_id=str(current_user.id),
        db_session=db,
        document_ids=req.document_ids
    )

    try:
        lc_docs = retriever.invoke(standalone_query)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document retrieval failed: {str(e)}"
        )

    # Format contexts to match the API response schema
    contexts = [
        {
            "text": doc.page_content,
            "filename": doc.metadata.get("source", "Unknown"),
            "page": doc.metadata.get("page", 1)
        } for doc in lc_docs
    ]

    if not contexts:
        answer = "I cannot answer your question because no matching document context was found. Please ensure you have uploaded and selected relevant documents."
        # Save to DB
        user_msg = ChatMessage(session_id=req.session_id, role="user", content=req.question)
        assistant_msg = ChatMessage(session_id=req.session_id, role="assistant", content=answer)
        db.add(user_msg)
        db.add(assistant_msg)
        db.commit()
        
        def cached_generator():
            yield json.dumps({"answer": answer, "cached": False, "done": True}) + "\n"
        return StreamingResponse(cached_generator(), media_type="application/x-ndjson")

    # Assemble retrieved context blocks
    context_str = "\n\n".join([f"[Source: {d.metadata.get('source', 'Unknown')} Page {d.metadata.get('page', 1)}]: {d.page_content}" for d in lc_docs])

    # 4. QA Chain (LCEL) - enforces strict context adherence and conciseness
    qa_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a precise AI assistant. Answer the user's question using ONLY the provided retrieved context. "
                   "Be concise, direct, and to the point. Do not extrapolate, add external information, or guess. "
                   "If the answer cannot be found in the context, state clearly: 'I cannot answer based on the provided documents.'\n\n"
                   "Context:\n{context}"),
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{question}")
    ])
    
    qa_chain = qa_prompt | model | StrOutputParser()

    def stream_generator():
        # Yield contexts first so UI displays references
        yield json.dumps({"contexts": contexts, "cached": False}) + "\n"
        
        full_answer = ""
        try:
            for chunk in qa_chain.stream({
                "context": context_str,
                "chat_history": chat_history,
                "question": standalone_query
            }):
                full_answer += chunk
                yield json.dumps({"answer_chunk": chunk}) + "\n"
        except Exception as e:
            yield json.dumps({"error": f"LLM streaming failed: {str(e)}"}) + "\n"
            return

        # 5. Save to SQL DB
        user_msg = ChatMessage(session_id=req.session_id, role="user", content=req.question)
        assistant_msg = ChatMessage(session_id=req.session_id, role="assistant", content=full_answer)
        db.add(user_msg)
        db.add(assistant_msg)
        db.commit()

        # 6. Write to Redis Cache (Exact + Semantic)
        set_cached_response(req.session_id, req.question, full_answer)
        if query_emb:
            set_semantic_cache(req.session_id, standalone_query, query_emb, full_answer)

        yield json.dumps({"done": True}) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")

@app.post("/eval")
async def evaluate_pipeline(req: EvalRequest, current_user: User = Depends(get_current_user_custom)):
    # Prepare data dictionary format for evaluator
    samples_dict = [
        {
            "question": s.question,
            "contexts": s.contexts,
            "answer": s.answer,
            "ground_truth": s.ground_truth
        } for s in req.samples
    ]
    
    from evaluate_rag import run_ragas_evaluation
    eval_results = await run_ragas_evaluation(samples_dict)
    return eval_results

# Serve static frontend SPA
from fastapi.staticfiles import StaticFiles

# Mount the static files directory
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def redirect_to_index():
    return RedirectResponse(url="/static/index.html")
