# minimal_mp_test.py
import sys
import multiprocessing
import time
import os
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget
from PySide6.QtCore import QThread, Signal, Slot # <<< --- ADDED Slot HERE --- <<<
import queue # For queue.Empty exception with multiprocessing.Manager().Queue()

def simple_worker_func(q_out):
    # This function runs in the separate process
    worker_pid = os.getpid()
    print(f"SIMPLE WORKER [{worker_pid}] ALIVE AND WELL")
    time.sleep(2) # Simulate some work
    try:
        q_out.put("DONE from worker")
        print(f"SIMPLE WORKER [{worker_pid}] FINISHED and put result on queue.")
    except Exception as e:
        print(f"SIMPLE WORKER [{worker_pid}] ERROR putting on queue: {e}")


class TestThread(QThread):
    finished_signal = Signal(str) # Signal to send result/error back to main window

    def run(self):
        thread_pid = os.getpid() # PID of the QThread itself (same as main process)
        print(f"TestThread [{thread_pid}]: Starting simple process...")
        # Use a manager for the queue to ensure it works across processes
        with multiprocessing.Manager() as manager:
            output_q = manager.Queue()
            # Create the separate process
            process_instance = multiprocessing.Process(target=simple_worker_func, args=(output_q,), daemon=True)

            try:
                process_instance.start()
                print(f"TestThread [{thread_pid}]: Process started (Child PID: {process_instance.pid}). Waiting for result...")

                # Wait for result from the queue with a timeout
                try:
                    result = output_q.get(timeout=10) # Wait up to 10 seconds
                    self.finished_signal.emit(f"Process result: {result}")
                except queue.Empty: # Correct exception for queue timeout
                    self.finished_signal.emit("Process error: Timed out waiting for result from worker.")
                except Exception as e_q:
                    self.finished_signal.emit(f"Process error: Exception getting from queue: {e_q}")

            except Exception as e_start:
                # This would catch errors in process_instance.start() itself
                self.finished_signal.emit(f"Process error: Failed to start process: {e_start}")
            finally:
                # Ensure the process is cleaned up
                if process_instance.is_alive():
                    print(f"TestThread [{thread_pid}]: Process still alive, terminating.")
                    process_instance.terminate() # Ask nicely first
                    process_instance.join(timeout=2) # Wait for termination
                if process_instance.is_alive():
                    print(f"TestThread [{thread_pid}]: Process did not terminate, killing.")
                    process_instance.kill() # Force kill
                    process_instance.join(timeout=1)
                print(f"TestThread [{thread_pid}]: Process joined/cleaned (exitcode: {process_instance.exitcode}).")


class MinimalApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minimal Multiprocessing Test")
        self.button = QPushButton("Start Process Test")
        self.button.clicked.connect(self.do_test)
        self.current_thread = None # Keep a reference to the thread

        layout = QVBoxLayout()
        layout.addWidget(self.button)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.resize(300, 100)

    @Slot() # Decorate the slot
    def do_test(self):
        self.button.setEnabled(False)
        app_pid = os.getpid()
        print(f"MinimalApp [{app_pid}]: Button clicked, starting TestThread.")
        # Create a new thread instance each time for a clean test
        self.current_thread = TestThread()
        self.current_thread.finished_signal.connect(self.on_test_done)
        # Clean up thread object when it finishes its Qt event loop
        self.current_thread.finished.connect(self.on_qthread_actually_finished)
        self.current_thread.start()

    @Slot(str) # Decorate the slot
    def on_test_done(self, message):
        app_pid = os.getpid()
        print(f"MinimalApp [{app_pid}]: TestThread reported back: {message}")
        # Re-enable button after the message is received, not necessarily when QThread finishes
        # self.button.setEnabled(True) # Moved to on_qthread_actually_finished

    @Slot() # Decorate the slot
    def on_qthread_actually_finished(self):
        """Called when the QThread object has finished its run() method and event processing."""
        print(f"MinimalApp [{os.getpid()}]: QThread object has finished.")
        self.button.setEnabled(True) # Re-enable button now
        if self.current_thread: # Check if it exists before trying to delete
            self.current_thread.deleteLater() # Schedule for deletion
            self.current_thread = None # Clear reference


if __name__ == "__main__":
    # This must be the first executable line in the main entry point
    multiprocessing.freeze_support()
    try:
        # Set start method to 'spawn' which is generally safer for GUI/frozen apps on Windows
        multiprocessing.set_start_method('spawn', force=True)
        print("[Main Entry] Multiprocessing start method set to 'spawn'.")
    except RuntimeError:
        # If it's already set (e.g., by a library, though unlikely if this is first)
        print("[Main Entry] Multiprocessing start method was already set or context used.")
        pass # Continue if it's already set

    app = QApplication(sys.argv)
    window = MinimalApp()
    window.show()
    sys.exit(app.exec())