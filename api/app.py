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
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import json

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

# Google Drive configuration
GOOGLE_DRIVE_CREDENTIALS = os.path.join(os.getenv('RENDER_TEMP_DIR', '/tmp'), 'google_drive_credentials.pkl')
GOOGLE_DRIVE_TOKEN = os.path.join(os.getenv('RENDER_TEMP_DIR', '/tmp'), 'google_drive_token.json')
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Load Google Drive credentials from environment
GOOGLE_DRIVE_CLIENT_CONFIG = {
    "web": {
        "client_id": os.environ.get('GOOGLE_DRIVE_CLIENT_ID'),
        "project_id": os.environ.get('GOOGLE_DRIVE_PROJECT_ID'),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": os.environ.get('GOOGLE_DRIVE_CLIENT_SECRET'),
        "redirect_uris": [os.environ.get('GOOGLE_DRIVE_REDIRECT_URI', 'http://localhost:5000/oauth2callback')]
    }
}

# Save client config to file
with open(GOOGLE_DRIVE_TOKEN, 'w') as f:
    json.dump(GOOGLE_DRIVE_CLIENT_CONFIG, f)

# Configure upload folder using Render's environment
UPLOAD_FOLDER = os.path.join(os.getenv('RENDER_TEMP_DIR', '/tmp'), 'uploads')
OUTPUT_FOLDER = os.path.join(os.getenv('RENDER_TEMP_DIR', '/tmp'), 'outputs')

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
            'status': job['status'],
            'progress': job['progress'],
            'stage': job['stage'],
            'message': job.get('message', ''),
            'error': job.get('error', '')
        })

    except Exception as e:
        logging.error(f"Error in get_job_status: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

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

        # Start processing in background
        def process_video():
            try:
                output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}.mp4")
                stitcher = VideoStitcher(job['wwe_video'], job['fan_video'])
                
                def progress_callback(progress, stage):
                    job['progress'] = progress
                    job['stage'] = stage
                
                success, message = stitcher.stitch_videos(output_path, progress_callback)
                
                if success:
                    job['status'] = 'completed'
                    job['progress'] = 100
                    job['stage'] = 'completed'
                    job['output_file'] = f"{job_id}.mp4"
                else:
                    raise Exception(message)
                
            except Exception as e:
                job['status'] = 'failed'
                job['error'] = str(e)
                job['stage'] = 'failed'
                job['progress'] = 0

        thread = Thread(target=process_video)
        thread.daemon = True
        thread.start()

        return jsonify({'success': True, 'message': 'Job started successfully'})

    except Exception as e:
        logging.error(f"Error in start_job: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/job/<job_id>/stop', methods=['POST'])
def stop_job(job_id):
    try:
        # Find the job
        job = next((j for j in active_jobs if j['id'] == job_id), None)
        if not job:
            return jsonify({'success': False, 'message': 'Job not found'})

        # Update job status
        job['status'] = 'stopped'
        job['stage'] = 'stopped'
        
        # Clean up job files
        cleanup_job_files(job_id)
        
        # Remove from active jobs
        active_jobs.remove(job)
        
        # Add to recent jobs if it was processing
        if job['status'] in ['processing', 'queued']:
            recent_jobs.append(job)
            if len(recent_jobs) > 10:  # Keep only last 10 jobs
                recent_jobs.pop(0)

        return jsonify({'success': True, 'message': 'Job stopped successfully'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

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
                        wwe_path = os.path.join(UPLOAD_FOLDER, job.get('wwe_filename'))
                        fan_path = os.path.join(UPLOAD_FOLDER, job.get('fan_filename'))
                        output_path = os.path.join(OUTPUT_FOLDER, job.get('output_file'))
                        
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

@app.route('/auth')
def auth():
    """Start Google Drive authentication"""
    service, auth_url = get_drive_service()
    if auth_url:
        return redirect(auth_url)
    return redirect(url_for('dashboard'))

@app.route('/oauth2callback')
def oauth2callback():
    """Handle Google Drive OAuth callback"""
    state = session['state']
    flow = Flow.from_client_config_file(GOOGLE_DRIVE_TOKEN, SCOPES, state=state)
    flow.redirect_uri = os.environ.get('GOOGLE_DRIVE_REDIRECT_URI', 'http://localhost:5000/oauth2callback')
    
    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    
    with open(GOOGLE_DRIVE_CREDENTIALS, 'wb') as token:
        pickle.dump(credentials, token)
    
    return redirect(url_for('dashboard'))

@app.route('/api/drive/upload/<job_id>', methods=['POST'])
def upload_to_drive(job_id):
    """Upload a file to Google Drive"""
    try:
        if job_id not in processing_jobs:
            return jsonify({'success': False, 'message': 'Job not found'}), 404

        job = processing_jobs[job_id]
        if job['status'] != 'completed':
            return jsonify({'success': False, 'message': 'Video is not ready for upload'}), 400

        output_filename = job.get('output_file')
        if not output_filename:
            return jsonify({'success': False, 'message': 'Output file not found'}), 404

        file_path = os.path.join(OUTPUT_FOLDER, output_filename)
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': 'Output file does not exist'}), 404

        service, auth_url = get_drive_service()
        if auth_url:
            return jsonify({'success': False, 'message': 'Google Drive not authenticated'}), 401

        file_metadata = {
            'name': f"stitched_video_{job_id}.mp4",
            'mimeType': 'video/mp4'
        }

        media = MediaFileUpload(
            file_path,
            mimetype='video/mp4',
            resumable=True
        )

        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()

        # Update job with Drive URL
        job['drive_url'] = file.get('webViewLink')
        
        return jsonify({
            'success': True,
            'message': 'File uploaded to Google Drive',
            'drive_url': file.get('webViewLink')
        })

    except Exception as e:
        logging.error(f"Error uploading to Drive: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/drive/status', methods=['GET'])
def get_drive_status():
    """Check if Google Drive is connected"""
    try:
        if os.path.exists(GOOGLE_DRIVE_CREDENTIALS):
            with open(GOOGLE_DRIVE_CREDENTIALS, 'rb') as token:
                creds = pickle.load(token)
                if creds and creds.valid:
                    return jsonify({
                        'success': True,
                        'connected': True
                    })
        return jsonify({
            'success': True,
            'connected': False
        })
    except Exception as e:
        logging.error(f"Error checking Drive status: {str(e)}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/api/folders', methods=['GET'])
def get_folders():
    """Get folder structure with Drive URLs"""
    try:
        uploads = []
        outputs = []
        
        # Get uploads
        for filename in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(file_path):
                stat = os.stat(file_path)
                uploads.append({
                    'name': filename,
                    'size': stat.st_size,
                    'modified': stat.st_mtime,
                    'path': file_path
                })
        
        # Get outputs with Drive URLs
        for filename in os.listdir(OUTPUT_FOLDER):
            file_path = os.path.join(OUTPUT_FOLDER, filename)
            if os.path.isfile(file_path):
                stat = os.stat(file_path)
                file_info = {
                    'name': filename,
                    'size': stat.st_size,
                    'modified': stat.st_mtime,
                    'path': file_path
                }
                
                # Extract job ID from filename (assuming format: job_id.mp4)
                job_id = os.path.splitext(filename)[0]
                file_info['job_id'] = job_id
                
                # Check if file has a Drive URL in any job
                for job in processing_jobs.values():
                    if job.get('output_file') == filename and job.get('drive_url'):
                        file_info['drive_url'] = job['drive_url']
                        break
                
                outputs.append(file_info)
        
        return jsonify({
            'success': True,
            'folders': {
                'uploads': {'files': uploads},
                'outputs': {'files': outputs}
            }
        })
    except Exception as e:
        logging.error(f"Error getting folders: {str(e)}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/api/files/<path:filename>', methods=['DELETE'])
def delete_file(filename):
    try:
        # Ensure the file path is within allowed directories
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        if not os.path.abspath(file_path).startswith(os.path.abspath(UPLOAD_FOLDER)):
            return jsonify({'success': False, 'message': 'Invalid file path'}), 400

        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({'success': True, 'message': 'File deleted successfully'})
        else:
            return jsonify({'success': False, 'message': 'File not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/files/<path:filename>', methods=['GET'])
def download_file(filename):
    try:
        # Ensure the file path is within allowed directories
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        if not os.path.abspath(file_path).startswith(os.path.abspath(UPLOAD_FOLDER)):
            return jsonify({'success': False, 'message': 'Invalid file path'}), 400

        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True)
        else:
            return jsonify({'success': False, 'message': 'File not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# Schedule cleanup task
def schedule_cleanup():
    while True:
        cleanup_old_files()
        time.sleep(3600)  # Run cleanup every hour

def get_drive_service():
    """Get or create Google Drive service"""
    creds = None
    if os.path.exists(GOOGLE_DRIVE_CREDENTIALS):
        with open(GOOGLE_DRIVE_CREDENTIALS, 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = Flow.from_client_config_file(GOOGLE_DRIVE_TOKEN, SCOPES)
            flow.redirect_uri = os.environ.get('GOOGLE_DRIVE_REDIRECT_URI', 'http://localhost:5000/oauth2callback')
            authorization_url, state = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true'
            )
            session['state'] = state
            return None, authorization_url
        
        with open(GOOGLE_DRIVE_CREDENTIALS, 'wb') as token:
            pickle.dump(creds, token)
    
    return build('drive', 'v3', credentials=creds), None

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