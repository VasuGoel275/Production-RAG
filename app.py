import streamlit as st
import os
import requests
import time
import pandas as pd
from typing import Dict, List, Optional

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

st.set_page_config(
    page_title="AskDocX Dashboard",
    page_icon="📚",
    initial_sidebar_state="expanded",
    layout="wide"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Playfair+Display:ital,wght@0,600;1,500&display=swap');

html, body, [class*="css"] {
    font-family: 'Outfit', sans-serif;
}
.title-grad {
    background: linear-gradient(135deg, #FF6B6B 0%, #FF8E53 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-weight: 800;
}
.glass-card {
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    padding: 20px;
    margin-bottom: 15px;
}
.btn-grad {
    background-image: linear-gradient(to right, #FF6B6B 0%, #FF8E53  51%, #FF6B6B  100%);
    margin: 10px;
    padding: 15px 45px;
    text-align: center;
    text-transform: uppercase;
    transition: 0.5s;
    background-size: 200% auto;
    color: white;            
    box-shadow: 0 0 20px #eee;
    border-radius: 10px;
    display: block;
}
</style>
""", unsafe_allow_html=True)

def api_request(method: str, path: str, data: Optional[Dict] = None, files: Optional[Dict] = None, token: Optional[str] = None) -> requests.Response:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    url = f"{BACKEND_URL}{path}"
    try:
        if method.upper() == "GET":
            return requests.get(url, headers=headers, timeout=30)
        elif method.upper() == "POST":
            if files:
                return requests.post(url, headers=headers, files=files, data=data, timeout=60)
            post_timeout = 600 if "/eval" in path else 30
            return requests.post(url, headers=headers, json=data, timeout=post_timeout)
    except Exception as e:
        st.error(f"API Connection error to {url}: {e}")
        class DummyResp:
            status_code = 500
            text = "Backend unavailable"
            def json(self): return {"detail": "Backend unavailable"}
        return DummyResp()

def api_stream_request(path: str, data: Dict, token: Optional[str] = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{BACKEND_URL}{path}"
    return requests.post(url, headers=headers, json=data, stream=True, timeout=60)

# State Management
if "token" not in st.session_state:
    st.session_state.token = None
if "email" not in st.session_state:
    st.session_state.email = None
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None
if "selected_doc_ids" not in st.session_state:
    st.session_state.selected_doc_ids = []

# --- AUTHENTICATION FLOW ---
if not st.session_state.token:
    st.markdown("<h1 style='text-align: center;' class='title-grad'>📚 AskDocX</h1>", unsafe_allow_html=True)
    st.markdown("<h3 style='text-align: center;'>Production RAG Pipeline Workspace</h3>", unsafe_allow_html=True)
    st.write("")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        tab1, tab2 = st.tabs(["🔐 Sign In", "📝 Create Account"])
        
        with tab1:
            with st.form("login_form"):
                email = st.text_input("Email Address")
                password = st.text_input("Password", type="password")
                submit = st.form_submit_button("Sign In", type="primary")
                if submit:
                    resp = api_request("POST", "/login", data={"email": email, "password": password})
                    if resp.status_code == 200:
                        st.session_state.token = resp.json()["access_token"]
                        st.session_state.email = email
                        st.success("Successfully logged in! 🎉")
                        st.rerun()
                    else:
                        st.error(resp.json().get("detail", "Failed to login"))
                        
        with tab2:
            with st.form("signup_form"):
                new_email = st.text_input("Email Address")
                new_password = st.text_input("Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                submit = st.form_submit_button("Register Account")
                if submit:
                    if new_password != confirm_password:
                        st.error("Passwords do not match!")
                    else:
                        resp = api_request("POST", "/signup", data={"email": new_email, "password": new_password})
                        if resp.status_code == 201:
                            st.success("Successfully registered! You can now log in.")
                        else:
                            st.error(resp.json().get("detail", "Failed to register"))
    st.stop()

# --- MAIN DASHBOARD FLOW ---

# Fetch Sessions and Docs
sessions_resp = api_request("GET", "/sessions", token=st.session_state.token)
sessions_list = sessions_resp.json() if sessions_resp.status_code == 200 else []

docs_resp = api_request("GET", "/documents", token=st.session_state.token)
docs_list = docs_resp.json() if docs_resp.status_code == 200 else []

# Sidebar Content
with st.sidebar:
    st.markdown(f"🤖 **User**: `{st.session_state.email}`")
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.token = None
        st.session_state.email = None
        st.session_state.current_session_id = None
        st.session_state.selected_doc_ids = []
        st.rerun()
        
    st.divider()
    
    # Navigation/View selector
    app_mode = st.radio("⚡ Workspace Mode", ["💬 RAG Chatbot", "📊 RAGAS Evaluation"], index=0)
    
    st.divider()
    
    # Document Manager
    st.subheader("📄 Documents Ingest")
    uploaded_files = st.file_uploader("Upload PDF context", type=["pdf"], accept_multiple_files=True)
    if uploaded_files:
        if st.button("🚀 Ingest Uploaded Files", use_container_width=True):
            for file in uploaded_files:
                with st.spinner(f"Ingesting {file.name}..."):
                    files_payload = {"file": (file.name, file.getvalue(), "application/pdf")}
                    resp = api_request("POST", "/upload", files=files_payload, token=st.session_state.token)
                    if resp.status_code == 200:
                        st.success(f"✓ Uploaded {file.name}! Processing started...")
                    else:
                        st.error(f"Failed uploading {file.name}: {resp.json().get('detail')}")
            time.sleep(1)
            st.rerun()

    # Active Documents List / Selector
    if docs_list:
        st.write("---")
        st.markdown("**📁 Ingested Documents**")
        selected_docs = []
        for doc in docs_list:
            status_icon = "🟢" if doc["status"] == "completed" else "⏳" if doc["status"] == "processing" else "🔴"
            label = f"{status_icon} {doc['filename']}"
            
            # Allow select only if completed
            if doc["status"] == "completed":
                is_selected = st.checkbox(label, value=(doc["id"] in st.session_state.selected_doc_ids), key=doc["id"])
                if is_selected:
                    selected_docs.append(doc["id"])
            else:
                st.caption(f"{status_icon} {doc['filename']} ({doc['status']})")
        st.session_state.selected_doc_ids = selected_docs

        # Trigger auto-polling refresh if any document is currently in "processing" state
        has_processing = any(doc["status"] == "processing" for doc in docs_list)
        if has_processing:
            st.info("⏳ Processing uploaded files... page will auto-refresh.")
            time.sleep(3)
            st.rerun()

# Helper function to render a premium metric card
def render_metric_card(name: str, val: float):
    # Determine status color
    if val >= 0.8:
        color = "#2ecc71"  # Vibrant Emerald Green
    elif val >= 0.6:
        color = "#f1c40f"  # Gold / Amber
    else:
        color = "#e74c3c"  # Coral Red
        
    st.markdown(f"""
    <div style="
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 20px 15px;
        text-align: center;
        box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
        backdrop-filter: blur(10px);
        transition: transform 0.3s ease;
    ">
        <p style="margin: 0; color: #a0a0a0; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">{name}</p>
        <h2 style="margin: 8px 0; color: {color}; font-size: 36px; font-weight: 800; font-family: 'Outfit', sans-serif;">{val:.3f}</h2>
        <div style="background-color: rgba(255, 255, 255, 0.1); border-radius: 10px; height: 6px; width: 100%; margin-top: 10px;">
            <div style="background-color: {color}; width: {min(max(val * 100, 0), 100)}%; height: 6px; border-radius: 10px;"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def display_ragas_results(results: Dict[str, float]):
    if "error" in results:
        st.error(f"Evaluation failed: {results['error']}")
        return
    st.balloons()
    st.markdown("### 📈 RAGAS Pipeline Performance")
    col_metrics = st.columns(len(results))
    for i, (metric, val) in enumerate(results.items()):
        with col_metrics[i]:
            label = metric.replace("_", " ").title()
            render_metric_card(label, val)
    st.write("")

# Page 1: Chatbot Interface
if app_mode == "💬 RAG Chatbot":
    st.markdown("<h2 class='title-grad'>💬 Chat Workstation</h2>", unsafe_allow_html=True)
    st.caption("Ask questions about your uploaded documents.")
    
    # Columns for workspace
    col_session, col_chat = st.columns([1, 3])
    
    with col_session:
        st.markdown("### 🗂️ Chat Sessions")
        new_session_title = st.text_input("New Session Name", placeholder="e.g. Sales Q4 Analysis...")
        if st.button("➕ Create Session", use_container_width=True):
            if new_session_title.strip():
                resp = api_request("POST", "/sessions", data={"title": new_session_title}, token=st.session_state.token)
                if resp.status_code == 200:
                    st.session_state.current_session_id = resp.json()["id"]
                    st.success("Session created!")
                    st.rerun()
            else:
                st.warning("Please provide a session name.")
                
        st.write("")
        if sessions_list:
            for s in sessions_list:
                if s['title'] == "Automated Batch Eval Session":
                    continue
                active_str = "▶️ " if s["id"] == st.session_state.current_session_id else "🗒️ "
                if st.button(f"{active_str}{s['title']}", key=f"session_{s['id']}", use_container_width=True):
                    st.session_state.current_session_id = s["id"]
                    st.rerun()
        else:
            st.info("No active chat sessions. Create one above!")

    with col_chat:
        if st.session_state.current_session_id:
            # 1. Ask Question Input Form at the top
            with st.form("query_form", clear_on_submit=True):
                user_query = st.text_input("💬 Ask a question about the selected documents:", placeholder="Type your query here...")
                submit_query = st.form_submit_button("Send Query", type="primary")
                
            if submit_query and user_query.strip():
                query_payload = {
                    "session_id": st.session_state.current_session_id,
                    "question": user_query.strip(),
                    "document_ids": st.session_state.selected_doc_ids
                }
                
                try:
                    # Request Streaming response
                    response = api_stream_request("/chat/query/stream", query_payload, token=st.session_state.token)
                    if response.status_code == 200:
                        # Render dynamic streaming block
                        with st.chat_message("assistant"):
                            answer_placeholder = st.empty()
                            full_text = ""
                            for line in response.iter_lines():
                                if line:
                                    decoded_line = line.decode("utf-8")
                                    try:
                                        data_chunk = json.loads(decoded_line)
                                        
                                        # Render text chunks
                                        if "answer_chunk" in data_chunk:
                                            full_text += data_chunk["answer_chunk"]
                                            answer_placeholder.markdown(full_text)
                                            
                                        # Render full answers (e.g. from cache)
                                        elif "answer" in data_chunk:
                                            full_text = data_chunk["answer"]
                                            answer_placeholder.markdown(full_text)
                                            if data_chunk.get("cached"):
                                                st.caption("⚡ Served from Redis cache")
                                                
                                        # Render errors
                                        elif "error" in data_chunk:
                                            st.error(data_chunk["error"])
                                            st.stop()
                                    except Exception:
                                        pass
                        
                        # Wait briefly so the user sees the completed streaming text before reload
                        time.sleep(0.8)
                        st.rerun()
                    else:
                        try:
                            error_detail = response.json().get("detail", "Streaming request failed.")
                        except Exception:
                            error_detail = "Streaming request failed."
                        st.error(f"Error querying backend: {error_detail}")
                        st.stop()
                except Exception as e:
                    st.error(f"Error establishing stream: {e}")
                    st.stop()

            # 2. Previous History below the form (Newest Q&A pair at the top)
            msg_resp = api_request("GET", f"/sessions/{st.session_state.current_session_id}/messages", token=st.session_state.token)
            messages = msg_resp.json() if msg_resp.status_code == 200 else []
            
            if messages:
                st.write("---")
                st.markdown("### 💬 Chat History")
                
                # Pair user questions with assistant answers to keep each turn together
                qa_pairs = []
                for i in range(0, len(messages), 2):
                    if i + 1 < len(messages):
                        qa_pairs.append((messages[i], messages[i+1]))
                    else:
                        qa_pairs.append((messages[i], None))
                
                # Reverse pairs so newest turn is at the top
                qa_pairs.reverse()
                
                for u_msg, a_msg in qa_pairs:
                    if u_msg:
                        with st.chat_message("user"):
                            st.write(u_msg["content"])
                    if a_msg:
                        with st.chat_message("assistant"):
                            st.write(a_msg["content"])
                    st.write("") # Spacer between turns
            else:
                st.info("No messages in this session yet. Ask a question above!")
        else:
            st.info("👈 Please select or create a chat session from the left column.")



# Page 2: RAGAS Evaluations Dashboard
elif app_mode == "📊 RAGAS Evaluation":
    st.markdown("<h2 class='title-grad'>📊 RAGAS Evaluation Suite</h2>", unsafe_allow_html=True)
    st.caption("Measure the accuracy, relevancy, and faithfulness of your RAG pipeline using Google Gemini as the core evaluator.")
    
    eval_method = st.radio("⚡ Select Evaluation Method", ["✍️ Manual Sample Builder", "📁 Upload Batch File (JSON)"], horizontal=True)
    
    if eval_method == "✍️ Manual Sample Builder":
        st.markdown("### 🧪 Manual Evaluation Builder")
        with st.form("eval_builder"):
            st.write("**Add Evaluation Sample**")
            eval_q = st.text_input("Test Question", placeholder="e.g. What was the net income of the company in 2025?")
            eval_c = st.text_area("Retrieved Context Chunks (one chunk per line)")
            eval_a = st.text_area("Generated Answer")
            eval_gt = st.text_input("Ground Truth Answer (Recommended for Recall and Precision metrics)")
            add_sample = st.form_submit_button("Save Eval Trial")
            
            if add_sample:
                if "evals" not in st.session_state:
                    st.session_state.evals = []
                
                chunks_list = [c.strip() for c in eval_c.split("\n") if c.strip()]
                st.session_state.evals.append({
                    "question": eval_q,
                    "contexts": chunks_list,
                    "answer": eval_a,
                    "ground_truth": eval_gt.strip() if eval_gt.strip() else None
                })
                st.success("Sample added to active evaluation batch!")

        if "evals" in st.session_state and st.session_state.evals:
            st.write("---")
            st.markdown(f"#### Active Eval Batch ({len(st.session_state.evals)} samples)")
            
            df_samples = pd.DataFrame(st.session_state.evals)
            st.dataframe(df_samples)
            
            col_buttons = st.columns([1, 1, 4])
            with col_buttons[0]:
                if st.button("🔥 Run RAGAS", type="primary"):
                    with st.spinner("Evaluating using RAGAS and Gemini LLM..."):
                        eval_resp = api_request("POST", "/eval", data={"samples": st.session_state.evals}, token=st.session_state.token)
                        if eval_resp.status_code == 200:
                            display_ragas_results(eval_resp.json())
                        else:
                            st.error(f"Evaluation failed: {eval_resp.json().get('detail')}")
                            
            with col_buttons[1]:
                if st.button("🗑️ Clear Batch"):
                    st.session_state.evals = []
                    st.rerun()
        else:
            st.info("No evaluation samples constructed yet. Create at least one sample above to run RAGAS.")

    elif eval_method == "📁 Upload Batch File (JSON)":
        st.markdown("### 🤖 Automated Batch Evaluation")
        st.write("Upload a JSON file containing a list of test questions and ground-truth answers. The pipeline will automatically run these against the selected documents, gather the retrieved contexts and generated answers, and execute Ragas metrics.")
        
        with st.expander("ℹ️ Show JSON Format Example"):
            st.code("""[
  {
    "question": "What is the primary mission of the company?",
    "ground_truth": "The primary mission is to commercialize room-temperature quantum computing systems."
  },
  {
    "question": "What was the total revenue in FY 2026?",
    "ground_truth": "$154.3 million"
  }
]""", language="json")

        uploaded_json = st.file_uploader("Upload Evaluation Suite JSON", type=["json"])
        
        if uploaded_json:
            import json
            try:
                test_cases = json.loads(uploaded_json.getvalue().decode("utf-8"))
                st.success(f"✓ Parsed test set with {len(test_cases)} questions successfully!")
                
                if not st.session_state.selected_doc_ids:
                    st.warning("⚠️ Warning: No documents selected in the sidebar! Evaluating without specific doc filters.")
                
                if st.button("🚀 Run Automated Batch Evaluation", type="primary", use_container_width=True):
                    with st.spinner("Executing queries and running Ragas assessment..."):
                        session_resp = api_request("POST", "/sessions", data={"title": "Automated Batch Eval Session"}, token=st.session_state.token)
                        if session_resp.status_code != 200:
                            st.error("Failed to create temporary session.")
                            st.stop()
                        session_id = session_resp.json()["id"]
                        
                        compiled_samples = []
                        progress_bar = st.progress(0)
                        
                        for idx, case in enumerate(test_cases):
                            q = case.get("question")
                            gt = case.get("ground_truth", "")
                            if not q:
                                continue
                                
                            q_payload = {
                                "session_id": session_id,
                                "question": q,
                                "document_ids": st.session_state.selected_doc_ids
                            }
                            q_resp = api_request("POST", "/chat/query", data=q_payload, token=st.session_state.token)
                            if q_resp.status_code == 200:
                                res_data = q_resp.json()
                                answer = res_data.get("answer", "")
                                contexts = res_data.get("contexts", []) or []
                                context_texts = [c.get("text", "") for c in contexts]
                                
                                compiled_samples.append({
                                    "question": q,
                                    "contexts": context_texts,
                                    "answer": answer,
                                    "ground_truth": gt
                                })
                            
                            progress_bar.progress((idx + 1) / len(test_cases))
                            
                        if compiled_samples:
                            eval_resp = api_request("POST", "/eval", data={"samples": compiled_samples}, token=st.session_state.token)
                            if eval_resp.status_code == 200:
                                display_ragas_results(eval_resp.json())
                                
                                st.write("### 📝 Detailed Results Breakdown")
                                details_df = pd.DataFrame(compiled_samples)
                                st.dataframe(details_df)
                            else:
                                st.error(f"RAGAS evaluation failed: {eval_resp.json().get('detail')}")
                        else:
                            st.error("No queries could be processed successfully.")
            except Exception as e:
                st.error(f"Failed to load JSON file: {e}")