from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for, session
from werkzeug.utils import secure_filename
import os
from core.video_stitcher import VideoStitcher
import uuid
import time
import threading
from datetime import datetime, timedelta
from queue import Queue
import logging
from typing import Dict, Optional, List
import traceback
from threading import Thread
import pickle

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('video_stitcher.log'),
        logging.StreamHandler()
    ]
)

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')  # Required for session

# Configuration
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
MAX_FILE_SIZE_MB = 100
MAX_FILE_AGE_HOURS = 24
MAX_CONCURRENT_PROCESSES = 2
MAX_QUEUE_SIZE = 10
MAX_RECENT_JOBS = 50

# Create necessary directories
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Global state
processing_jobs: Dict[str, dict] = {}
job_queue = Queue()
active_processes = 0
processing_lock = threading.Lock()
recent_jobs = []

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'mp4', 'avi', 'mov'}

def check_file_size(file):
    file.seek(0, os.SEEK_END)
    size = file.tell() / (1024 * 1024)  # Convert to MB
    file.seek(0)
    return size <= MAX_FILE_SIZE_MB

def update_job_progress(job_id: str, progress: float, stage: str):
    """Update job progress and stage"""
    if job_id in processing_jobs:
        processing_jobs[job_id].update({
            'progress': progress,
            'stage': stage
        })

def cleanup_old_files():
    """Clean up files older than MAX_FILE_AGE_HOURS"""
    try:
        current_time = datetime.now()
        for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
            for filename in os.listdir(folder):
                filepath = os.path.join(folder, filename)
                file_modified = datetime.fromtimestamp(os.path.getmtime(filepath))
                if current_time - file_modified > timedelta(hours=MAX_FILE_AGE_HOURS):
                    try:
                        os.remove(filepath)
                        logging.info(f"Cleaned up old file: {filename}")
                    except Exception as e:
                        logging.error(f"Error cleaning up file {filename}: {str(e)}")
    except Exception as e:
        logging.error(f"Error during cleanup: {str(e)}")

def process_video_job(job_id: str, wwe_path: str, fan_path: str, output_path: str):
    """Process a video job"""
    global active_processes
    
    try:
        with processing_lock:
            active_processes += 1
        
        # Update job status with more details
        processing_jobs[job_id].update({
            'id': job_id,
            'status': 'processing',
            'start_time': datetime.now().isoformat(),
            'progress': 0,
            'stage': 'initializing',
            'message': 'Starting video processing...'
        })
        
        # Process videos
        stitcher = VideoStitcher(wwe_path, fan_path)
        update_job_progress(job_id, 0.1, 'loading_videos')
        success, message = stitcher.stitch_videos(output_path, progress_callback=lambda p, s: update_job_progress(job_id, p, s))
        
        if success:
            processing_jobs[job_id].update({
                'status': 'completed',
                'end_time': datetime.now().isoformat(),
                'message': message,
                'progress': 1.0,
                'stage': 'completed'
            })
            # Clean up uploaded files
            os.remove(wwe_path)
            os.remove(fan_path)
            
            # Add to recent jobs
            with processing_lock:
                recent_jobs.insert(0, processing_jobs[job_id].copy())
                if len(recent_jobs) > MAX_RECENT_JOBS:
                    recent_jobs.pop()
        else:
            raise Exception(message)
            
    except Exception as e:
        processing_jobs[job_id].update({
            'status': 'failed',
            'error': str(e),
            'stage': 'failed',
            'progress': 0
        })
        logging.error(f"Error processing job {job_id}: {str(e)}")
    finally:
        with processing_lock:
            active_processes -= 1

def process_queue():
    """Process jobs from the queue"""
    while True:
        try:
            job = job_queue.get()
            job_id, wwe_path, fan_path, output_path = job
            
            try:
                process_video_job(job_id, wwe_path, fan_path, output_path)
            except Exception as e:
                logging.error(f"Failed to process job {job_id}: {str(e)}")
            finally:
                job_queue.task_done()
                
        except Exception as e:
            logging.error(f"Error in queue processor: {str(e)}")
            time.sleep(1)  # Prevent tight loop on errors

