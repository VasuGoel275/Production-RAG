// STATE VARIABLES
let token = localStorage.getItem('access_token');
let activeSessionId = null;
let selectedDocumentIds = new Set();
let allDocuments = [];

// DOM ELEMENTS
const authScreen = document.getElementById('auth-screen');
const appScreen = document.getElementById('app-screen');
const loginForm = document.getElementById('login-form');
const signupForm = document.getElementById('signup-form');
const userEmailDisplay = document.getElementById('user-email-display');
const docList = document.getElementById('document-list');
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const sessionList = document.getElementById('session-list');
const chatMessages = document.getElementById('chat-messages');
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const activeSessionTitle = document.getElementById('active-session-title');
const selectedDocsCount = document.getElementById('selected-docs-count');
const sourcesList = document.getElementById('sources-list');
const uploadProgressContainer = document.getElementById('upload-progress-container');
const uploadProgressBarFill = document.getElementById('upload-progress-bar-fill');
const uploadStatusText = document.getElementById('upload-status-text');

// INITIALIZE APP
document.addEventListener('DOMContentLoaded', () => {
    setupAuthListeners();
    setupDropZone();
    setupChatListeners();
    setupSessionListeners();
    
    if (token) {
        showAppScreen();
    } else {
        showAuthScreen();
    }
});

// TOAST NOTIFICATIONS
function showToast(message, isError = false) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.style.borderColor = isError ? 'var(--error-color)' : 'var(--border-color)';
    toast.classList.add('active');
    setTimeout(() => {
        toast.classList.remove('active');
    }, 3000);
}

// NAVIGATION
function showAuthScreen() {
    authScreen.classList.add('active');
    appScreen.classList.remove('active');
}

function showAppScreen() {
    authScreen.classList.remove('active');
    appScreen.classList.add('active');
    
    // Set user email in profile
    const payload = JSON.parse(atob(token.split('.')[1]));
    userEmailDisplay.textContent = payload.sub || 'user@company.com';
    
    // Load Workspace Data
    loadDocuments();
    loadSessions();
}

function toggleAuthForm(formName) {
    if (formName === 'signup') {
        loginForm.classList.remove('active');
        signupForm.classList.add('active');
    } else {
        loginForm.classList.add('active');
        signupForm.classList.remove('active');
    }
}

// LOGOUT
document.getElementById('logout-btn').addEventListener('click', () => {
    localStorage.removeItem('access_token');
    token = null;
    activeSessionId = null;
    selectedDocumentIds.clear();
    showAuthScreen();
});

// AUTHENTICATION API CALLS
function setupAuthListeners() {
    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('login-email').value;
        const password = document.getElementById('login-password').value;
        
        try {
            const res = await fetch('/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });
            const data = await res.json();
            if (res.ok) {
                token = data.access_token;
                localStorage.setItem('access_token', token);
                showAppScreen();
            } else {
                showToast(data.detail || 'Login failed', true);
            }
        } catch (err) {
            showToast('Unable to connect to server', true);
        }
    });

    signupForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('signup-email').value;
        const password = document.getElementById('signup-password').value;
        
        try {
            const res = await fetch('/signup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });
            const data = await res.json();
            if (res.ok) {
                showToast('Account created! Please sign in.');
                toggleAuthForm('login');
            } else {
                showToast(data.detail || 'Sign up failed', true);
            }
        } catch (err) {
            showToast('Unable to connect to server', true);
        }
    });
}

// DOCUMENT WORKSPACE
async function loadDocuments() {
    try {
        const res = await fetch('/documents', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) return handleUnauthorized();
        
        const data = await res.json();
        allDocuments = data;
        renderDocuments();
    } catch (err) {
        showToast('Failed to load documents', true);
    }
}

