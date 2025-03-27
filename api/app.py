from flask import Flask, request, jsonify, send_file, render_template
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

# Configure upload folder
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'outputs')

# Ensure upload and output folders exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Configure allowed file extensions
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov'}

# File management settings
MAX_FILE_AGE_HOURS = 24  # Files older than this will be deleted
MAX_CONCURRENT_PROCESSES = 1  # Reduced for free tier
MAX_FILE_SIZE_MB = 100  # Reduced for free tier
MAX_QUEUE_SIZE = 3  # Reduced for free tier
MAX_RECENT_JOBS = 5  # Reduced for free tier

# Global processing lock and queue
processing_lock = threading.Lock()
active_processes = 0
job_queue = Queue(maxsize=MAX_QUEUE_SIZE)
processing_jobs: Dict[str, Dict] = {}  # Store job status and details
recent_jobs: List[Dict] = []  # Store recent completed/failed jobs

class VideoProcessingError(Exception):
    """Custom exception for video processing errors"""
    pass

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

def check_file_size(file):
    """Check if file size is within limits"""
    file.seek(0, 2)  # Seek to end of file
    size = file.tell() / (1024 * 1024)  # Convert to MB
    file.seek(0)  # Reset file pointer
    return size <= MAX_FILE_SIZE_MB

def update_job_progress(job_id: str, progress: float, stage: str):
    """Update the progress of a job"""
    if job_id in processing_jobs:
        processing_jobs[job_id]['progress'] = progress
        processing_jobs[job_id]['stage'] = stage

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
            raise VideoProcessingError(message)
            
    except Exception as e:
        processing_jobs[job_id].update({
            'status': 'failed',
            'end_time': datetime.now().isoformat(),
            'error': str(e),
            'traceback': traceback.format_exc(),
            'progress': 0,
            'stage': 'failed',
            'message': f'Processing failed: {str(e)}'
        })
        logging.error(f"Error processing job {job_id}: {str(e)}")
        
        # Add to recent jobs
        with processing_lock:
            recent_jobs.insert(0, processing_jobs[job_id].copy())
            if len(recent_jobs) > MAX_RECENT_JOBS:
                recent_jobs.pop()
        raise
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

@app.route('/')
def dashboard():
    """Render the dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    """Get active and recent jobs"""
    try:
        with processing_lock:
            active_jobs = []
            for job_id, job in processing_jobs.items():
                if job['status'] in ['queued', 'processing']:
                    job_info = {
                        'id': job_id,
                        'status': job['status'],
                        'created_at': job['created_at'],
                        'start_time': job.get('start_time'),
                        'progress': job.get('progress', 0),
                        'stage': job.get('stage', 'queued'),
                        'message': job.get('message', ''),
                        'error': job.get('error', '')
                    }
                    active_jobs.append(job_info)
            
            recent_job_details = []
            for job in recent_jobs:
                job_info = {
                    'id': job.get('id'),
                    'status': job['status'],
                    'created_at': job['created_at'],
                    'start_time': job.get('start_time'),
                    'end_time': job.get('end_time'),
                    'progress': job.get('progress', 0),
                    'stage': job.get('stage', 'completed'),
                    'message': job.get('message', ''),
                    'error': job.get('error', '')
                }
                recent_job_details.append(job_info)
            
            return jsonify({
                'success': True,
                'active_jobs': active_jobs,
                'recent_jobs': recent_job_details,
                'server_time': datetime.now().isoformat()
            })
    except Exception as e:
        logging.error(f"Error getting jobs: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error getting jobs: {str(e)}'
        }), 500

@app.route('/api/stitch', methods=['POST'])
def stitch_videos():
    try:
        # Check if files are present in the request
        if 'wwe_video' not in request.files or 'fan_video' not in request.files:
            return jsonify({
                'success': False,
                'message': 'Both WWE video and fan video are required'
            }), 400

        wwe_video = request.files['wwe_video']
        fan_video = request.files['fan_video']

        # Check if files are valid
        if not (wwe_video and fan_video):
            return jsonify({
                'success': False,
                'message': 'No selected files'
            }), 400

        if not (allowed_file(wwe_video.filename) and allowed_file(fan_video.filename)):
            return jsonify({
                'success': False,
                'message': 'Invalid file type. Allowed types: mp4, avi, mov'
            }), 400

        # Check file sizes
        if not (check_file_size(wwe_video) and check_file_size(fan_video)):
            return jsonify({
                'success': False,
                'message': f'File size exceeds {MAX_FILE_SIZE_MB}MB limit'
            }), 400

        # Generate unique filenames and job ID
        job_id = str(uuid.uuid4())
        unique_id = str(uuid.uuid4())
        wwe_filename = secure_filename(f"wwe_{unique_id}_{wwe_video.filename}")
        fan_filename = secure_filename(f"fan_{unique_id}_{fan_video.filename}")
        output_filename = f"output_{unique_id}.mp4"

        # Save uploaded files
        wwe_path = os.path.join(UPLOAD_FOLDER, wwe_filename)
        fan_path = os.path.join(UPLOAD_FOLDER, fan_filename)
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)

        wwe_video.save(wwe_path)
        fan_video.save(fan_path)

        # Check queue status
        if job_queue.full():
            return jsonify({
                'success': False,
                'message': 'Processing queue is full. Please try again later.'
            }), 503

        # Initialize job status with more details
        processing_jobs[job_id] = {
            'id': job_id,
            'status': 'queued',
            'created_at': datetime.now().isoformat(),
            'output_file': output_filename,
            'progress': 0,
            'stage': 'queued',
            'message': 'Job queued for processing'
        }

        # Add job to queue
        job_queue.put((job_id, wwe_path, fan_path, output_path))

        return jsonify({
            'success': True,
            'message': 'Video processing job queued successfully',
            'job_id': job_id,
            'output_file': output_filename
        })

    except Exception as e:
        logging.error(f"Error in stitch_videos endpoint: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error processing request: {str(e)}'
        }), 500

@app.route('/api/job/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """Get the status of a specific job"""
    try:
        if job_id not in processing_jobs:
            return jsonify({
                'success': False,
                'message': 'Job not found'
            }), 404

        job_info = processing_jobs[job_id]
        return jsonify({
            'success': True,
            'job_id': job_id,
            'status': job_info['status'],
            'created_at': job_info['created_at'],
            'output_file': job_info.get('output_file'),
            'message': job_info.get('message'),
            'error': job_info.get('error')
        })

    except Exception as e:
        logging.error(f"Error getting job status: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error getting job status: {str(e)}'
        }), 500

@app.route('/api/download/<filename>', methods=['GET'])
def download_video(filename):
    try:
        file_path = os.path.join(OUTPUT_FOLDER, filename)
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'message': 'File not found'
            }), 404
            
        return send_file(
            file_path,
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logging.error(f"Error downloading file: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error downloading file: {str(e)}'
        }), 404

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get current server status and processing queue"""
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
        logging.error(f"Error getting server status: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error getting status: {str(e)}'
        }), 500