@app.route('/api/stitch', methods=['POST'])
def stitch_videos():
    try:
        if 'wwe_video' not in request.files or 'fan_video' not in request.files:
            return jsonify({'success': False, 'message': 'Both WWE and fan videos are required'})

        wwe_video = request.files['wwe_video']
        fan_video = request.files['fan_video']

        if wwe_video.filename == '' or fan_video.filename == '':
            return jsonify({'success': False, 'message': 'No selected files'})

        if not allowed_file(wwe_video.filename) or not allowed_file(fan_video.filename):
            return jsonify({'success': False, 'message': 'Invalid file type. Only MP4, AVI, and MOV files are allowed.'})

        if not check_file_size(wwe_video) or not check_file_size(fan_video):
            return jsonify({'success': False, 'message': f'File size exceeds {MAX_FILE_SIZE_MB}MB limit'})

        # Generate a unique job ID
        job_id = str(uuid.uuid4())
        
        # Create job directory
        job_dir = os.path.join(UPLOAD_FOLDER, job_id)
        os.makedirs(job_dir, exist_ok=True)

        # Save uploaded files with original names
        wwe_path = os.path.join(job_dir, secure_filename(wwe_video.filename))
        fan_path = os.path.join(job_dir, secure_filename(fan_video.filename))
        
        wwe_video.save(wwe_path)
        fan_video.save(fan_path)

        # Add job to queue
        job_data = {
            'id': job_id,
            'wwe_video': wwe_path,
            'fan_video': fan_path,
            'status': 'queued',
            'stage': 'queued',
            'progress': 0,
            'created_at': datetime.now().isoformat(),
            'original_wwe_filename': wwe_video.filename,
            'original_fan_filename': fan_video.filename
        }
        
        processing_jobs[job_id] = job_data
        return jsonify({
            'success': True,
            'message': 'Videos uploaded successfully',
            'job_id': job_id
        })

    except Exception as e:
        logging.error(f"Error in stitch_videos: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/job/<job_id>', methods=['GET'])
def get_job_status(job_id):
    try:
        if job_id not in processing_jobs:
            return jsonify({'success': False, 'message': 'Job not found'}), 404

        job = processing_jobs[job_id]
        return jsonify({
            'success': True,
            'job': job
        })

    except Exception as e:
        logging.error(f"Error in get_job_status: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/job/<job_id>/start', methods=['POST'])
def start_job(job_id):
    try:
        if job_id not in processing_jobs:
            return jsonify({'success': False, 'message': 'Job not found'})

        job = processing_jobs[job_id]
        if not os.path.exists(job['wwe_video']) or not os.path.exists(job['fan_video']):
            return jsonify({'success': False, 'message': 'Video files not found. They may have been cleaned up or deleted.'})

        # Update job status
        job['status'] = 'processing'
        job['stage'] = 'initializing'
        job['progress'] = 0

        # Add to processing queue
        output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}.mp4")
        job_queue.put((job_id, job['wwe_video'], job['fan_video'], output_path))

        return jsonify({'success': True, 'message': 'Job added to processing queue'})

    except Exception as e:
        logging.error(f"Error in start_job: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/job/<job_id>/stop', methods=['POST'])
def stop_job(job_id):
    try:
        if job_id not in processing_jobs:
            return jsonify({'success': False, 'message': 'Job not found'})

        job = processing_jobs[job_id]
        job['status'] = 'stopped'
        job['stage'] = 'stopped'
        
        return jsonify({'success': True, 'message': 'Job stopped'})

    except Exception as e:
        logging.error(f"Error in stop_job: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/job/<job_id>/pause', methods=['POST'])
def pause_job(job_id):
    try:
        if job_id not in processing_jobs:
            return jsonify({'success': False, 'message': 'Job not found'})

        job = processing_jobs[job_id]
        job['status'] = 'paused'
        job['stage'] = 'paused'
        
        return jsonify({'success': True, 'message': 'Job paused'})

    except Exception as e:
        logging.error(f"Error in pause_job: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/job/<job_id>/resume', methods=['POST'])
def resume_job(job_id):
    try:
        if job_id not in processing_jobs:
            return jsonify({'success': False, 'message': 'Job not found'})

        job = processing_jobs[job_id]
        job['status'] = 'queued'
        job['stage'] = 'queued'
        
        # Add to processing queue
        output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}.mp4")
        job_queue.put((job_id, job['wwe_video'], job['fan_video'], output_path))

        return jsonify({'success': True, 'message': 'Job resumed'})

    except Exception as e:
        logging.error(f"Error in resume_job: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/jobs/start_all', methods=['POST'])
def start_all_jobs():
    try:
        started_count = 0
        for job_id in processing_jobs:
            if processing_jobs[job_id]['status'] in ['queued', 'paused', 'stopped']:
                response = start_job(job_id)
                if response.json['success']:
                    started_count += 1

        return jsonify({
            'success': True,
            'message': f'Started {started_count} jobs'
        })

    except Exception as e:
        logging.error(f"Error in start_all_jobs: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/jobs/stop_all', methods=['POST'])
def stop_all_jobs():
    try:
        stopped_count = 0
        for job_id in processing_jobs:
            if processing_jobs[job_id]['status'] in ['processing', 'queued']:
                response = stop_job(job_id)
                if response.json['success']:
                    stopped_count += 1

        return jsonify({
            'success': True,
            'message': f'Stopped {stopped_count} jobs'
        })

    except Exception as e:
        logging.error(f"Error in stop_all_jobs: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/jobs/delete_all', methods=['POST'])
def delete_all_outputs():
    try:
        deleted_count = 0
        for filename in os.listdir(OUTPUT_FOLDER):
            if filename.endswith('.mp4'):
                try:
                    os.remove(os.path.join(OUTPUT_FOLDER, filename))
                    deleted_count += 1
                except Exception as e:
                    logging.error(f"Error deleting file {filename}: {str(e)}")

        return jsonify({
            'success': True,
            'message': f'Deleted {deleted_count} output files'
        })

    except Exception as e:
        logging.error(f"Error in delete_all_outputs: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/jobs/download_all', methods=['GET'])
def download_all_outputs():
    try:
        # Create a zip file containing all output videos
        import zipfile
        from io import BytesIO
        
        memory_file = BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for filename in os.listdir(OUTPUT_FOLDER):
                if filename.endswith('.mp4'):
                    file_path = os.path.join(OUTPUT_FOLDER, filename)
                    zf.write(file_path, filename)
        
        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='all_outputs.zip'
        )

    except Exception as e:
        logging.error(f"Error in download_all_outputs: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/download/<job_id>', methods=['GET'])
def download_video(job_id):
    try:
        if job_id not in processing_jobs:
            return jsonify({'success': False, 'message': 'Job not found'}), 404

        job = processing_jobs[job_id]
        if job['status'] != 'completed':
            return jsonify({'success': False, 'message': 'Video is not ready for download'}), 400

        output_filename = job.get('output_file')
        if not output_filename:
            return jsonify({'success': False, 'message': 'Output file not found'}), 404

        file_path = os.path.join(OUTPUT_FOLDER, output_filename)
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': 'Output file does not exist'}), 404
            
        return send_file(
            file_path,
            as_attachment=True,
            download_name=f"stitched_video_{job_id}.mp4"
        )

    except Exception as e:
        logging.error(f"Error in download_video: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    try:
        with processing_lock:
            return jsonify({
                'success': True,
                'active_processes': active_processes,
                'max_concurrent_processes': MAX_CONCURRENT_PROCESSES,
                'queue_size': job_queue.qsize(),
                'max_queue_size': MAX_QUEUE_SIZE,
                'server_status': 'busy' if active_processes >= MAX_CONCURRENT_PROCESSES else 'available'
            })
    except Exception as e:
        logging.error(f"Error in get_status: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

# Schedule cleanup task
def schedule_cleanup():
    while True:
        cleanup_old_files()
        time.sleep(3600)  # Run cleanup every hour

if __name__ == '__main__':
    # Start cleanup thread
    cleanup_thread = Thread(target=schedule_cleanup, daemon=True)
    cleanup_thread.start()
    
    # Start queue processing threads
    for _ in range(MAX_CONCURRENT_PROCESSES):
        queue_thread = threading.Thread(target=process_queue, daemon=True)
        queue_thread.start()
    
    # Get port from environment variable or use default
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port) 