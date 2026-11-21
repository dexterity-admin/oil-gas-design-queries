# llm_cli.py
import sys
import json
# NO OTHER IMPORTS AT MODULE LEVEL (especially not llama_cpp, os, time, traceback, pathlib)

# Define constants for queue communication (though not used by this CLI directly for output to parent)
# These are more for conceptual alignment with the RAGThread if it were still using Queues directly.
STREAM_TOKEN_SENTINEL = "[[STREAM_TOKEN]]" # Not used by stdout streaming directly
STREAM_END_SENTINEL = "[[STREAM_END]]"     # Not used by stdout streaming directly
ERROR_SENTINEL = "[[ERROR]]"              # Error messages go to stderr
STATUS_UPDATE_SENTINEL = "[[STATUS_UPDATE]]"  # Status messages go to stderr for debugging

def main():
    # --- Step 2: "hello" print and isolated Llama import ---
    print("LLM_CLI: Entered main()", flush=True) # Print to stdout for parent to see
    try:
        # Only import llama_cpp (and other heavy libs) INSIDE main()
        from llama_cpp import Llama
        import os # os is needed for n_threads default
        import traceback # for detailed error reporting to stderr
        print("LLM_CLI: Imported llama_cpp and os successfully", flush=True)
    except Exception as import_err:
        # If this fails, it's a fundamental issue with llama_cpp in the Python env for the subprocess
        sys.stderr.write(f"ERROR_IMPORT_LLAMA_CPP: {import_err}\nTraceback:\n{traceback.format_exc()}\n")
        sys.exit(5) # Specific exit code for import failure

    # Read parameters from stdin
    try:
        input_json_str = sys.stdin.read()
        if not input_json_str:
            sys.stderr.write("ERROR_INPUT: No JSON input received from stdin.\n")
            sys.exit(3)
        params = json.loads(input_json_str)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR_INPUT_JSON: Failed to decode JSON: {e}. Received: {input_json_str[:200]}...\n")
        sys.exit(4)
    except Exception as e_read:
        sys.stderr.write(f"ERROR_INPUT_READ: Failed to read/parse stdin: {e_read}\n")
        sys.exit(7) # Different exit code for input read errors

    model_path = params.get("model_path")
    prompt_text = params.get("prompt")
    max_tokens = params.get("max_tokens", 512)
    temperature = params.get("temperature", 0.6)
    stop_strings = params.get("stop", ["<|im_end|>", "<|endoftext|>"])
    n_ctx = params.get("n_ctx", 2048)
    # Use os.cpu_count() now that os is imported
    n_threads = params.get("n_threads", os.cpu_count() if os.cpu_count() else 4)
    n_gpu_layers = params.get("n_gpu_layers", 0)

    if not model_path or not prompt_text:
        sys.stderr.write("ERROR_PARAMS: Missing 'model_path' or 'prompt' in input JSON.\n")
        sys.exit(6)

    # --- Step 3: Test model load alone ---
    llama_instance = None # Ensure it's defined for finally block
    try:
        print(f"LLM_CLI: Attempting to load model: {model_path}", flush=True)
        sys.stderr.write(f"STATUS_UPDATE: LLM_CLI loading model: {os.path.basename(model_path)}\n") # Send status to stderr
        sys.stderr.flush()

        llama_instance = Llama(
            model_path=str(model_path),
            n_ctx=int(n_ctx),
            n_threads=int(n_threads),
            n_gpu_layers=int(n_gpu_layers),
            verbose=False # Keep C++ logs quiet unless debugging llama.cpp itself
        )
        print(f"LLM_CLI: Model loaded successfully!", flush=True)
        sys.stderr.write(f"STATUS_UPDATE: LLM_CLI model loaded. Starting generation.\n")
        sys.stderr.flush()
    except Exception as e_load:
        sys.stderr.write(f"ERROR_LOADING_MODEL: {e_load}\nTraceback:\n{traceback.format_exc()}\n")
        sys.exit(1) # Exit code 1 for model loading errors

    # --- Step 4: Wire back the streaming and re-test ---
    try:
        print(f"LLM_CLI: Starting stream generation (prompt len {len(prompt_text)})...", flush=True)
        for token_chunk in llama_instance.create_completion(
            prompt=prompt_text,
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            stop=list(stop_strings), # Ensure it's a list
            stream=True
        ):
            if "choices" in token_chunk and len(token_chunk["choices"]) > 0:
                text_piece = token_chunk["choices"][0].get("text", "")
                if text_piece:
                    sys.stdout.write(text_piece)
                    sys.stdout.flush() # Crucial for parent process to read immediately
        # print(f"LLM_CLI: Stream generation finished.", flush=True) # Can be noisy
        sys.exit(0) # Success
    except Exception as e_inference:
        sys.stderr.write(f"ERROR_INFERENCE: {e_inference}\nTraceback:\n{traceback.format_exc()}\n")
        sys.exit(2) # Exit code 2 for inference errors
    finally:
        if llama_instance:
            # print("LLM_CLI: Deleting Llama instance.", flush=True) # Optional debug
            del llama_instance # Ensure cleanup

if __name__ == "__main__":
    # This script is intended to be called as a subprocess.
    # It reads parameters from stdin as a JSON string and writes results to stdout/stderr.
    main()