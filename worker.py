# worker.py
import fitz # PyMuPDF
import pytesseract
from PIL import Image, UnidentifiedImageError
import io
import os
import time
import traceback
from pathlib import Path
import multiprocessing
from config import sanitize_filename, SUPPORTED_EXTENSIONS # Import constants

# --- >> NEW: Import pptx and docx libraries << ---
try:
    import pptx
    from pptx.enum.shapes import MSO_SHAPE_TYPE
except ImportError:
    pptx = None # Flag if library is missing
try:
    import docx
except ImportError:
    docx = None # Flag if library is missing
# --- >> END NEW << ---

# --- Global function to handle logging via Queue ---
def log_to_queue(queue_obj, level, message):
    """Formats message with process info and puts it on the provided queue."""
    if not queue_obj: return
    try:
        process = multiprocessing.current_process()
        log_entry = (level.upper(), f"{process.name}:{process.pid} | {message}")
        queue_obj.put(log_entry)
    except Exception as e:
        process = multiprocessing.current_process()
        print(f"QUEUE LOG ERROR for {process.name}:{process.pid}: {e} - Message: {message}")

# --- simple_chunker remains the same ---
def simple_chunker(text, chunk_size, chunk_overlap):
    """Naive chunking by splitting text and joining."""
    # (Code for simple_chunker is unchanged)
    if not text: return []
    words = text.split(); chunks = []; current_chunk = []; current_len = 0
    for word in words:
        if current_len == 0 or current_len + len(word) + 1 <= chunk_size:
            current_chunk.append(word); current_len += len(word) + (1 if current_len > 0 else 0)
        else:
            chunks.append(" ".join(current_chunk)); overlap_word_count = 0
            if chunk_overlap > 0 and current_chunk:
                 temp_overlap_len = 0
                 for i in range(len(current_chunk) - 1, -1, -1):
                     word_len = len(current_chunk[i]) + 1
                     if temp_overlap_len + word_len <= chunk_overlap: temp_overlap_len += word_len; overlap_word_count += 1
                     else: break
            overlap_words = current_chunk[-overlap_word_count:] if overlap_word_count > 0 else []
            current_chunk = overlap_words + [word]; current_len = len(" ".join(current_chunk))
    if current_chunk: chunks.append(" ".join(current_chunk))
    return chunks

# --- process_pdf_page remains largely the same, now a helper ---
def _process_pdf_page(page, page_num, pdf_path_str, settings, worker_log_queue):
    """Helper to process a single PDF page."""
    # (Code for _process_pdf_page is unchanged from previous version)
    # ... uses log_to_queue, extracts text, images, performs OCR ...
    page_text = page.get_text("text", sort=True)
    image_paths_on_page = []
    try:
        img_list = page.get_images(full=True)
        if img_list:
            log_to_queue(worker_log_queue, "DEBUG", f"PDF Page {page_num+1}: Found {len(img_list)} images.")
            image_output_dir = Path(settings['chroma_path']) / settings['image_output_folder']
            pdf_base_name = sanitize_filename(Path(pdf_path_str).stem)
            for img_index, img_info in enumerate(img_list):
                xref = img_info[0]; base_image = None
                try: base_image = page.parent.extract_image(xref)
                except Exception as ex_img_err: log_to_queue(worker_log_queue, "WARNING", f"PDF Page {page_num+1}: Error extracting image xref {xref}: {ex_img_err}")
                if not base_image: continue
                image_bytes = base_image["image"]; image_ext = base_image["ext"]
                image_filename = f"{pdf_base_name}_page_{page_num+1}_img_{img_index}.{image_ext}"
                save_path = image_output_dir / image_filename
                image_paths_on_page.append(str(save_path))
                try:
                    with open(save_path, "wb") as img_file: img_file.write(image_bytes)
                except Exception as save_err:
                     log_to_queue(worker_log_queue, "ERROR", f"Failed to save image {save_path}: {save_err}")
                     if str(save_path) in image_paths_on_page: image_paths_on_page.remove(str(save_path))
    except Exception as img_list_err: log_to_queue(worker_log_queue, "ERROR", f"PDF Page {page_num+1}: Error getting image list: {img_list_err}")
    tesseract_cmd = settings.get('tesseract_cmd'); tessdata_prefix = settings.get('tessdata_prefix')
    if tessdata_prefix and os.path.isdir(tessdata_prefix): os.environ['TESSDATA_PREFIX'] = tessdata_prefix
    if len(page_text.strip()) < 50:
        log_to_queue(worker_log_queue, "DEBUG", f"PDF Page {page_num+1}: Low text, attempting OCR.")
        try:
            if tesseract_cmd and os.path.isfile(tesseract_cmd): pytesseract.tesseract_cmd = tesseract_cmd
            pix = page.get_pixmap(dpi=300); img_data = pix.tobytes("png"); img = Image.open(io.BytesIO(img_data))
            ocr_text = pytesseract.image_to_string(img, lang='eng')
            log_to_queue(worker_log_queue, "DEBUG", f"PDF Page {page_num+1}: OCR finished.")
            if len(ocr_text.strip()) > len(page_text.strip()) + 100: page_text = ocr_text
        except pytesseract.TesseractNotFoundError: log_to_queue(worker_log_queue, "ERROR", f"TESSERACT NOT FOUND. Skipping OCR for PDF page {page_num+1}.")
        except Exception as ocr_err: log_to_queue(worker_log_queue, "ERROR", f"OCR Error on PDF page {page_num+1}: {ocr_err}")
    return page_text.strip(), image_paths_on_page


