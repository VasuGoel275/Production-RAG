import os
import time
import math
import asyncio
from typing import List, Dict, Any
from datasets import Dataset
from ragas import aevaluate, RunConfig
from ragas.metrics import faithfulness, answer_relevancy, context_recall, context_precision
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings.base import LangchainEmbeddingsWrapper
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

async def run_ragas_evaluation(eval_samples: List[Dict[str, Any]]) -> Dict[str, float]:
    """Runs standard Ragas evaluation using Gemini LLM-as-a-judge asynchronously.
    
    This matches industry-standard evaluation practices for RAG pipelines.
    It measures:
      - Faithfulness (Groundedness of answer in retrieved contexts)
      - Answer Relevancy (Alignment of answer with the user's question)
      - Context Recall (Retrieval completeness against ground truth)
      - Context Precision (Retrieval noise/relevancy level)
    
    Args:
        eval_samples: List of dicts, each having 'question', 'contexts', 'answer',
                      and optionally 'ground_truth'.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is not set")
        
    if not eval_samples:
        return {"error": "No evaluation samples provided"}

    # Initialize Gemini models for evaluation
    evaluator_llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",  # Stable, cost-efficient model for evaluations
        google_api_key=GEMINI_API_KEY,
        temperature=0.0,
        max_retries=10,
        timeout=180
    )
    evaluator_embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        google_api_key=GEMINI_API_KEY
    )
    ragas_llm = LangchainLLMWrapper(evaluator_llm)
    ragas_embeddings = LangchainEmbeddingsWrapper(evaluator_embeddings)

    # Determine metrics to run based on whether ground truth is provided
    metrics = [faithfulness, answer_relevancy]
    has_ground_truth = any(s.get("ground_truth") for s in eval_samples)
    if has_ground_truth:
        metrics.extend([context_recall, context_precision])

    # Convert samples to Ragas-compatible dataset format
    questions = [s["question"] for s in eval_samples]
    contexts = [s["contexts"] for s in eval_samples]
    answers = [s["answer"] for s in eval_samples]
    
    dataset_dict = {
        "question": questions,
        "contexts": contexts,
        "answer": answers
    }
    
    if has_ground_truth:
        dataset_dict["ground_truth"] = [s.get("ground_truth", "") for s in eval_samples]

    dataset = Dataset.from_dict(dataset_dict)

    accumulated_scores = {}
    successful_samples = 0

    print(f"Starting standard Ragas evaluation for {len(dataset)} samples...")

    for idx in range(len(dataset)):
        # Construct single sample dataset
        sample_dict = {
            "question": [dataset[idx]["question"]],
            "contexts": [dataset[idx]["contexts"]],
            "answer": [dataset[idx]["answer"]]
        }
        if has_ground_truth:
            sample_dict["ground_truth"] = [dataset[idx]["ground_truth"]]
            
        single_dataset = Dataset.from_dict(sample_dict)
        
        try:
            print(f"Evaluating sample {idx + 1}/{len(dataset)}...")
            run_config = RunConfig(timeout=300, max_workers=1)
            result = await aevaluate(
                dataset=single_dataset,
                metrics=metrics,
                llm=ragas_llm,
                embeddings=ragas_embeddings,
                run_config=run_config
            )
            
            # Aggregate scores safely across different Ragas versions
            if hasattr(result, "scores"):
                if isinstance(result.scores, list):
                    scores_dict = result.scores[0] if len(result.scores) > 0 else {}
                else:
                    scores_dict = result.scores
            else:
                scores_dict = dict(result)

            for metric, score in scores_dict.items():
                if score is not None and not math.isnan(score):
                    metric_name = str(metric)
                    accumulated_scores[metric_name] = accumulated_scores.get(metric_name, 0.0) + score
            successful_samples += 1
            
        except Exception as e:
            print(f"Error evaluating sample {idx + 1}: {e}")
            
        # Respect rate limits on Gemini free tier (15s sleep)
        if idx < len(dataset) - 1:
            print("Sleeping 15 seconds to respect query rate limits...")
            await asyncio.sleep(15)
            
    if successful_samples == 0:
        return {"error": "All samples failed to evaluate. Your API key might be exhausted/rate-limited."}
        
    # Calculate averages
    averages = {metric: round(total / successful_samples, 3) for metric, total in accumulated_scores.items()}
    return averages

