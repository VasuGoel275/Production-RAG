import os
import sys
import json
import requests
import argparse
from typing import List, Dict, Any

def run_batch_eval():
    parser = argparse.ArgumentParser(description="Automated Ragas Batch Evaluation CLI")
    parser.add_argument("--email", required=True, help="User email address")
    parser.add_argument("--password", required=True, help="User password")
    parser.add_argument("--test-set", required=True, help="Path to JSON test set file")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000", help="FastAPI backend host URL")
    parser.add_argument("--doc-ids", nargs="*", help="Specific document UUIDs to evaluate against")
    args = parser.parse_args()

    print("\n=== STARTING AUTOMATED RAGAS BATCH EVALUATION ===")
    
    # 1. Login
    login_url = f"{args.backend_url}/login"
    print(f"Logging in as {args.email}...")
    resp = requests.post(login_url, json={"email": args.email, "password": args.password})
    if resp.status_code != 200:
        print(f"Error logging in: {resp.text}")
        sys.exit(1)
        
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("Login successful! Token acquired.")

    # 2. Load test set
    if not os.path.exists(args.test_set):
        print(f"Error: Test set file '{args.test_set}' does not exist.")
        sys.exit(1)
        
    with open(args.test_set, "r", encoding="utf-8") as f:
        try:
            test_cases = json.load(f)
        except Exception as e:
            print(f"Error parsing JSON: {e}")
            sys.exit(1)

    if not isinstance(test_cases, list) or len(test_cases) == 0:
        print("Error: Test set must be a list containing at least one test case object.")
        sys.exit(1)
        
    print(f"Loaded {len(test_cases)} test cases from '{args.test_set}'.")

    # 3. Create temporary chat session
    session_url = f"{args.backend_url}/sessions"
    resp = requests.post(session_url, json={"title": "Ragas Automated Batch Eval Session"}, headers=headers)
    if resp.status_code != 200:
        print(f"Error creating chat session: {resp.text}")
        sys.exit(1)
    session_id = resp.json()["id"]
    print(f"Created temporary chat session with ID: {session_id}")

    # 4. Generate responses and extract contexts
    query_url = f"{args.backend_url}/chat/query"
    eval_samples = []
    
    for idx, case in enumerate(test_cases):
        question = case.get("question")
        ground_truth = case.get("ground_truth")
        if not question:
            print(f"  [Skip] Test case {idx+1} is missing 'question'.")
            continue
            
        print(f"\n[{idx+1}/{len(test_cases)}] Querying: '{question}'...")
        query_payload = {
            "session_id": session_id,
            "question": question,
            "document_ids": args.doc_ids
        }
        
        q_resp = requests.post(query_url, json=query_payload, headers=headers)
        if q_resp.status_code != 200:
            print(f"  [ERROR] Chat query failed: {q_resp.text}")
            continue
            
        res_data = q_resp.json()
        answer = res_data.get("answer", "")
        contexts = res_data.get("contexts", []) or []
        
        # Flatten contexts to list of text strings for Ragas evaluation
        context_strings = [c.get("text", "") for c in contexts]
        
        eval_samples.append({
            "question": question,
            "contexts": context_strings,
            "answer": answer,
            "ground_truth": ground_truth if ground_truth else ""
        })
        print(f"  Generated Answer: {answer[:80]}...")
        print(f"  Retrieved {len(context_strings)} context chunks.")

    # 5. Run Ragas Evaluation
    if not eval_samples:
        print("Error: No successfully queried samples to evaluate.")
        sys.exit(1)
        
    print("\nSending gathered queries & contexts to Ragas evaluator...")
    eval_url = f"{args.backend_url}/eval"
    
    eval_resp = requests.post(eval_url, json={"samples": eval_samples}, headers=headers)
    if eval_resp.status_code != 200:
        print(f"Ragas evaluation failed: {eval_resp.text}")
        sys.exit(1)
        
    scores = eval_resp.json()
    
    # 6. Output Report
    print("\n================ EVALUATION SUMMARY ================ ")
    for metric, score in scores.items():
        if metric != "error":
            print(f" * {metric.replace('_', ' ').title()}: {score:.4f}")
    print("==================================================== ")

    # Write report as a clean JSON file instead of markdown
    report_path = "ragas_report.json"
    report_data = {
        "summary": scores,
        "samples": eval_samples
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
            
    print(f"\nWritten detailed JSON report to: {os.path.abspath(report_path)}")

if __name__ == "__main__":
    run_batch_eval()