# --- >> NEW: Function to process PPTX << ---
def _process_pptx_document(file_path, settings, worker_log_queue):
    """Extracts text and images from a PPTX file, slide by slide."""
    if not pptx:
        log_to_queue(worker_log_queue, "ERROR", "python-pptx library not installed. Cannot process PPTX.")
        return None # Indicate failure

    all_page_data = []
    base_name = sanitize_filename(file_path.stem)
    image_output_dir = Path(settings['chroma_path']) / settings['image_output_folder']

    try:
        presentation = pptx.Presentation(file_path)
        log_to_queue(worker_log_queue, "DEBUG", f"Processing PPTX: {file_path.name} ({len(presentation.slides)} slides)")

        for i, slide in enumerate(presentation.slides):
            slide_num = i + 1
            slide_text = []
            image_paths = []
            shape_img_index = 0

            # Extract text from shapes
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            slide_text.append(run.text)
                # Extract images
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    try:
                        image = shape.image
                        image_bytes = image.blob
                        image_ext = image.ext or 'png' # Default extension if unknown
                        img_filename = f"{base_name}_slide_{slide_num}_img_{shape_img_index}.{image_ext}"
                        save_path = image_output_dir / img_filename
                        with open(save_path, "wb") as img_file:
                            img_file.write(image_bytes)
                        image_paths.append(str(save_path))
                        shape_img_index += 1
                    except Exception as img_ex:
                        log_to_queue(worker_log_queue, "WARNING", f"PPTX Slide {slide_num}: Failed to extract/save image: {img_ex}")

            full_slide_text = " ".join(slide_text).strip()
            # Add data for this slide if it has text or images
            if full_slide_text or image_paths:
                 all_page_data.append({'page': slide_num, 'text': full_slide_text, 'image_paths': image_paths})

    except Exception as e:
        log_to_queue(worker_log_queue, "ERROR", f"Failed to process PPTX file {file_path.name}: {e}")
        log_to_queue(worker_log_queue, "DEBUG", traceback.format_exc())
        return None # Indicate failure

    return all_page_data

# --- >> NEW: Function to process DOCX << ---
def _process_docx_document(file_path, settings, worker_log_queue):
    """Extracts text and images from a DOCX file."""
    if not docx:
        log_to_queue(worker_log_queue, "ERROR", "python-docx library not installed. Cannot process DOCX.")
        return None # Indicate failure

    all_doc_text = []
    image_paths = []
    base_name = sanitize_filename(file_path.stem)
    image_output_dir = Path(settings['chroma_path']) / settings['image_output_folder']
    doc_img_index = 0

    try:
        document = docx.Document(file_path)
        log_to_queue(worker_log_queue, "DEBUG", f"Processing DOCX: {file_path.name}")

        # Extract text from paragraphs
        for para in document.paragraphs:
            all_doc_text.append(para.text)

        # Extract text from tables
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs: # Cells contain paragraphs
                        all_doc_text.append(para.text)

        # Extract images (primarily inline shapes)
        for shape in document.inline_shapes:
             # Check if it's a picture type (WD_INLINE_SHAPE_TYPE constants might be needed for more robust check)
             # A simpler check is to see if it links to an image part
             if hasattr(shape, '_inline') and 'graphicData' in shape._inline.graphic.tag:
                 # This is complex - need to navigate relationships to find the actual image part
                 # Let's try a simpler approach accessing image directly if available (might vary by python-docx version)
                 try:
                     # The exact way to get image blob might differ, requires inspection
                     # This is a common pattern but might fail
                     blip = shape._inline.graphic.graphicData.pic.blipFill.blip
                     rId = blip.embed
                     image_part = document.part.related_parts[rId]
                     image_bytes = image_part.blob
                     image_ext = image_part.ext or 'png'

                     img_filename = f"{base_name}_img_{doc_img_index}.{image_ext}"
                     save_path = image_output_dir / img_filename
                     with open(save_path, "wb") as img_file:
                         img_file.write(image_bytes)
                     image_paths.append(str(save_path))
                     doc_img_index += 1
                 except (AttributeError, KeyError, Exception) as img_ex:
                      log_to_queue(worker_log_queue, "WARNING", f"DOCX: Could not extract inline image {doc_img_index}: {img_ex}")


        full_doc_text = "\n".join(all_doc_text).strip() # Join paragraphs/table text

        # Return data as a single "page" for simplicity
        # Associate all found images with this single conceptual page
        if full_doc_text or image_paths:
             return [{'page': 1, 'text': full_doc_text, 'image_paths': image_paths}]
        else:
             return []

    except Exception as e:
        log_to_queue(worker_log_queue, "ERROR", f"Failed to process DOCX file {file_path.name}: {e}")
        log_to_queue(worker_log_queue, "DEBUG", traceback.format_exc())
        return None # Indicate failure


