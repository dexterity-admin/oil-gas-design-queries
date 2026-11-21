# manager_thread.py
from PySide6.QtCore import QThread, Signal
import multiprocessing
import time
import os
from pathlib import Path
from tqdm import tqdm # For console progress, optional if GUI is primary
import chromadb
from sentence_transformers import SentenceTransformer
from functools import partial
from loguru import logger
import traceback
from datetime import timedelta
import queue # For queue.Empty exception
import json

from worker import process_file_task # Assumes worker.py has this main task function
import config # Import configuration including SUPPORTED_EXTENSIONS, COLLECTION_NAME, etc.

class ProcessingManagerThread(QThread):
    """
    Manages the multiprocessing pool for processing various file types (PDF, PPTX, DOCX).
    Handles resumability, formats metadata (like image paths list to string),
    prevents duplicate chunk additions within a single run, splits large results
    into suitable batches for ChromaDB, and communicates progress and logs
    (received from workers via a queue) back to the GUI thread.
    """
    # Signals for GUI communication
    progress_updated = Signal(int, int) # current_value, max_value (of files processed in *this run*)
    status_update = Signal(str)         # Text status updates for the GUI status bar
    # log_message Signal is kept for direct logging from manager, but worker logs come via queue
    log_message = Signal(str)
    processing_finished = Signal(bool)  # True if run completed without errors, False otherwise

    def __init__(self, pdf_folder, chroma_path, worker_count, tesseract_cmd_path, tessdata_prefix_path, parent=None):
        super().__init__(parent)
        self.pdf_folder = Path(pdf_folder)
        self.chroma_path = Path(chroma_path)
        self.worker_count = worker_count
        self.tesseract_cmd_path = tesseract_cmd_path
        self.tessdata_prefix_path = tessdata_prefix_path
        self._is_stopping = False
        self.pool = None
        self.async_results = []
        self.chroma_client = None
        self.collection = None
        self.embedding_model = None
        self.processed_log_path = Path(config.PROCESSED_LOG_FILE)
        self.processed_files_set = set() # Stores resolved paths of successfully processed files

        # Setup for inter-process communication (log queue)
        self.manager = multiprocessing.Manager()
        self.log_queue = self.manager.Queue()

        self._log(f"ManagerThread initialized with worker_count = {self.worker_count}")


    def _log(self, message, level="INFO"):
        """Helper to log messages using the configured Loguru logger."""
        # The GUI's Loguru sink will pick these up if configured
        if level == "INFO": logger.info(message)
        elif level == "WARNING": logger.warning(message)
        elif level == "ERROR": logger.error(message)
        elif level == "SUCCESS": logger.success(message)
        elif level == "DEBUG": logger.debug(message)
        else: logger.info(f"[{level}] {message}") # Fallback for custom levels

    def _load_processed_files(self):
        """Loads the set of successfully processed file paths from the log file for resumability."""
        self.processed_files_set = set()
        if self.processed_log_path.exists():
            self._log(f"Loading list of already processed files from: {self.processed_log_path}")
            try:
                with open(self.processed_log_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        stripped_line = line.strip()
                        if stripped_line: # Ensure line is not empty before resolving
                            self.processed_files_set.add(Path(stripped_line).resolve())
                self._log(f"Loaded {len(self.processed_files_set)} previously processed file paths.")
            except Exception as e:
                self._log(f"Error reading processed files log '{self.processed_log_path}': {e}", "ERROR")
        else:
             self._log(f"Processed files log '{config.PROCESSED_LOG_FILE}' not found. Starting fresh.")

    def _log_processed_file(self, file_path_str):
         """Appends a successfully processed file's absolute path to the log file."""
         file_path_resolved = Path(file_path_str).resolve()
         if file_path_resolved not in self.processed_files_set:
            # self._log(f"Marking '{Path(file_path_str).name}' as processed.", "DEBUG") # Optional: less verbose
            try:
                with open(self.processed_log_path, 'a', encoding='utf-8') as f:
                    f.write(str(file_path_resolved) + '\n')
                self.processed_files_set.add(file_path_resolved) # Add to set for current session too
            except Exception as e:
                 self._log(f"Error writing to processed files log '{self.processed_log_path}' for {file_path_str}: {e}", "ERROR")

    def _write_batch_to_chroma(self, batch_data):
        """Helper function to write a prepared batch to ChromaDB, handling potential errors."""
        if not batch_data or not batch_data.get('ids'):
            self._log("Attempted to write an empty or invalid batch.", "WARNING")
            return True, 0 # Treat as success with 0 errors if batch is empty

        batch_len = len(batch_data['ids'])
        self._log(f"Attempting to add batch of {batch_len} chunks to ChromaDB...")
        self.status_update.emit(f"Writing batch ({batch_len} chunks)...")
        try:
            # Final metadata value type check/conversion before adding to ChromaDB
            for meta_item in batch_data['metadatas']:
                 for key, value in meta_item.items():
                     if not isinstance(value, (str, int, float, bool)):
                          logger.warning(f"Batch Add: Converting non-scalar metadata type for key '{key}': {type(value)} to string.")
                          meta_item[key] = str(value) # Convert to string as a fallback

            # Perform the add operation
            self.collection.add(
                ids=batch_data['ids'],
                embeddings=batch_data['embeddings'],
                documents=batch_data['documents'],
                metadatas=batch_data['metadatas']
            )
            self._log(f"Batch of {batch_len} chunks added successfully to ChromaDB.")
            return True, 0 # Success, 0 errors
        except ValueError as ve: # Catch ChromaDB's metadata validation errors
            self._log(f"CRITICAL: ValueError adding batch (size {batch_len}) to ChromaDB: {ve}", "ERROR")
            self._log(f"This usually indicates a metadata value is not a str, int, float, or bool.", "ERROR")
            self._log(f"Sample problematic metadata (first item if available): {batch_data['metadatas'][0] if batch_data['metadatas'] else 'N/A'}", "ERROR")
            self._log(traceback.format_exc(), "ERROR")
            return False, batch_len # Failure, count all items in batch as errors
        except Exception as db_err: # Catch other ChromaDB errors (e.g., DuplicateIDError)
            self._log(f"CRITICAL: Exception adding batch (size {batch_len}) to ChromaDB: {db_err}", "ERROR")
            self._log(traceback.format_exc(), "ERROR")
            # Could check if db_err is DuplicateIDError here specifically if needed
            return False, batch_len # Failure, count all items in batch as errors

    def run(self):
        """Main execution logic of the processing thread."""
        self._is_stopping = False
        self.status_update.emit("Starting processing...")
        manager_start_time = time.time()
        error_count_this_run = 0 # Initialize error counter for this specific run

        try:
            # 1. Load list of previously processed files for resumability
            self._load_processed_files()

            # 2. Initialize ChromaDB and Image Directory
            self._log("Initializing ChromaDB..."); self.status_update.emit("Initializing vector database...")
            self.chroma_path.mkdir(parents=True, exist_ok=True)
            image_output_dir = self.chroma_path / config.IMAGE_OUTPUT_SUBFOLDER
            image_output_dir.mkdir(parents=True, exist_ok=True)
            self._log(f"Image output directory: {image_output_dir}")
            self.chroma_client = chromadb.PersistentClient(path=str(self.chroma_path))
            self.collection = self.chroma_client.get_or_create_collection(name=config.COLLECTION_NAME)
            collection_count = self.collection.count() # Get count before adding more
            self._log(f"Using ChromaDB collection: '{config.COLLECTION_NAME}' at {self.chroma_path} (Current Items: {collection_count})")

            # 3. Load Embedding Model
            self._log(f"Loading embedding model: {config.EMBEDDING_MODEL_NAME}..."); self.status_update.emit("Loading embedding model...")
            self.embedding_model = SentenceTransformer(config.EMBEDDING_MODEL_NAME)
            self._log("Embedding model loaded.")

            # 4. Find Supported Files and Filter Based on Processed Log
            self._log(f"Scanning for supported files ({', '.join(config.SUPPORTED_EXTENSIONS)}) in: {self.pdf_folder}")
            all_files_found = []
            for ext in config.SUPPORTED_EXTENSIONS: # Use defined supported extensions
                all_files_found.extend(self.pdf_folder.glob(f"*{ext}"))
                all_files_found.extend(self.pdf_folder.glob(f"*{ext.upper()}")) # Case-insensitive scan
            all_files_found = list(set(all_files_found)) # Remove duplicates if glob patterns overlap

            if not all_files_found:
                self._log(f"No supported files found in the directory: {self.pdf_folder}", "WARNING")
                self.status_update.emit("No supported files found.")
                self.processing_finished.emit(True); return # No errors, just no work

            files_to_process = []
            skipped_count = 0
            for file_path in all_files_found:
                 file_path_resolved = file_path.resolve()
                 if file_path_resolved in self.processed_files_set: skipped_count += 1
                 else: files_to_process.append(file_path)

            total_files_found = len(all_files_found); files_to_process_count = len(files_to_process)
            log_msg = f"Found {total_files_found} supported files. "
            if skipped_count > 0: log_msg += f"{skipped_count} already processed and will be skipped. "
            log_msg += f"Processing {files_to_process_count} new files."
            self._log(log_msg)
            if files_to_process_count == 0: # All files were previously processed
                 self.status_update.emit("All found files already processed."); self.progress_updated.emit(0, 0)
                 self.processing_finished.emit(True); return # No errors, task complete
            self.progress_updated.emit(0, files_to_process_count) # Set progress bar max
            status_msg = f"Processing {files_to_process_count} files"
            if skipped_count > 0: status_msg += f" ({skipped_count} skipped)"
            self.status_update.emit(status_msg + "...")

            # 5. Setup Multiprocessing Pool
            processed_count_this_run = 0
            worker_settings = {
                'tesseract_cmd': self.tesseract_cmd_path, 'tessdata_prefix': self.tessdata_prefix_path,
                'chroma_path': str(self.chroma_path), 'image_output_folder': config.IMAGE_OUTPUT_SUBFOLDER,
                'chunk_size': config.CHUNK_SIZE, 'chunk_overlap': config.CHUNK_OVERLAP,
                'log_queue': self.log_queue # Pass the log queue to workers
            }
            worker_func_with_settings = partial(process_file_task, settings=worker_settings)
            self._log(f"Starting multiprocessing pool with {self.worker_count} workers.")
            self.pool = multiprocessing.Pool(processes=self.worker_count)

            # 6. Submit Tasks Asynchronously
            self.async_results = []
            for file_obj in files_to_process: # Iterate Path objects
                if self._is_stopping: self._log("Stop requested during task submission.", "WARNING"); break
                async_result = self.pool.apply_async(worker_func_with_settings, (str(file_obj),)) # Pass path as string
                self.async_results.append(async_result)
            self.pool.close() # No more tasks will be submitted

            # 7. Process Results AND Log Queue
            self._log("All tasks submitted. Waiting for workers to complete and process results...")
            self.status_update.emit("Processing files and collecting results...")
            all_chunks_for_db = {'ids': [], 'documents': [], 'metadatas': [], 'embeddings': []}
            batch_size_threshold = 4096 # A safe batch size for ChromaDB with SQLite backend
            self._log(f"Using batch size threshold for ChromaDB: {batch_size_threshold}")
            tasks_completed_count = 0 # Count tasks whose results are retrieved
            processed_ids_in_session = set() # Track chunk IDs added in this run to prevent duplicates

            while tasks_completed_count < len(self.async_results) and not self._is_stopping:
                # Process logs from workers non-blockingly
                try:
                    while not self.log_queue.empty():
                        log_level, log_message_from_worker = self.log_queue.get_nowait()
                        if log_level == "DEBUG": logger.debug(log_message_from_worker)
                        elif log_level == "INFO": logger.info(log_message_from_worker)
                        elif log_level == "WARNING": logger.warning(log_message_from_worker)
                        elif log_level == "ERROR": logger.error(log_message_from_worker)
                        elif log_level == "SUCCESS": logger.success(log_message_from_worker)
                        else: logger.info(f"[{log_level}] {log_message_from_worker}") # Fallback
                except queue.Empty: pass # Queue is empty, continue
                except Exception as q_err: logger.error(f"Error reading log queue: {q_err}")

                # Check the next async result if available
                try:
                    async_result_to_check = self.async_results[tasks_completed_count]
                    if async_result_to_check.ready():
                         file_path_str, status, data_or_error = async_result_to_check.get()
                         tasks_completed_count += 1 # Increment after get()

                         if status == "success":
                            chunks_data = data_or_error # List of {'id':..., 'text':..., 'metadata':...}
                            if chunks_data:
                                 # Prepare data from this file for batching, checking for session duplicates
                                 file_batch_ids = []; file_batch_docs = []; file_batch_metadatas_processed = []
                                 texts_to_embed = []; original_items_for_embedding = []
                                 skipped_duplicates_in_file = 0

                                 # First pass: filter out duplicates already processed in this session
                                 for item in chunks_data:
                                     if item['id'] in processed_ids_in_session:
                                         skipped_duplicates_in_file += 1
                                         continue # Skip this duplicate chunk
                                     original_items_for_embedding.append(item)
                                     texts_to_embed.append(item['text'])

                                 if skipped_duplicates_in_file > 0: self._log(f"Skipped {skipped_duplicates_in_file} duplicate chunk IDs generated within file '{Path(file_path_str).name}'.", "WARNING")

                                 if not texts_to_embed: # All chunks for this file were duplicates or empty
                                     self._log(f"No new unique chunks to process for '{Path(file_path_str).name}'.", "WARNING")
                                 else:
                                     # Generate embeddings only for unique chunks
                                     self.status_update.emit(f"Generating embeddings for {Path(file_path_str).name}...")
                                     embeddings = self.embedding_model.encode(texts_to_embed, show_progress_bar=False).tolist()

                                     # Process metadata for these unique chunks
                                     for i, item in enumerate(original_items_for_embedding):
                                         metadata = item['metadata'].copy() # Work on a copy
                                         image_paths = metadata.get('image_paths', []) # Should be list from worker
                                         # Convert list of image paths to a single delimited string for ChromaDB
                                         if isinstance(image_paths, list) and image_paths:
                                             metadata['image_paths_str'] = "|".join(image_paths)
                                         else:
                                             metadata['image_paths_str'] = "" # Empty string if no images or not a list
                                         if 'image_paths' in metadata: del metadata['image_paths'] # Remove original list key
                                         file_batch_metadatas_processed.append(metadata) # Add processed metadata

                                         # Prepare data for batch accumulator
                                         current_id = item['id']
                                         file_batch_ids.append(current_id)
                                         file_batch_docs.append(item['text']) # This is texts_to_embed[i]
                                         processed_ids_in_session.add(current_id) # Track ID as added this session

                                     # Add the filtered, processed data to the main batch accumulator
                                     all_chunks_for_db['ids'].extend(file_batch_ids)
                                     all_chunks_for_db['documents'].extend(file_batch_docs)
                                     all_chunks_for_db['metadatas'].extend(file_batch_metadatas_processed)
                                     all_chunks_for_db['embeddings'].extend(embeddings) # Embeddings correspond to file_batch_docs

                                 # File's chunks (if any unique ones) processed, log file for resumability
                                 self._log_processed_file(file_path_str)
                                 processed_count_this_run += 1 # Increment file count here

                                 # Write to ChromaDB in smaller batches if accumulator exceeds threshold
                                 while len(all_chunks_for_db['ids']) >= batch_size_threshold:
                                     if self._is_stopping: break
                                     current_batch = {
                                         'ids': all_chunks_for_db['ids'][:batch_size_threshold],
                                         'documents': all_chunks_for_db['documents'][:batch_size_threshold],
                                         'metadatas': all_chunks_for_db['metadatas'][:batch_size_threshold],
                                         'embeddings': all_chunks_for_db['embeddings'][:batch_size_threshold],
                                     }
                                     success, errors_in_batch = self._write_batch_to_chroma(current_batch)
                                     if not success: error_count_this_run += 1 # Count 1 error event per failed batch write
                                     # Remove the processed batch from accumulator
                                     all_chunks_for_db['ids'] = all_chunks_for_db['ids'][batch_size_threshold:]
                                     all_chunks_for_db['documents'] = all_chunks_for_db['documents'][batch_size_threshold:]
                                     all_chunks_for_db['metadatas'] = all_chunks_for_db['metadatas'][batch_size_threshold:]
                                     all_chunks_for_db['embeddings'] = all_chunks_for_db['embeddings'][batch_size_threshold:]
                                 if self._is_stopping: break # Exit outer loop if stopped during batch writes
                            else: # Worker succeeded but produced no chunks
                                 self._log(f"Worker completed '{Path(file_path_str).name}' but yielded no data chunks.", "WARNING")
                                 self._log_processed_file(file_path_str); processed_count_this_run += 1

                         elif status == "error":
                             self._log(f"Worker failed processing '{Path(file_path_str).name}': {data_or_error}", "ERROR")
                             error_count_this_run += 1 # File processing error
                         elif status == "skipped":
                             self._log(f"Worker skipped unsupported file: '{Path(file_path_str).name}'", "INFO")
                             self._log_processed_file(file_path_str); processed_count_this_run += 1
                         else: # Unknown status from worker
                             self._log(f"Received unknown status '{status}' for {Path(file_path_str).name}", "ERROR")
                             error_count_this_run += 1

                         # Update GUI progress based on files processed
                         self.progress_updated.emit(processed_count_this_run, files_to_process_count)
                         self.status_update.emit(f"Processed {processed_count_this_run}/{files_to_process_count} files in this run...")
                    else: # Task result not ready yet
                         time.sleep(0.1) # Yield control briefly

                except KeyboardInterrupt:
                     self._log("KeyboardInterrupt received, attempting graceful stop...", "WARNING"); self.stop(); break
                except Exception as e: # Error getting result from async_result.get()
                    self._log(f"Error checking/retrieving result for task index {tasks_completed_count}: {e}", "ERROR")
                    self._log(traceback.format_exc(), "ERROR"); error_count_this_run += 1
                    tasks_completed_count += 1 # Assume this task failed and move past it
                    # Update progress on error to avoid stalling progress bar
                    self.progress_updated.emit(min(processed_count_this_run + error_count_this_run, files_to_process_count), files_to_process_count)

                if self._is_stopping: self._log("Stop requested during result processing loop.", "WARNING"); break

            # Final check of log queue after main loop
            self._log("Main processing loop finished. Checking for final log messages from workers...")
            time.sleep(0.2) # Short sleep to allow worker queues to flush
            try:
                while not self.log_queue.empty():
                    log_level, log_message_from_worker = self.log_queue.get_nowait()
                    logger.log(log_level, log_message_from_worker) # Use logger.log for dynamic level
            except queue.Empty: pass
            except Exception as q_err: logger.error(f"Error reading final log queue messages: {q_err}")

            # 8. Add any remaining chunks after the loop
            if not self._is_stopping and all_chunks_for_db['ids']:
                 if all_chunks_for_db['ids']: # Final check if batch has items
                    success, errors_in_batch = self._write_batch_to_chroma(all_chunks_for_db)
                    if not success: error_count_this_run += 1 # Count final batch failure as 1 error event

            # 9. Cleanup Worker Pool
            if self.pool:
                 self._log("Waiting for worker pool to join..."); self.pool.join(); self._log("Worker pool joined.")

        except Exception as e: # Catch unexpected errors in the manager thread itself
            self._log(f"An unexpected critical error occurred in the manager thread: {e}", "ERROR")
            self._log(traceback.format_exc(), "ERROR"); self.status_update.emit(f"Fatal error: {e}")
            if not self._is_stopping: self.processing_finished.emit(False) # Signal failure if not already stopping
            if self.pool: self._log("Terminating worker pool due to manager error.", "WARNING"); self.pool.terminate(); self.pool.join()
            return

        finally: # Final cleanup block
            if self.pool and not self.pool._state == multiprocessing.pool.CLOSE: # If pool exists and not closed
                 try:
                     if not self._is_stopping: self.pool.join() # Wait for normal exit if not stopping
                     else: self._log("Pool cleanup in finally block after stop request.", "DEBUG")
                 except Exception as final_join_err: self._log(f"Error during final pool join in finally block: {final_join_err}", "WARNING")
            try: # Ensure multiprocessing.Manager is shut down
                if hasattr(self, 'manager') and self.manager: self.manager.shutdown()
            except Exception as mgr_shutdown_err: self._log(f"Error shutting down multiprocessing manager: {mgr_shutdown_err}", "WARNING")

        # 10. Signal Finish
        manager_end_time = time.time(); duration = manager_end_time - manager_start_time
        final_message = f"Processing finished in {timedelta(seconds=int(duration))}. "
        # Calculate success based on files processed in *this run* minus errors occurred *in this run*
        successful_in_run = processed_count_this_run - error_count_this_run
        final_message += f"{successful_in_run}/{files_to_process_count} files processed successfully in this run."
        if skipped_count > 0: final_message += f" ({skipped_count} previously processed files were skipped)."
        if error_count_this_run > 0: final_message += f" ({error_count_this_run} errors occurred during this run)."
        self._log(final_message, "SUCCESS" if error_count_this_run == 0 else "WARNING"); self.status_update.emit(final_message)
        self.processing_finished.emit(error_count_this_run == 0) # Success if no errors in *this* run

    def stop(self):
        """Requests the processing to stop gracefully by terminating the worker pool."""
        if not self._is_stopping: # Prevent multiple stop calls
            self._log("Stop signal received by manager thread. Terminating workers...", "WARNING")
            self.status_update.emit("Stopping... (terminating workers)")
            self._is_stopping = True
            if self.pool:
                self._log("Terminating worker pool...", "WARNING")
                try: self.pool.terminate() # Forcefully stop worker processes
                except Exception as term_err: self._log(f"Error terminating worker pool: {term_err}", "ERROR")