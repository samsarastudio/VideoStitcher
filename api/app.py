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
import cv2
import subprocess

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('video_stitcher.log'),
        logging.StreamHandler()
    ]
)

app = Flask(__name__, 
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'),
    static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')  # Required for session

# Configuration
# Use Render's temporary directory for file storage
RENDER_TEMP_DIR = os.getenv('RENDER_TEMP_DIR', '/tmp')
UPLOAD_FOLDER = os.path.join(RENDER_TEMP_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(RENDER_TEMP_DIR, 'outputs')

# Get the root directory (where the app is running)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WWE_VIDEO = os.getenv('DEFAULT_WWE_VIDEO', os.path.join(ROOT_DIR, 'wwe_video.mp4'))

MAX_FILE_SIZE_MB = 100
MAX_FILE_AGE_HOURS = 24
MAX_CONCURRENT_PROCESSES = 2
MAX_QUEUE_SIZE = 10
MAX_RECENT_JOBS = 50

# Create necessary directories
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Log directory paths for debugging
logging.info(f"Root directory: {ROOT_DIR}")
logging.info(f"Upload folder: {UPLOAD_FOLDER}")
logging.info(f"Output folder: {OUTPUT_FOLDER}")
logging.info(f"Default WWE video path: {DEFAULT_WWE_VIDEO}")

# Check if default WWE video exists and is valid
if os.path.exists(DEFAULT_WWE_VIDEO):
    size_mb = os.path.getsize(DEFAULT_WWE_VIDEO) / (1024 * 1024)
    logging.info(f"Default WWE video found: {DEFAULT_WWE_VIDEO} ({size_mb:.2f} MB)")
    
    # Validate the video file
    cap = cv2.VideoCapture(DEFAULT_WWE_VIDEO)
    if not cap.isOpened():
        logging.error(f"Default WWE video is not a valid video file: {DEFAULT_WWE_VIDEO}")
    else:
        logging.info(f"Default WWE video is valid (FPS: {cap.get(cv2.CAP_PROP_FPS)})")
    cap.release()
else:
    logging.error(f"Default WWE video not found at: {DEFAULT_WWE_VIDEO}")

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
        
        logging.info(f"Starting job {job_id} with WWE video: {wwe_path} and fan video: {fan_path}")
        
        # Validate input videos
        for video_path in [wwe_path, fan_path]:
            if not os.path.exists(video_path):
                raise Exception(f"Video file not found: {video_path}")
            
            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            logging.info(f"Video file size: {video_path} ({size_mb:.2f} MB)")
            
            if size_mb < 0.01:  # Less than 10KB
                raise Exception(f"Video file too small: {video_path} ({size_mb:.2f} MB)")
            
            # Check if file is a valid video
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise Exception(f"Invalid video file: {video_path}")
            cap.release()
        
        # Process videos
        stitcher = VideoStitcher(wwe_path, fan_path)
        update_job_progress(job_id, 0.1, 'loading_videos')
        
        # Add error handling for video stitching
        try:
            logging.info(f"Starting video stitching for job {job_id}")
            success, message = stitcher.stitch_videos(output_path, progress_callback=lambda p, s: update_job_progress(job_id, p, s))
            logging.info(f"Video stitching completed for job {job_id}: success={success}, message={message}")
        except Exception as e:
            logging.error(f"Error during video stitching for job {job_id}: {str(e)}")
            raise Exception(f"Error during video stitching: {str(e)}")
        
        if success:
            # Validate output video
            if not os.path.exists(output_path):
                raise Exception("Output video not created")
            
            output_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logging.info(f"Output video size: {output_size_mb:.2f} MB")
            
            # Validate output video is playable
            cap = cv2.VideoCapture(output_path)
            if not cap.isOpened():
                raise Exception("Generated video is not valid")
            cap.release()
            
            # Update job status with output file info
            processing_jobs[job_id].update({
                'status': 'completed',
                'end_time': datetime.now().isoformat(),
                'message': message,
                'progress': 1.0,
                'stage': 'completed',
                'output_path': output_path,
                'output_size': output_size_mb,
                'output_filename': os.path.basename(output_path)
            })
            
            logging.info(f"Job {job_id} completed successfully")
            
            # Add to recent jobs
            with processing_lock:
                recent_jobs.insert(0, processing_jobs[job_id].copy())
                if len(recent_jobs) > MAX_RECENT_JOBS:
                    recent_jobs.pop()
            
            # Clean up uploaded files
            try:
                os.remove(wwe_path)
                os.remove(fan_path)
            except Exception as e:
                logging.warning(f"Error cleaning up files: {str(e)}")
        else:
            raise Exception(message)
            
    except Exception as e:
        logging.error(f"Error processing job {job_id}: {str(e)}")
        processing_jobs[job_id].update({
            'status': 'failed',
            'error': str(e),
            'stage': 'failed',
            'progress': 0
        })
        
        # Clean up any partial output file
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except Exception as cleanup_error:
            logging.error(f"Error cleaning up failed output: {str(cleanup_error)}")
    finally:
        with processing_lock:
            active_processes -= 1
            logging.info(f"Job {job_id} processing finished. Active processes: {active_processes}")

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
                if job['status'] in ['queued', 'processing', 'paused']:
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
                    'status': job.get('status'),
                    'created_at': job.get('created_at'),
                    'start_time': job.get('start_time'),
                    'end_time': job.get('end_time'),
                    'progress': job.get('progress', 0),
                    'stage': job.get('stage', 'completed'),
                    'message': job.get('message', ''),
                    'error': job.get('error', ''),
                    'output_size': job.get('output_size', 0)
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

@app.route('/api/stitch_single', methods=['POST'])
def stitch_single_video():
    """Process a single fan video with the default WWE video"""
    try:
        logging.info("Received single video upload request")
        
        if 'fan_video' not in request.files:
            logging.error("No fan_video in request files")
            return jsonify({'success': False, 'message': 'Fan video is required'}), 400

        fan_video = request.files['fan_video']
        logging.info(f"Received file: {fan_video.filename}")

        if fan_video.filename == '':
            logging.error("Empty filename")
            return jsonify({'success': False, 'message': 'No selected file'}), 400

        if not allowed_file(fan_video.filename):
            logging.error(f"Invalid file type: {fan_video.filename}")
            return jsonify({'success': False, 'message': 'Invalid file type. Only MP4, AVI, and MOV files are allowed.'}), 400

        # Check file size
        if not check_file_size(fan_video):
            logging.error(f"File too large: {fan_video.filename}")
            return jsonify({'success': False, 'message': f'File size exceeds {MAX_FILE_SIZE_MB}MB limit'}), 400

        # Validate the uploaded video
        is_valid, message = validate_uploaded_video(fan_video)
        if not is_valid:
            logging.error(f"Video validation failed: {message}")
            return jsonify({'success': False, 'message': message}), 400

        if not os.path.exists(DEFAULT_WWE_VIDEO):
            logging.error(f"Default WWE video not found at: {DEFAULT_WWE_VIDEO}")
            return jsonify({'success': False, 'message': 'Default WWE video not found'}), 404

        # Validate the default WWE video
        is_valid, message = validate_video_file(DEFAULT_WWE_VIDEO)
        if not is_valid:
            logging.error(f"Default WWE video validation failed: {message}")
            return jsonify({'success': False, 'message': f"Default WWE video is invalid: {message}"}), 400

        # Generate a unique job ID
        job_id = str(uuid.uuid4())
        logging.info(f"Generated job ID: {job_id}")
        
        # Create job directory
        job_dir = os.path.join(UPLOAD_FOLDER, job_id)
        os.makedirs(job_dir, exist_ok=True)
        logging.info(f"Created job directory: {job_dir}")

        # Save uploaded fan video
        fan_path = os.path.join(job_dir, secure_filename(fan_video.filename))
        fan_video.save(fan_path)
        logging.info(f"Saved fan video to: {fan_path}")

        # Add job to queue
        job_data = {
            'id': job_id,
            'wwe_video': DEFAULT_WWE_VIDEO,
            'fan_video': fan_path,
            'status': 'processing',
            'stage': 'initializing',
            'progress': 0,
            'created_at': datetime.now().isoformat(),
            'original_wwe_filename': 'wwe_video.mp4',
            'original_fan_filename': fan_video.filename,
            'is_single_upload': True
        }
        
        processing_jobs[job_id] = job_data
        logging.info(f"Added job to processing queue: {job_id}")
        
        # Add to processing queue and start immediately
        output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}.mp4")
        job_queue.put((job_id, DEFAULT_WWE_VIDEO, fan_path, output_path))
        logging.info(f"Added job to queue with output path: {output_path}")

        # Start the job immediately
        Thread(target=process_video_job, args=(job_id, DEFAULT_WWE_VIDEO, fan_path, output_path)).start()
        logging.info(f"Started processing thread for job: {job_id}")

        return jsonify({
            'success': True,
            'message': 'Video uploaded successfully and processing started',
            'job_id': job_id
        })

    except Exception as e:
        logging.error(f"Error in stitch_single_video: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/stitch', methods=['POST'])
def stitch_videos():
    try:
        logging.info("Received custom video upload request")
        
        if 'wwe_video' not in request.files or 'fan_video' not in request.files:
            logging.error("Missing video files in request")
            return jsonify({'success': False, 'message': 'Both WWE and fan videos are required'}), 400

        wwe_video = request.files['wwe_video']
        fan_video = request.files['fan_video']

        if wwe_video.filename == '' or fan_video.filename == '':
            logging.error("Empty filenames")
            return jsonify({'success': False, 'message': 'No selected files'}), 400

        if not allowed_file(wwe_video.filename) or not allowed_file(fan_video.filename):
            logging.error(f"Invalid file types: {wwe_video.filename}, {fan_video.filename}")
            return jsonify({'success': False, 'message': 'Invalid file type. Only MP4, AVI, and MOV files are allowed.'}), 400

        # Check file sizes
        if not check_file_size(wwe_video) or not check_file_size(fan_video):
            logging.error("File size exceeds limit")
            return jsonify({'success': False, 'message': f'File size exceeds {MAX_FILE_SIZE_MB}MB limit'}), 400

        # Validate both videos
        for video, name in [(wwe_video, 'WWE'), (fan_video, 'Fan')]:
            is_valid, message = validate_uploaded_video(video)
            if not is_valid:
                logging.error(f"{name} video validation failed: {message}")
                return jsonify({'success': False, 'message': f"{name} video: {message}"}), 400

        # Generate a unique job ID
        job_id = str(uuid.uuid4())
        logging.info(f"Generated job ID: {job_id}")
        
        # Create job directory
        job_dir = os.path.join(UPLOAD_FOLDER, job_id)
        os.makedirs(job_dir, exist_ok=True)
        logging.info(f"Created job directory: {job_dir}")

        # Save uploaded files
        wwe_path = os.path.join(job_dir, secure_filename(wwe_video.filename))
        fan_path = os.path.join(job_dir, secure_filename(fan_video.filename))
        
        wwe_video.save(wwe_path)
        fan_video.save(fan_path)
        logging.info(f"Saved videos to: {wwe_path}, {fan_path}")

        # Add job to queue
        job_data = {
            'id': job_id,
            'wwe_video': wwe_path,
            'fan_video': fan_path,
            'status': 'processing',
            'stage': 'initializing',
            'progress': 0,
            'created_at': datetime.now().isoformat(),
            'original_wwe_filename': wwe_video.filename,
            'original_fan_filename': fan_video.filename,
            'is_single_upload': False
        }
        
        processing_jobs[job_id] = job_data
        logging.info(f"Added job to processing queue: {job_id}")
        
        # Add to processing queue and start immediately
        output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}.mp4")
        job_queue.put((job_id, wwe_path, fan_path, output_path))
        logging.info(f"Added job to queue with output path: {output_path}")

        # Start the job immediately
        Thread(target=process_video_job, args=(job_id, wwe_path, fan_path, output_path)).start()
        logging.info(f"Started processing thread for job: {job_id}")

        return jsonify({
            'success': True,
            'message': 'Videos uploaded successfully and processing started',
            'job_id': job_id
        })

    except Exception as e:
        logging.error(f"Error in stitch_videos: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

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

@app.route('/api/preview/<job_id>')
def preview_video(job_id):
    """Preview a video file"""
    try:
        job = processing_jobs.get(job_id)
        if not job:
            return jsonify({
                'success': False,
                'message': 'Job not found'
            }), 404

        if job['status'] != 'completed':
            return jsonify({
                'success': False,
                'message': 'Video not ready for preview'
            }), 400

        output_path = job.get('output_path')
        if not output_path or not os.path.exists(output_path):
            return jsonify({
                'success': False,
                'message': 'Video file not found'
            }), 404

        return send_file(
            output_path,
            mimetype='video/mp4',
            as_attachment=False
        )
    except Exception as e:
        logging.error(f"Error previewing video: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error previewing video: {str(e)}'
        }), 500

@app.route('/api/download/<job_id>', methods=['GET'])
def download_video(job_id):
    """Download a processed video"""
    try:
        output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}.mp4")
        
        if not os.path.exists(output_path):
            return jsonify({
                'success': False,
                'message': 'Video not found'
            }), 404
            
        # Validate the video file
        cap = cv2.VideoCapture(output_path)
        if not cap.isOpened():
            return jsonify({
                'success': False,
                'message': 'Invalid video file'
            }), 400
        cap.release()
        
        # Check file size
        file_size = os.path.getsize(output_path)
        if file_size < 10 * 1024 * 1024:  # Less than 10MB
            return jsonify({
                'success': False,
                'message': 'Video file is too small or invalid'
            }), 400
        
        return send_file(
            output_path,
            mimetype='video/mp4',
            as_attachment=True,
            download_name=f"stitched_video_{job_id}.mp4"
        )
    except Exception as e:
        logging.error(f"Error downloading video: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error downloading video: {str(e)}'
        }), 500

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