function renderDocuments() {
    if (allDocuments.length === 0) {
        docList.innerHTML = `<div class="sources-placeholder" style="padding: 1rem;"><p>No documents uploaded yet.</p></div>`;
        return;
    }
    
    docList.innerHTML = '';
    let hasProcessing = false;
    
    allDocuments.forEach(doc => {
        const isSelected = selectedDocumentIds.has(doc.id);
        const item = document.createElement('div');
        item.className = `doc-item ${isSelected ? 'selected' : ''}`;
        item.dataset.id = doc.id;
        
        let statusIcon = '<i class="fa-regular fa-file"></i>';
        if (doc.status === 'processing') {
            statusIcon = '<i class="fa-solid fa-spinner doc-status-icon processing"></i>';
            hasProcessing = true;
        } else if (doc.status === 'processed') {
            statusIcon = '<i class="fa-solid fa-circle-check doc-status-icon processed"></i>';
        } else if (doc.status === 'failed') {
            statusIcon = '<i class="fa-solid fa-circle-xmark doc-status-icon failed"></i>';
        }
        
        item.innerHTML = `
            ${statusIcon}
            <span class="doc-item-title" title="${doc.filename}">${doc.filename}</span>
        `;
        
        item.addEventListener('click', () => toggleDocumentSelection(doc.id));
        docList.appendChild(item);
    });
    
    // Auto-refresh document list if any are still processing
    if (hasProcessing) {
        setTimeout(loadDocuments, 3000);
    }
}

function toggleDocumentSelection(docId) {
    if (selectedDocumentIds.has(docId)) {
        selectedDocumentIds.delete(docId);
    } else {
        selectedDocumentIds.add(docId);
    }
    selectedDocsCount.textContent = `${selectedDocumentIds.size} documents selected for context`;
    renderDocuments();
}

function setupDropZone() {
    dropZone.addEventListener('click', () => fileInput.click());
    
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleMultipleFileUploads(e.target.files);
        }
    });
    
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });
    
    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });
    
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleMultipleFileUploads(e.dataTransfer.files);
        }
    });
}

async function handleMultipleFileUploads(files) {
    uploadProgressContainer.classList.remove('hidden');
    uploadProgressBarFill.style.width = '10%';
    let uploadedCount = 0;
    const total = files.length;
    
    for (let i = 0; i < total; i++) {
        const file = files[i];
        if (!file.name.endsWith('.pdf')) {
            showToast(`Skipping ${file.name}: Only PDF files are supported.`, true);
            continue;
        }
        
        uploadStatusText.textContent = `Uploading ${i + 1} of ${total}: ${file.name}...`;
        uploadProgressBarFill.style.width = `${((i) / total) * 80 + 10}%`;
        
        const formData = new FormData();
        formData.append('file', file);
        
        try {
            const res = await fetch('/upload', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
                body: formData
            });
            
            if (res.ok) {
                uploadedCount++;
            } else {
                const data = await res.json();
                showToast(`Failed to upload ${file.name}: ${data.detail || 'Error'}`, true);
            }
        } catch (err) {
            showToast(`Network error uploading ${file.name}`, true);
        }
    }
    
    uploadProgressBarFill.style.width = '100%';
    uploadStatusText.textContent = `Completed! Uploaded ${uploadedCount} of ${total} files.`;
    showToast(`Uploaded ${uploadedCount} documents successfully.`);
    loadDocuments();
    
    setTimeout(() => {
        uploadProgressContainer.classList.add('hidden');
    }, 3000);
}

// SESSIONS / CHAT HISTORIES
async function loadSessions() {
    try {
        const res = await fetch('/sessions', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) return handleUnauthorized();
        const data = await res.json();
        renderSessions(data);
    } catch (err) {
        showToast('Failed to load chat history', true);
    }
}

function renderSessions(sessions) {
    sessionList.innerHTML = '';
    sessions.forEach(sess => {
        if (sess.title.startsWith("Automated ")) return; // Filter out automated evaluation sessions
        
        const item = document.createElement('div');
        item.className = `session-item ${activeSessionId === sess.id ? 'active' : ''}`;
        item.dataset.id = sess.id;
        item.innerHTML = `
            <i class="fa-regular fa-message"></i>
            <span>${sess.title}</span>
        `;
        item.addEventListener('click', () => selectSession(sess.id, sess.title));
        sessionList.appendChild(item);
    });
}