# --- >> RENAMED and MODIFIED main task function << ---
def process_file_task(file_path_str, settings):
    """
    Worker function dispatching based on file type. Handles PDF, PPTX, DOCX.
    Logs via settings['log_queue'].
    Returns tuple: (file_path_str, status_string, data_or_error)
    """
    worker_log_queue = settings.get('log_queue')
    if not worker_log_queue:
        print(f"CRITICAL ERROR: log_queue not found in settings for worker processing {file_path_str}")
        def log_to_queue_dummy(*args): pass
        global log_to_queue; log_to_queue = log_to_queue_dummy

    file_path = Path(file_path_str)
    file_name = file_path.name
    file_ext = file_path.suffix.lower()

    log_to_queue(worker_log_queue, "INFO", f"Worker received task: {file_name}")
    results = [] # Holds {'id': ..., 'text': ..., 'metadata': ...} dictionaries
    all_page_or_slide_data = None # Holds list of dicts [{'page': N, 'text': T, 'image_paths': [...]}, ...]

    try:
        # --- Dispatch based on file type ---
        if file_ext == '.pdf':
            all_page_or_slide_data = []
            doc = fitz.open(file_path)
            log_to_queue(worker_log_queue, "DEBUG", f"Processing PDF: {file_name} ({len(doc)} pages)")
            for page_num in range(len(doc)):
                page_text, image_paths = _process_pdf_page(doc[page_num], page_num, file_path_str, settings, worker_log_queue)
                if page_text or image_paths:
                    all_page_or_slide_data.append({'page': page_num + 1, 'text': page_text, 'image_paths': image_paths})
            doc.close()

        elif file_ext == '.pptx':
            all_page_or_slide_data = _process_pptx_document(file_path, settings, worker_log_queue)

        elif file_ext == '.docx':
            all_page_or_slide_data = _process_docx_document(file_path, settings, worker_log_queue)

        else:
            log_to_queue(worker_log_queue, "WARNING", f"Unsupported file type skipped: {file_name}")
            return file_path_str, "skipped", "Unsupported file type"
        # --- End Dispatch ---

        # Check if processing failed in helpers
        if all_page_or_slide_data is None:
             raise ValueError(f"Processing function failed for {file_name}")

        # --- Chunking and Metadata Generation (Common Logic) ---
        log_to_queue(worker_log_queue, "DEBUG", f"Chunking text for {file_name}...")
        chunk_count = 0
        for page_data in all_page_or_slide_data:
            page_num = page_data["page"] # Page or Slide number
            page_text = page_data["text"]
            image_paths_for_metadata = page_data["image_paths"]

            page_chunks = simple_chunker(page_text, settings['chunk_size'], settings['chunk_overlap'])

            # Handle pages/slides with only images
            if not page_chunks and image_paths_for_metadata:
                 placeholder_text = f"Images present on page/slide {page_num}."
                 page_chunks = [placeholder_text]
                 log_to_queue(worker_log_queue, "WARNING", f"{file_name} Page/Slide {page_num}: No text found, using placeholder.")

            for i, chunk in enumerate(page_chunks):
                metadata = {
                    'source': file_name, # Store filename
                    'page': page_num,    # Store page or slide number (or 1 for DOCX)
                    'chunk_in_page': i + 1,
                    'image_paths': image_paths_for_metadata # Store original list here for processing in manager
                }
                doc_id = f"{sanitize_filename(file_path.stem)}_page_{page_num}_chunk_{i}"
                results.append({'id': doc_id, 'text': chunk, 'metadata': metadata})
                chunk_count += 1
        # --- End Chunking ---

        log_msg_end = f"Worker finished: {file_name} - Found {chunk_count} chunks."
        if not results and not all_page_or_slide_data: # Check if nothing was processed at all
             log_to_queue(worker_log_queue, "WARNING", f"Worker finished: {file_name} - No content extracted.")
        elif not results: # Check if content was extracted but no chunks generated (e.g., only empty text)
             log_to_queue(worker_log_queue, "WARNING", f"Worker finished: {file_name} - Extracted content yielded no text chunks.")
        else:
             log_to_queue(worker_log_queue, "SUCCESS", log_msg_end)

        return file_path_str, "success", results

    except Exception as e:
        tb_str = traceback.format_exc()
        log_to_queue(worker_log_queue, "ERROR", f"Worker CRASHED on {file_name}: {e}\nTraceback:\n{tb_str}")
        return file_path_str, "error", str(e)
# --- >> END RENAMED function << ---