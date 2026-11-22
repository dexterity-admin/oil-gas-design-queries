# rag_worker.py
import threading
import traceback
import time
from pathlib import Path

# Imports needed for the work done in the thread
import chromadb
import requests
import json
from loguru import logger # Use logger for logging within the thread

class RAGWorker(threading.Thread):
    """
    Performs the RAG process (DB query, LLM API call) in a standard Python thread.
    Communicates results back via a callback function.
    """
    def __init__(self, question, db_path_str, collection_name_str, embedding_model,
                 llm_server_url, top_k, callback_fn, parent_pid):
        """
        Initializes the worker thread.

        Args:
            question (str): User's query.
            db_path_str (str): Path to the ChromaDB directory.
            collection_name_str (str): Name of the ChromaDB collection.
            embedding_model (SentenceTransformer): Pre-loaded embedding model instance.
            llm_server_url (str): Full URL to the LLM server's /generate endpoint.
            top_k (int): Number of results to retrieve.
            callback_fn (callable): Function to call with results (success: bool, data: dict|str, error: str).
                                    Expected signature: callback(success, result_data, error_string)
            parent_pid (int): PID of the parent process for logging context.
        """
        super().__init__(daemon=True) # Make thread daemon so it exits if main app dies
        self.question = question
        self.db_path = db_path_str
        self.collection_name = collection_name_str
        self.embedding_model = embedding_model
        self.llm_server_url = llm_server_url # Expecting full endpoint URL now
        self.top_k = top_k
        self.callback = callback_fn # Function provided by MainWindow to receive results
        self.parent_pid = parent_pid
        self._is_stopping = False # Basic stop flag (limited effect on blocking calls)

    def stop(self):
        # Note: This has limited effect as requests.post is blocking.
        # A more robust stop would require making requests non-blocking or using timeouts effectively.
        self._is_stopping = True
        logger.info(f"RAGWorker(Thread-{self.native_id}/Parent-{self.parent_pid}): Stop requested.")

    def run(self):
        """The main logic executed by this thread."""
        logger.info(f"RAGWorker(Thread-{self.native_id}/Parent-{self.parent_pid}): Starting RAG process.")
        chroma_client = None
        success = False
        result_data = None # Can be dict with answer/context/images or just answer string
        error_string = ""
        current_operation = "initializing"

        try:
            # 1. Initialize ChromaDB Client and Collection IN THIS THREAD
            current_operation = "initializing ChromaDB"
            logger.info(f"RAGWorker: Initializing ChromaDB client for path: {self.db_path}")
            if not Path(self.db_path).is_dir(): raise FileNotFoundError(f"DB path invalid: {self.db_path}")
            chroma_client = chromadb.PersistentClient(path=self.db_path)
            collection = chroma_client.get_collection(name=self.collection_name)
            db_count = collection.count()
            logger.info(f"RAGWorker: ChromaDB connected (Collection: {self.collection_name}, Items: {db_count}).")
            # Prepare initial result data including DB count
            result_data = {"db_count": db_count, "context": [], "images": [], "answer": ""}


            # 2. Generate Query Embedding
            current_operation = "generating query embedding"
            logger.debug("RAGWorker: Generating query embedding...")
            if self._is_stopping: raise InterruptedError("Stopped")
            query_embedding = self.embedding_model.encode([self.question]).tolist()
            logger.debug("RAGWorker: Query embedding generated.")

            # 3. Query ChromaDB
            current_operation = "querying ChromaDB"
            logger.debug(f"RAGWorker: Querying ChromaDB for top {self.top_k} results...")
            if self._is_stopping: raise InterruptedError("Stopped")
            results = collection.query(query_embeddings=query_embedding, n_results=self.top_k, include=['documents', 'metadatas'])
            logger.debug(f"RAGWorker: ChromaDB query returned {len(results.get('ids', [[]])[0])} results.")

            # 4. Process ChromaDB Results
            current_operation = "processing DB results"
            source_chunks_for_prompt = []
            all_image_paths = set()
            retrieved_context_for_gui = [] # Separate list for GUI display signal
            if results and results.get('ids') and results.get('ids')[0]:
                ids, docs, metas = results['ids'][0], results.get('documents', [[]])[0] or [], results.get('metadatas', [[]])[0] or []
                if not (len(ids) == len(docs) == len(metas)): raise ValueError("Inconsistent DB data length")
                for i in range(len(ids)):
                    doc_text = docs[i] or ""; meta = metas[i] or {}
                    processed_meta = meta.copy()
                    img_paths_str = processed_meta.get('image_paths_str', "")
                    img_list = [p for p in img_paths_str.split("|") if p] if img_paths_str else []
                    processed_meta['image_paths'] = img_list # Parsed list
                    source_chunks_for_prompt.append((doc_text, processed_meta)) # Used for LLM context
                    retrieved_context_for_gui.append((doc_text, processed_meta)) # Also send back to GUI
                    all_image_paths.update([p for p in img_list if Path(p).is_file()])
                result_data["context"] = retrieved_context_for_gui # Add context for GUI
                result_data["images"] = sorted(list(all_image_paths)) # Add images for GUI
            else:
                logger.warning("RAGWorker: ChromaDB query returned no valid results.")
                raise ValueError("No relevant context found in knowledge base.") # Raise error to stop processing

            if self._is_stopping: raise InterruptedError("Stopped")
            if not source_chunks_for_prompt:
                 logger.warning("RAGWorker: No processable context chunks found after processing DB results.")
                 raise ValueError("No processable context found.")

            # --- Chunk Validation Removed for Simplicity (as per ChatGPT Option B spirit) ---
            # We'll rely on the final API call handling potential issues.
            final_context_to_use = source_chunks_for_prompt
            logger.info("RAGWorker: Proceeding with all retrieved chunks for final prompt.")
            # --- End Removal ---

            # 5. Construct Final LLM Prompt
            current_operation = "constructing final LLM prompt"
            logger.debug("RAGWorker: Constructing Qwen2 ChatML prompt...")
            system_prompt = "You are a helpful assistant answering questions about Oil & Gas standards based ONLY on the provided context document snippets. Be concise and clear. If the context doesn't contain the information needed to answer the question accurately, state that the provided context does not contain the necessary information."
            context_pieces = [f"Source: {m.get('source', 'N/A')} (Page: {m.get('page', 'N/A')})\n{str(d or '')}" for d, m in final_context_to_use]
            context_text = "\n\n---\n\n".join(context_pieces)
            full_prompt = f"<|im_start|>system\n{system_prompt}\n<|im_end|>\n<|im_start|>user\nBased on the following context:\n\n{context_text}\n\n---\n\nAnswer this question: {self.question}<|im_end|>\n<|im_start|>assistant\n"
            logger.debug(f"RAGWorker: Final Prompt Length: {len(full_prompt)} chars.")

            # 6. Call LLM API (Non-Streaming HTTP POST)
            current_operation = "calling LLM API"
            logger.info(f"RAGWorker: Sending request to LLM Server: {self.llm_server_url}")
            payload = {"prompt": full_prompt, "max_tokens": 512, "temperature": 0.6, "stop": ["<|im_end|>", "<|endoftext|>"]}
            request_timeout = 180.0 # 3 minutes timeout for the API call

            response = requests.post(self.llm_server_url, json=payload, timeout=request_timeout)
            response.raise_for_status() # Check for HTTP errors

            response_data = response.json()
            generated_text = response_data.get("text", "")
            logger.info(f"RAGWorker: Received response from LLM API (len: {len(generated_text)} chars).")

            if not generated_text:
                logger.warning("RAGWorker: LLM API call successful but returned empty text.")
                # Optionally set a specific message or let it be empty
                result_data["answer"] = "(LLM generated no text for this query)"
            else:
                 result_data["answer"] = generated_text.strip() # Store the answer

            success = True # Mark as overall success

        except InterruptedError:
            error_string = "Processing stopped by user."
            logger.info(f"RAGWorker: {error_string}")
            success = False
        except Exception as e:
            tb = traceback.format_exc()
            error_string = f"Error during '{current_operation}': {e}\n{tb}"
            logger.error(f"RAGWorker: {error_string}")
            success = False
        finally:
            # Ensure callback is always called, even on error or stop
            logger.debug(f"RAGWorker: Calling callback with success={success}")
            try:
                # The callback will use QTimer.singleShot to update the GUI thread
                self.callback(success, result_data, error_string)
            except Exception as cb_e:
                 logger.error(f"RAGWorker: Error executing callback function: {cb_e}")
            logger.info(f"RAGWorker(Thread-{self.native_id}/Parent-{self.parent_pid}): Finished.")