@app.route('/api/check_wwe_video', methods=['GET'])
def check_wwe_video():
    """Check if the default WWE video exists and is valid"""
    try:
        logging.info(f"Checking WWE video at: {DEFAULT_WWE_VIDEO}")
        
        if not os.path.exists(DEFAULT_WWE_VIDEO):
            logging.error(f"WWE video not found at: {DEFAULT_WWE_VIDEO}")
            return jsonify({
                'success': False,
                'message': 'Default WWE video not found',
                'exists': False,
                'path': DEFAULT_WWE_VIDEO
            })
        
        # Check if file is valid video
        if not allowed_file(DEFAULT_WWE_VIDEO):
            logging.error(f"WWE video is not a valid video file: {DEFAULT_WWE_VIDEO}")
            return jsonify({
                'success': False,
                'message': 'Default WWE video is not a valid video file',
                'exists': True,
                'valid': False,
                'path': DEFAULT_WWE_VIDEO
            })
        
        size_mb = os.path.getsize(DEFAULT_WWE_VIDEO) / (1024 * 1024)
        logging.info(f"WWE video check successful: {DEFAULT_WWE_VIDEO} ({size_mb:.2f} MB)")
        
        return jsonify({
            'success': True,
            'message': 'Default WWE video is available',
            'exists': True,
            'valid': True,
            'size': size_mb,
            'path': DEFAULT_WWE_VIDEO
        })
    except Exception as e:
        logging.error(f"Error checking WWE video: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error checking WWE video: {str(e)}',
            'path': DEFAULT_WWE_VIDEO
        }), 500