function setupSessionListeners() {
    document.getElementById('new-session-btn').addEventListener('click', async () => {
        const title = prompt('Enter chat session title:');
        if (!title || !title.trim()) return;
        
        try {
            const res = await fetch('/sessions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({ title: title.trim() })
            });
            const data = await res.json();
            if (res.ok) {
                activeSessionId = data.id;
                loadSessions().then(() => selectSession(data.id, data.title));
            } else {
                showToast(data.detail || 'Failed to create session', true);
            }
        } catch (err) {
            showToast('Server connection failed', true);
        }
    });
}

async function selectSession(sessionId, title) {
    activeSessionId = sessionId;
    activeSessionTitle.textContent = title;
    
    // Enable Chat inputs
    chatInput.removeAttribute('disabled');
    chatInput.placeholder = "Ask anything about the selected documents...";
    sendBtn.removeAttribute('disabled');
    
    // Active session item in sidebar
    document.querySelectorAll('.session-item').forEach(item => {
        if (item.dataset.id === sessionId) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });
    
    // Load existing messages
    try {
        const res = await fetch(`/sessions/${sessionId}/messages`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const messages = await res.json();
        renderMessages(messages);
    } catch (err) {
        showToast('Failed to load messages', true);
    }
}

function renderMessages(messages) {
    chatMessages.innerHTML = '';
    sourcesList.innerHTML = `<div class="sources-placeholder"><i class="fa-solid fa-circle-info"></i><p>Ask a question to see matching context sources here.</p></div>`;
    
    if (messages.length === 0) {
        chatMessages.innerHTML = `
            <div class="welcome-message-box">
                <i class="fa-regular fa-comments text-gradient logo-large"></i>
                <h2>Chat Session Activated</h2>
                <p>No messages yet. Enter your question below to retrieve and analyze document context.</p>
            </div>
        `;
        return;
    }
    
    messages.forEach(msg => {
        appendMessage(msg.role, msg.content);
    });
}

function appendMessage(role, content) {
    // Remove welcome box if present
    const welcome = chatMessages.querySelector('.welcome-message-box');
    if (welcome) welcome.remove();
    
    const bubble = document.createElement('div');
    bubble.className = `message-bubble ${role}`;
    
    let iconClass = role === 'user' ? 'fa-solid fa-user' : 'fa-solid fa-robot';
    bubble.innerHTML = `
        <div class="message-icon"><i class="${iconClass}"></i></div>
        <div class="message-content">${content}</div>
    `;
    
    chatMessages.appendChild(bubble);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return bubble.querySelector('.message-content');
}

// CHAT QUERY & STREAMING RESPONSE
function setupChatListeners() {
    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const question = chatInput.value.trim();
        if (!question || !activeSessionId) return;
        
        chatInput.value = '';
        appendMessage('user', question);
        
        // Append placeholder assistant bubble
        const assistantBubbleContent = appendMessage('assistant', '<i class="fa-solid fa-spinner fa-spin"></i> Stream loading...');
        
        // Set payload
        const reqPayload = {
            session_id: activeSessionId,
            question: question,
            document_ids: Array.from(selectedDocumentIds)
        };
        
        try {
            const response = await fetch('/chat/query/stream', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify(reqPayload)
            });
            
            if (!response.ok) {
                const errData = await response.json();
                assistantBubbleContent.innerHTML = `<span style="color: var(--error-color);">Error: ${errData.detail || 'Failed to get answer'}</span>`;
                return;
            }
            
            // Handle Streaming Chunk Reading (NDJSON lines)
            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let fullText = "";
            let isFirstChunk = true;
            let buffer = "";
            
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                
                // Keep the last partial line in the buffer
                buffer = lines.pop();
                
                for (const line of lines) {
                    if (!line.trim()) continue;
                    try {
                        const chunk = JSON.parse(line);
                        
                        // 1. Process citations context chunk
                        if (chunk.contexts) {
                            renderCitations(chunk.contexts);
                        }
                        
                        // 2. Process streaming token text
                        if (chunk.answer_chunk) {
                            if (isFirstChunk) {
                                assistantBubbleContent.textContent = '';
                                isFirstChunk = false;
                            }
                            fullText += chunk.answer_chunk;
                            assistantBubbleContent.textContent = fullText;
                            chatMessages.scrollTop = chatMessages.scrollHeight;
                        }
                        
                        // 3. Process complete block answer (cached or fallback)
                        if (chunk.answer) {
                            assistantBubbleContent.textContent = chunk.answer;
                            chatMessages.scrollTop = chatMessages.scrollHeight;
                        }
                        
                        // 4. Process backend errors
                        if (chunk.error) {
                            assistantBubbleContent.innerHTML = `<span style="color: var(--error-color);">Error: ${chunk.error}</span>`;
                        }
                    } catch (e) {
                        console.error('Failed to parse line: ', line, e);
                    }
                }
            }
        } catch (err) {
            assistantBubbleContent.innerHTML = `<span style="color: var(--error-color);">Network Connection Error</span>`;
        }
    });
}

