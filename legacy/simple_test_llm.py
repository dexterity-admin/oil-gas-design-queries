# simple_test_llm.py
from llama_cpp import Llama
import os
import traceback

MODEL_PATH = r"D:\Py\Oil & Gas Design Queries\models\qwen2.5-0.5b-instruct-q5_k_m.gguf"
# --- >> ENSURE THIS IS THE CORRECT PATH TO THE FILE JUST SAVED BY SQA APP << ---
PROMPT_FILE = r"D:\Py\Oil & Gas Design Queries\debug_crashing_chunk_1.txt" # Or its full path

try:
    print(f"Loading LLM from: {MODEL_PATH}")
    n_threads = os.cpu_count() if os.cpu_count() else 4
    llm = Llama(
        model_path=MODEL_PATH,
        n_ctx=2048,
        n_gpu_layers=0,    # Force CPU
        n_threads=n_threads,
        verbose=True        # Max output from llama.cpp
    )
    print("LLM loaded.")

    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        # Read only the "--- FULL TEST PROMPT ---" part for accurate testing
        content = f.read()
        prompt_start_marker = "--- FULL TEST PROMPT ---"
        prompt_text = content.split(prompt_start_marker)[1].strip() # Get text after marker
        # Or, if you saved ONLY the prompt, just use: prompt_text = f.read()

    print(f"\n--- Sending Prompt (Length: {len(prompt_text)} chars) FROM FILE ---")
    print(f"--- Prompt Preview (first 300): ---\n{prompt_text[:300]}\n-----------------------------------\n")


    stream = llm(
        prompt_text,
        max_tokens=50, # Small output for test
        stop=["<|im_end|>", "<|endoftext|>"],
        temperature=0.1, # Low temp for predictability
        stream=True
    )

    print("--- LLM Response Stream ---")
    full_response = ""
    for chunk in stream:
        text_piece = chunk["choices"][0]["text"]
        print(text_piece, end="", flush=True)
        full_response += text_piece
    print("\n--- Stream Finished ---")
    print(f"Full response length: {len(full_response)}")

except Exception as e:
    print(f"\n--- ERROR IN SIMPLE TEST ---")
    traceback.print_exc()