def validate_uploaded_video(file):
    """Validate an uploaded video file"""
    try:
        # Check file size
        file.seek(0, os.SEEK_END)
        size = file.tell() / (1024 * 1024)  # Convert to MB
        file.seek(0)
        
        logging.info(f"Validating uploaded file: size={size:.2f}MB")
        
        if size > MAX_FILE_SIZE_MB:
            return False, f"File size exceeds {MAX_FILE_SIZE_MB}MB limit"
        
        if size < 0.01:  # Less than 10KB
            return False, "File is too small"
        
        # Save to temporary file for validation
        temp_path = os.path.join(UPLOAD_FOLDER, f"temp_{uuid.uuid4()}.mp4")
        file.save(temp_path)
        logging.info(f"Saved temporary file: {temp_path}")
        
        # Validate using FFMPEG
        result = subprocess.run([
            'ffmpeg', '-v', 'error', '-i', temp_path,
            '-f', 'null', '-'
        ], capture_output=True, text=True)
        
        # Clean up temp file
        try:
            os.remove(temp_path)
            logging.info("Cleaned up temporary file")
        except Exception as e:
            logging.warning(f"Error cleaning up temp file: {str(e)}")
        
        if result.stderr:
            logging.error(f"FFMPEG validation error: {result.stderr}")
            return False, f"Invalid video file: {result.stderr}"
        
        logging.info("File validation successful")
        return True, "Video file is valid"
    except Exception as e:
        logging.error(f"Error validating video: {str(e)}")
        return False, f"Error validating video: {str(e)}"