@app.route('/api/job/<job_id>/start', methods=['POST'])
def start_job(job_id):
    """Start a specific job"""
    try:
        if job_id not in processing_jobs:
            return jsonify({
                'success': False,
                'message': 'Job not found'
            }), 404

        job = processing_jobs[job_id]
        if job['status'] != 'queued':
            return jsonify({
                'success': False,
                'message': 'Job is not in queued state'
            }), 400

        # Start processing the job
        wwe_path = os.path.join(UPLOAD_FOLDER, f"wwe_{job_id}.mp4")
        fan_path = os.path.join(UPLOAD_FOLDER, f"fan_{job_id}.mp4")
        output_path = os.path.join(OUTPUT_FOLDER, f"output_{job_id}.mp4")

        if not (os.path.exists(wwe_path) and os.path.exists(fan_path)):
            return jsonify({
                'success': False,
                'message': 'Video files not found'
            }), 400

        # Add job to processing queue
        job_queue.put((job_id, wwe_path, fan_path, output_path))

        return jsonify({
            'success': True,
            'message': 'Job started successfully'
        })

    except Exception as e:
        logging.error(f"Error starting job {job_id}: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error starting job: {str(e)}'
        }), 500

@app.route('/api/job/<job_id>/stop', methods=['POST'])
def stop_job(job_id):
    """Stop a specific job"""
    try:
        if job_id not in processing_jobs:
            return jsonify({
                'success': False,
                'message': 'Job not found'
            }), 404

        job = processing_jobs[job_id]
        if job['status'] not in ['queued', 'processing']:
            return jsonify({
                'success': False,
                'message': 'Job is not active'
            }), 400

        # Mark job as failed
        job.update({
            'status': 'failed',
            'end_time': datetime.now().isoformat(),
            'error': 'Job stopped by user',
            'message': 'Processing stopped by user request'
        })

        # Clean up any temporary files
        try:
            wwe_path = os.path.join(UPLOAD_FOLDER, f"wwe_{job_id}.mp4")
            fan_path = os.path.join(UPLOAD_FOLDER, f"fan_{job_id}.mp4")
            output_path = os.path.join(OUTPUT_FOLDER, f"output_{job_id}.mp4")
            
            for path in [wwe_path, fan_path, output_path]:
                if os.path.exists(path):
                    os.remove(path)
        except Exception as e:
            logging.error(f"Error cleaning up files for job {job_id}: {str(e)}")

        return jsonify({
            'success': True,
            'message': 'Job stopped successfully'
        })

    except Exception as e:
        logging.error(f"Error stopping job {job_id}: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error stopping job: {str(e)}'
        }), 500

@app.route('/api/jobs/stop-all', methods=['POST'])
def stop_all_jobs():
    """Stop all active jobs"""
    try:
        stopped_count = 0
        with processing_lock:
            for job_id, job in processing_jobs.items():
                if job['status'] in ['queued', 'processing']:
                    job.update({
                        'status': 'failed',
                        'end_time': datetime.now().isoformat(),
                        'error': 'Job stopped by user',
                        'message': 'Processing stopped by user request'
                    })
                    stopped_count += 1

                    # Clean up any temporary files
                    try:
                        wwe_path = os.path.join(UPLOAD_FOLDER, f"wwe_{job_id}.mp4")
                        fan_path = os.path.join(UPLOAD_FOLDER, f"fan_{job_id}.mp4")
                        output_path = os.path.join(OUTPUT_FOLDER, f"output_{job_id}.mp4")
                        
                        for path in [wwe_path, fan_path, output_path]:
                            if os.path.exists(path):
                                os.remove(path)
                    except Exception as e:
                        logging.error(f"Error cleaning up files for job {job_id}: {str(e)}")

        return jsonify({
            'success': True,
            'message': f'Stopped {stopped_count} jobs successfully'
        })

    except Exception as e:
        logging.error(f"Error stopping all jobs: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error stopping jobs: {str(e)}'
        }), 500

# Schedule cleanup task
def schedule_cleanup():
    while True:
        cleanup_old_files()
        time.sleep(3600)  # Run cleanup every hour

if __name__ == '__main__':
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=schedule_cleanup, daemon=True)
    cleanup_thread.start()
    
    # Start queue processing threads
    for _ in range(MAX_CONCURRENT_PROCESSES):
        queue_thread = threading.Thread(target=process_queue, daemon=True)
        queue_thread.start()
    
    # Get port from environment variable or use default
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port) 