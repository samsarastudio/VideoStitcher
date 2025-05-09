# Video Stitcher Application

A Flask-based web application for stitching WWE and fan videos with specific timing and audio overlay.

## Features

- Video stitching with precise timing control
- Audio overlay support
- Real-time job status tracking
- Concurrent request handling with job queue
- Automatic file cleanup
- Web-based dashboard for monitoring

## Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

## Installation

### Windows

1. Clone or download this repository
2. Navigate to the `video_stitcher_app` directory
3. Double-click `setup.bat` or run it from the command prompt:
   ```bash
   setup.bat
   ```

### Linux/Mac

1. Clone or download this repository
2. Navigate to the `video_stitcher_app` directory
3. Make the setup script executable:
   ```bash
   chmod +x setup.sh
   ```
4. Run the setup script:
   ```bash
   ./setup.sh
   ```

The setup script will:
- Install all required Python packages
- Create necessary directories
- Set up the application structure

## Running the Application

### Local Development

#### Windows
```bash
python api/app.py
```

#### Linux/Mac
```bash
python3 api/app.py
```

The application will start on `http://localhost:5000`

### Production Deployment

The application is configured for deployment on Render.com. Use the following settings:

#### Build Command
```bash
pip install -r requirements.txt
```

#### Start Command
```bash
gunicorn wsgi:app
```

## API Endpoints

### POST /api/stitch
Submit a new video stitching job.

**Request:**
- `wwe_video`: WWE video file (mp4, avi, mov)
- `fan_video`: Fan video file (mp4, avi, mov)

**Response:**
```json
{
    "success": true,
    "message": "Video processing job queued successfully",
    "job_id": "uuid",
    "output_file": "output_filename.mp4"
}
```

### GET /api/job/<job_id>
Get the status of a specific job.

**Response:**
```json
{
    "success": true,
    "job_id": "uuid",
    "status": "queued|processing|completed|failed",
    "created_at": "timestamp",
    "output_file": "output_filename.mp4",
    "message": "success message",
    "error": "error message if failed"
}
```

### GET /api/download/<filename>
Download a processed video file.

### GET /api/status
Get current server status and queue information.

**Response:**
```json
{
    "success": true,
    "active_processes": 2,
    "max_concurrent_processes": 3,
    "queue_size": 1,
    "max_queue_size": 10,
    "server_status": "available|busy"
}
```

### GET /api/jobs
Get active and recent jobs.

**Response:**
```json
{
    "success": true,
    "active_jobs": [...],
    "recent_jobs": [...]
}
```

## System Limitations

- Maximum concurrent processes: 3
- Maximum queue size: 10 jobs
- Maximum file size: 500MB
- File retention: 24 hours
- Supported video formats: mp4, avi, mov

## Error Handling

The application includes comprehensive error handling for:
- Invalid file types
- File size limits
- Queue capacity
- Processing errors
- File system operations

## Logging

All operations are logged to `video_stitcher.log` in the application directory.

## Directory Structure

```
video_stitcher_app/
├── api/
│   ├── app.py
│   └── templates/
│       └── dashboard.html
├── core/
│   └── video_stitcher.py
├── static/
│   ├── uploads/
│   └── outputs/
├── requirements.txt
├── setup.py
├── setup.bat
├── setup.sh
└── wsgi.py
```

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