def validate_video_file(file_path):
    """Validate a video file using FFMPEG"""
    try:
        # Check if file exists
        if not os.path.exists(file_path):
            return False, "File does not exist"
        
        # Check file size
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if size_mb < 0.01:  # Less than 10KB
            return False, f"File too small: {size_mb:.2f} MB"
        
        # Use FFMPEG to check video file
        result = subprocess.run([
            'ffmpeg', '-v', 'error', '-i', file_path,
            '-f', 'null', '-'
        ], capture_output=True, text=True)
        
        if result.stderr:
            return False, f"Invalid video file: {result.stderr}"
        
        return True, "Video file is valid"
    except Exception as e:
        return False, f"Error validating video: {str(e)}"

@app.route('/api/outputs', methods=['GET'])
def get_output_files():
    """Get list of all output files"""
    try:
        files = []
        for filename in os.listdir(OUTPUT_FOLDER):
            if filename.endswith('.mp4'):
                file_path = os.path.join(OUTPUT_FOLDER, filename)
                file_stat = os.stat(file_path)
                job_id = filename.rsplit('.', 1)[0]  # Remove .mp4 extension
                
                files.append({
                    'name': filename,
                    'job_id': job_id,
                    'size': file_stat.st_size,
                    'created_at': datetime.fromtimestamp(file_stat.st_ctime).isoformat(),
                    'modified_at': datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                })
        
        # Sort by creation date, newest first
        files.sort(key=lambda x: x['created_at'], reverse=True)
        
        return jsonify({
            'success': True,
            'files': files
        })
    except Exception as e:
        logging.error(f"Error getting output files: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error getting output files: {str(e)}'
        }), 500

@app.route('/api/output/<job_id>', methods=['DELETE'])
def delete_output_file(job_id):
    """Delete a specific output file"""
    try:
        file_path = os.path.join(OUTPUT_FOLDER, f"{job_id}.mp4")
        
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'message': 'File not found'
            }), 404
        
        os.remove(file_path)
        logging.info(f"Deleted output file: {file_path}")
        
        return jsonify({
            'success': True,
            'message': 'File deleted successfully'
        })
    except Exception as e:
        logging.error(f"Error deleting output file: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error deleting file: {str(e)}'
        }), 500

@app.route('/api/outputs/delete_all', methods=['POST'])
def delete_all_output_files():
    """Delete all output files"""
    try:
        deleted_count = 0
        for filename in os.listdir(OUTPUT_FOLDER):
            if filename.endswith('.mp4'):
                file_path = os.path.join(OUTPUT_FOLDER, filename)
                try:
                    os.remove(file_path)
                    deleted_count += 1
                    logging.info(f"Deleted output file: {file_path}")
                except Exception as e:
                    logging.error(f"Error deleting file {filename}: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': f'Deleted {deleted_count} output files'
        })
    except Exception as e:
        logging.error(f"Error deleting all output files: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error deleting files: {str(e)}'
        }), 500

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