function renderCitations(contexts) {
    if (!contexts || contexts.length === 0) {
        sourcesList.innerHTML = `<div class="sources-placeholder"><p>No relevant context chunks found.</p></div>`;
        return;
    }
    
    sourcesList.innerHTML = '';
    contexts.forEach((ctx, index) => {
        const card = document.createElement('div');
        card.className = 'source-card';
        card.innerHTML = `
            <div class="source-meta">
                <span>[Source ${index + 1}]</span>
                <span>Page ${ctx.page || 1}</span>
            </div>
            <p><strong>File:</strong> ${ctx.filename}</p>
            <p style="margin-top: 0.5rem; color: var(--text-muted); font-style: italic;">"${ctx.text.substring(0, 160)}..."</p>
        `;
        sourcesList.appendChild(card);
    });
}

// TAB NAVIGATION
function switchTab(tabName) {
    document.querySelectorAll('.nav-tab').forEach(tab => tab.classList.remove('active'));
    document.querySelectorAll('.tab-view').forEach(view => view.classList.remove('active'));
    
    if (tabName === 'chat') {
        document.getElementById('tab-chat').classList.add('active');
        document.getElementById('view-chat').classList.add('active');
    } else {
        document.getElementById('tab-eval').classList.add('active');
        document.getElementById('view-eval').classList.add('active');
        setupEvalListeners();
    }
}

// RAGAS AUTOMATED EVALUATION SECTION
let loadedEvalSuite = [];

function setupEvalListeners() {
    const evalDropZone = document.getElementById('eval-drop-zone');
    const evalFileInput = document.getElementById('eval-file-input');
    const runEvalBtn = document.getElementById('run-eval-btn');
    
    // Dropzone listeners
    if (evalDropZone && !evalDropZone.dataset.listener) {
        evalDropZone.dataset.listener = "true";
        evalDropZone.addEventListener('click', () => evalFileInput.click());
        
        evalFileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                parseEvalFile(e.target.files[0]);
            }
        });
        
        evalDropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            evalDropZone.classList.add('dragover');
        });
        
        evalDropZone.addEventListener('dragleave', () => {
            evalDropZone.classList.remove('dragover');
        });
        
        evalDropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            evalDropZone.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                parseEvalFile(e.dataTransfer.files[0]);
            }
        });
        
        runEvalBtn.addEventListener('click', runAutomatedEvaluation);
    }
}

function parseEvalFile(file) {
    if (!file.name.endsWith('.json')) {
        showToast('Please upload a JSON file.', true);
        return;
    }
    
    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const suite = JSON.parse(e.target.result);
            if (!Array.isArray(suite)) {
                showToast('JSON must contain an array of test cases.', true);
                return;
            }
            loadedEvalSuite = suite;
            document.getElementById('eval-suite-info').classList.remove('hidden');
            document.getElementById('eval-suite-status').textContent = `✓ Suite loaded: ${suite.length} samples`;
            showToast(`Test suite loaded with ${suite.length} test cases.`);
        } catch (err) {
            showToast('Failed to parse JSON file.', true);
        }
    };
    reader.readAsText(file);
}

async function runAutomatedEvaluation() {
    if (loadedEvalSuite.length === 0) return;
    
    const evalProgressContainer = document.getElementById('eval-progress-container');
    const evalProgressBarFill = document.getElementById('eval-progress-bar-fill');
    const evalStatusText = document.getElementById('eval-status-text');
    const runEvalBtn = document.getElementById('run-eval-btn');
    const resultsCard = document.getElementById('eval-results-card');
    
    runEvalBtn.setAttribute('disabled', 'true');
    evalProgressContainer.classList.remove('hidden');
    resultsCard.classList.add('hidden');
    
    // 1. Create a temporary RAGAS eval chat session
    let evalSessionId = null;
    try {
        const sessionRes = await fetch('/sessions', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ title: "Automated Ragas Eval Session" })
        });
        const sessionData = await sessionRes.json();
        if (sessionRes.ok) {
            evalSessionId = sessionData.id;
        } else {
            showToast('Failed to start evaluation session', true);
            runEvalBtn.removeAttribute('disabled');
            return;
        }
    } catch (err) {
        showToast('Connection failed', true);
        runEvalBtn.removeAttribute('disabled');
        return;
    }
    
    // 2. Query each question against the pipeline
    const compiledSamples = [];
    const total = loadedEvalSuite.length;
    
    for (let i = 0; i < total; i++) {
        const testCase = loadedEvalSuite[i];
        evalStatusText.textContent = `Querying RAG pipeline (${i + 1}/${total}): "${testCase.question}"`;
        evalProgressBarFill.style.width = `${((i + 1) / total) * 50}%`; // First 50% for query loops
        
        try {
            const queryRes = await fetch('/chat/query/stream', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({
                    session_id: evalSessionId,
                    question: testCase.question,
                    document_ids: Array.from(selectedDocumentIds)
                })
            });
            
            if (queryRes.ok) {
                // Read NDJSON stream response
                const reader = queryRes.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let answer = "";
                let contexts = [];
                let buffer = "";
                
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();
                    
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const chunk = JSON.parse(line);
                            if (chunk.contexts) contexts = chunk.contexts;
                            if (chunk.answer_chunk) answer += chunk.answer_chunk;
                            if (chunk.answer && chunk.cached) answer = chunk.answer;
                        } catch (e) {}
                    }
                }
                
                compiledSamples.push({
                    question: testCase.question,
                    contexts: contexts.map(c => c.text),
                    answer: answer,
                    ground_truth: testCase.ground_truth || ""
                });
            }
        } catch (err) {
            console.error('Failed to query evaluation case', err);
        }
    }
    
    // 3. Send compiled data to the /eval endpoint for metrics
    evalStatusText.textContent = 'Calculating RAGAS metrics scores (running Gemini eval)...';
    evalProgressBarFill.style.width = '75%';
    
    try {
        const evalRes = await fetch('/eval', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ samples: compiledSamples })
        });
        
        const evalData = await evalRes.json();
        if (evalRes.ok) {
            evalProgressBarFill.style.width = '100%';
            evalStatusText.textContent = 'Evaluation complete!';
            
            // Render Ragas Metric scores
            document.getElementById('val-faithfulness').textContent = evalData.faithfulness ? evalData.faithfulness.toFixed(3) : '0.000';
            document.getElementById('val-relevancy').textContent = evalData.answer_relevancy ? evalData.answer_relevancy.toFixed(3) : '0.000';
            document.getElementById('val-recall').textContent = evalData.context_recall ? evalData.context_recall.toFixed(3) : '0.000';
            document.getElementById('val-precision').textContent = evalData.context_precision ? evalData.context_precision.toFixed(3) : '0.000';
            
            resultsCard.classList.remove('hidden');
            showToast('RAGAS Evaluation complete!');
        } else {
            showToast(evalData.detail || 'Evaluation metrics calculation failed', true);
        }
    } catch (err) {
        showToast('RAGAS API Connection Error', true);
    } finally {
        runEvalBtn.removeAttribute('disabled');
        setTimeout(() => {
            evalProgressContainer.classList.add('hidden');
        }, 3000);
    }
}

// UTILITY ERROR HANDLING
function handleUnauthorized() {
    localStorage.removeItem('access_token');
    token = null;
    showToast('Session expired. Please log in again.', true);
    showAuthScreen();
}

